"""Read-only, fail-closed recent telemetry diagnostics and journal worker.

The existing inverter_metrics.time is collection time, not device or receipt
time. This adapter therefore cannot promote those rows to learning evidence.
ADAPTIVE_OUTPUT_HISTORICAL_BATTERY_V changes only the labelled historical
reference used by hypotheses; it never supplies a current threshold.
"""
import asyncio
import hashlib
import json
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from app.control.sag_learning import SagLearner, SagSample

VERSION = 'online-exploratory-4'
WINDOW = timedelta(hours=3)
LOGGER = logging.getLogger('IMPORTANT')
LOCK_ID = 5276389241085182
PAIR_MAX_GAP = timedelta(minutes=60)
RESPONSE_CHANGE_MIN_V = 0.25
RESPONSE_BOUND_V = 2.0


def _configured_historical_baseline():
    """Load an illustrative historical reference, never current device settings."""
    value = os.environ.get('ADAPTIVE_OUTPUT_HISTORICAL_BATTERY_V')
    if value is None:
        return 26.0
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


async def validate_journal_schema(pool):
    """Refuse a journal whose columns or durable guards do not match migration 001."""
    async with pool.acquire() as conn:
        rows = await conn.fetch('''SELECT column_name, data_type, is_nullable FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = 'adaptive_decision_journal' ''')
        columns = {row['column_name']: (row['data_type'], row['is_nullable']) for row in rows}
        expected = {'evaluation_id': ('text', 'NO'), 'evaluated_at': ('timestamp with time zone', 'NO'),
                    'entry': ('jsonb', 'NO'), 'hardware_write_attempted': ('boolean', 'NO')}
        if any(columns.get(key) != value for key, value in expected.items()):
            raise RuntimeError('incompatible decision journal schema')
        defaults = await conn.fetch('''SELECT column_name, column_default FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = 'adaptive_decision_journal' ''')
        defaults = {row['column_name']: row['column_default'] for row in defaults}
        if any(defaults.get(key) is not None for key in ('evaluation_id', 'evaluated_at', 'entry')):
            raise RuntimeError('incompatible decision journal defaults')
        if defaults.get('hardware_write_attempted') not in ('false', 'false::boolean'):
            raise RuntimeError('incompatible decision journal default')
        unexpected_required = await conn.fetchval('''SELECT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name='adaptive_decision_journal'
              AND column_name NOT IN ('evaluation_id','evaluated_at','entry','hardware_write_attempted')
              AND is_nullable='NO' AND column_default IS NULL)''')
        if unexpected_required:
            raise RuntimeError('decision journal has an unsupported required column')
        primary = await conn.fetchval('''SELECT EXISTS (SELECT 1 FROM pg_constraint c
            JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname=current_schema() AND t.relname='adaptive_decision_journal'
            AND c.contype='p' AND pg_get_constraintdef(c.oid)='PRIMARY KEY (evaluation_id)')''')
        if not primary:
            raise RuntimeError('missing decision journal primary key')
        guard = await conn.fetchval('''SELECT EXISTS (SELECT 1 FROM pg_constraint c
            JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname=current_schema() AND t.relname='adaptive_decision_journal'
            AND c.contype='c' AND c.convalidated AND
            regexp_replace(lower(pg_get_constraintdef(c.oid)), '[" ()]', '', 'g')
                IN ('checkhardware_write_attempted=false', 'checkhardware_write_attempted=false::boolean'))''')
        if not guard:
            raise RuntimeError('missing hardware-write audit guard')
        index = await conn.fetchval('''SELECT EXISTS (
            SELECT 1 FROM pg_index i
            JOIN pg_class t ON t.oid=i.indrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace
            JOIN pg_class x ON x.oid=i.indexrelid
            JOIN pg_am am ON am.oid=x.relam
            WHERE n.nspname=current_schema() AND t.relname='adaptive_decision_journal'
              AND x.relname='adaptive_decision_journal_evaluated_at_idx'
              AND i.indisvalid AND i.indisready AND NOT i.indisunique
              AND am.amname='btree'
              AND i.indnatts=1
              AND right(pg_get_indexdef(i.indexrelid), length('(evaluated_at DESC)'))='(evaluated_at DESC)')''')
        if not index:
            raise RuntimeError('missing decision journal evaluation index')


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def parse_historical_record(fields, timezone_name, received_at, seen_ids):
    """Parse documented ID/time only; field mapping and source trust are separate.

    Refuse ambiguous/nonexistent local timestamps rather than guessing DST fold.
    """
    if not isinstance(fields, list) or len(fields) < 2 or not isinstance(fields[0], str) or not fields[0]:
        raise ValueError('invalid record')
    if fields[0] in seen_ids:
        raise ValueError('duplicate record')
    if not isinstance(received_at, datetime) or received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError('invalid receipt time')
    local = datetime.strptime(fields[1], '%Y-%m-%d %H:%M:%S')
    zone = ZoneInfo(timezone_name)
    candidates = set()
    for fold in (0, 1):
        candidate = local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if candidate.astimezone(zone).replace(tzinfo=None) == local:
            candidates.add(candidate)
    if len(candidates) != 1:
        raise ValueError('ambiguous or nonexistent local time')
    observed = candidates.pop()
    received = received_at.astimezone(timezone.utc)
    if observed > received:
        raise ValueError('future observation')
    if received - observed > WINDOW:
        raise ValueError('stale observation')
    seen_ids.add(fields[0])
    return {'record_id': fields[0], 'device_observed_at': observed, 'received_at': received,
            'time_provenance': 'DOCUMENTED_HISTORICAL_RECORD', 'field_mapping_verified': False}


def _observation_signature(row):
    mode = row.get('working_mode') if isinstance(row.get('working_mode'), str) else None
    numeric = tuple(_number(row.get(key)) for key in ('battery_voltage', 'battery_current_chg',
                                                        'battery_current_dis', 'pv_power_w',
                                                        'output_power_w', 'battery_soc'))
    return (mode, *numeric)


def _explore_collections(rows, changes, now):
    """Recency-weighted research contrast after de-duplication and matched operating conditions."""
    usable = [r for r in rows if r.get('working_mode') in ('Line Mode', 'Invert Mode')
              and all(_number(r.get(k)) is not None and r[k] >= 0 for k in
                      ('battery_voltage', 'battery_current_chg', 'battery_current_dis', 'pv_power_w', 'output_power_w'))]
    # A changed collection timestamp does not make a repeated cached payload new evidence.
    unique, seen = [], set()
    for r in usable:
        signature = _observation_signature(r)
        if signature not in seen:
            unique.append(r)
            seen.add(signature)
    usable = unique[-24:]
    if len(usable) < 6:
        return None

    line = [r for r in usable if r['working_mode'] == 'Line Mode' and r['battery_current_chg'] == 0]
    invert = [r for r in usable if r['working_mode'] == 'Invert Mode'
              and r['battery_current_dis'] > 0 and r['battery_current_chg'] == 0]
    typical_load = median(r['output_power_w'] for r in usable)
    typical_pv = median(r['pv_power_w'] for r in usable)
    load_tolerance = max(75.0, min(200.0, typical_load * 0.15))
    pv_tolerance = max(75.0, min(300.0, typical_pv * 0.20))

    candidates = []
    for line_row in line:
        for invert_row in invert:
            load_delta = abs(line_row['output_power_w'] - invert_row['output_power_w'])
            pv_delta = abs(line_row['pv_power_w'] - invert_row['pv_power_w'])
            elapsed = abs((line_row['time'] - invert_row['time']).total_seconds())
            if load_delta <= load_tolerance and pv_delta <= pv_tolerance and elapsed <= PAIR_MAX_GAP.total_seconds():
                distance = load_delta / load_tolerance + pv_delta / pv_tolerance + elapsed / PAIR_MAX_GAP.total_seconds()
                candidates.append((distance, line_row, invert_row, load_delta, pv_delta))
    candidates.sort(key=lambda item: (item[0], -max(item[1]['time'], item[2]['time']).timestamp()))
    used_line, used_invert, pairs = set(), set(), []
    for distance, line_row, invert_row, load_delta, pv_delta in candidates:
        if id(line_row) in used_line or id(invert_row) in used_invert:
            continue
        used_line.add(id(line_row)); used_invert.add(id(invert_row))
        at = max(line_row['time'], invert_row['time'])
        pairs.append({'response_v': line_row['battery_voltage'] - invert_row['battery_voltage'],
                      'time': at,
                      'load_w': (line_row['output_power_w'] + invert_row['output_power_w']) / 2,
                      'pv_w': (line_row['pv_power_w'] + invert_row['pv_power_w']) / 2,
                      'distance': distance, 'load_delta_w': load_delta, 'pv_delta_w': pv_delta,
                      'signatures': (_observation_signature(line_row), _observation_signature(invert_row))})

    weighted = [(r, 0.5 ** ((now - r['time']).total_seconds() / 3600)) for r in usable]
    def average(key):
        return sum(r[key] * w for r, w in weighted) / sum(w for _, w in weighted)
    recent = usable[-4:]
    charging = sum(r['battery_current_chg'] > 0 and r['battery_current_dis'] == 0 for r in recent)
    raw_response = weighted_response = uncertainty = None
    if len(pairs) >= 3:
        center = median(p['response_v'] for p in pairs)
        mad = median(abs(p['response_v'] - center) for p in pairs)
        outlier_limit = max(0.15, 3 * mad)
        retained = [p for p in pairs if abs(p['response_v'] - center) <= outlier_limit]
        if len(retained) >= 2:
            pair_weights = [0.5 ** ((now - p['time']).total_seconds() / 2700.0) for p in retained]
            raw_response = sum(p['response_v'] * w for p, w in zip(retained, pair_weights)) / sum(pair_weights)
            weighted_response = max(-RESPONSE_BOUND_V, min(RESPONSE_BOUND_V, raw_response))
            uncertainty = median(abs(p['response_v'] - median(q['response_v'] for q in retained)) for p in retained)
        else:
            retained = []
    else:
        retained = []
    confidence = min(0.45, len(usable) / 12) if usable else 0.0
    if retained:
        pair_fit = max(0.2, 1 - 0.3 * median(p['distance'] for p in retained) / 3)
        oldest_pair_age = max(0.0, (now - max(p['time'] for p in retained)).total_seconds())
        freshness = 0.5 ** (oldest_pair_age / 5400)
        confidence = min(1.0, len(retained) / 3) * pair_fit * freshness
        if raw_response is not None and abs(raw_response) > RESPONSE_BOUND_V:
            confidence *= 0.25
    latest_signature = _observation_signature(usable[-1])
    paired_signatures = {signature for pair in retained for signature in pair['signatures']}
    latest_charging = (usable[-1]['working_mode'] == 'Line Mode'
                       and usable[-1]['battery_current_chg'] > 0
                       and usable[-1]['battery_current_dis'] == 0)
    latest_discharging = (usable[-1]['working_mode'] == 'Invert Mode'
                          and usable[-1]['battery_current_dis'] > 0
                          and usable[-1]['battery_current_chg'] == 0)
    latest_raise_compatible = latest_signature in paired_signatures
    if latest_discharging and not latest_raise_compatible:
        confidence *= 0.5
    return {'contrast_v': round(weighted_response, 3) if weighted_response is not None else None,
            'unbounded_response_v': round(raw_response, 3) if raw_response is not None else None,
            'response_bounds_v': [-RESPONSE_BOUND_V, RESPONSE_BOUND_V],
            'response_uncertainty_v': round(uncertainty, 3) if uncertainty is not None else None,
            'comparable_load_tolerance_w': round(load_tolerance, 1),
            'comparable_pv_tolerance_w': round(pv_tolerance, 1),
            'comparable_pairs': len(retained), 'candidate_pairs': len(pairs),
            'confidence': round(max(0.0, min(1.0, confidence)), 3),
            'latest_raise_compatible': latest_raise_compatible,
            'latest_lower_compatible': latest_charging,
            'latest_observation_not_comparable': latest_discharging and not latest_raise_compatible,
            'samples': len(usable), 'distinct_signatures': len(usable), 'reported_mode_changes': changes,
            'effective_window_seconds': max(0, int((usable[-1]['time'] - usable[0]['time']).total_seconds())),
            'recent_charging_samples': charging,
            'weighted_voltage_v': round(average('battery_voltage'), 3),
            'weighted_load_w': round(average('output_power_w'), 1),
            'weighted_pv_w': round(average('pv_power_w'), 1),
            'voltage_range_v': round(max(r['battery_voltage'] for r in usable) - min(r['battery_voltage'] for r in usable), 3),
            'pv_range_w': [min(r['pv_power_w'] for r in usable), max(r['pv_power_w'] for r in usable)],
            'load_range_w': [min(r['output_power_w'] for r in usable), max(r['output_power_w'] for r in usable)]}


def _verified_sag_result(observations, learner, now):
    """Run SagLearner only on evidence with explicit observed-time and source provenance."""
    if not isinstance(observations, (list, tuple)) or not observations:
        return {'status': 'BLOCKED', 'evidence_level': 'EXPLORATORY',
                'reason_codes': ['VERIFIED_OBSERVATION_SOURCE_UNAVAILABLE'], 'observations': [],
                'estimate': None, 'provenance': []}
    if learner is None:
        return {'status': 'BLOCKED', 'evidence_level': 'EXPLORATORY',
                'reason_codes': ['SAG_LEARNER_CONFIGURATION_UNAVAILABLE'], 'observations': [],
                'estimate': None, 'provenance': []}
    if not isinstance(learner, SagLearner):
        return {'status': 'BLOCKED', 'evidence_level': 'EXPLORATORY',
                'reason_codes': ['SAG_LEARNER_CONFIGURATION_INVALID'], 'observations': [],
                'estimate': None, 'provenance': []}
    prepared = []
    for observation in observations:
        if not isinstance(observation, dict):
            return {'status': 'BLOCKED', 'evidence_level': 'EXPLORATORY',
                    'reason_codes': ['VERIFIED_OBSERVATION_PROVENANCE_UNAVAILABLE'], 'observations': [],
                    'estimate': None, 'provenance': []}
        sample = observation.get('sample')
        time_basis = observation.get('time_basis')
        source = observation.get('source_provenance')
        if time_basis not in ('DEVICE_OBSERVED_AT', 'SYNTHETIC_VERIFIED_DEVICE_TIME_FIXTURE'):
            reason = 'DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED'
            return {'status': 'BLOCKED', 'evidence_level': 'EXPLORATORY', 'reason_codes': [reason],
                    'observations': [], 'estimate': None, 'provenance': []}
        if type(sample) is not SagSample:
            return {'status': 'BLOCKED', 'evidence_level': 'EXPLORATORY',
                    'reason_codes': ['VERIFIED_SAMPLE_INVALID'], 'observations': [],
                    'estimate': None, 'provenance': []}
        if sample.source_verified is not True:
            return {'status': 'BLOCKED', 'evidence_level': 'EXPLORATORY',
                    'reason_codes': ['SAMPLE_SOURCE_NOT_VERIFIED'], 'observations': [],
                    'estimate': None, 'provenance': []}
        if not isinstance(source, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', source):
            return {'status': 'BLOCKED', 'evidence_level': 'EXPLORATORY',
                    'reason_codes': ['OBSERVATION_SOURCE_PROVENANCE_UNAVAILABLE'], 'observations': [],
                    'estimate': None, 'provenance': []}
        if any(not isinstance(getattr(sample, name), datetime)
               or getattr(sample, name).tzinfo is None or getattr(sample, name).utcoffset() is None
               for name in ('observed_at', 'received_at')):
            return {'status': 'BLOCKED', 'evidence_level': 'EXPLORATORY',
                    'reason_codes': ['DEVICE_OBSERVATION_TIME_INVALID'], 'observations': [],
                    'estimate': None, 'provenance': []}
        prepared.append((sample, time_basis, source))

    results = []
    provenance = []
    for sample, time_basis, source in prepared:
        results.append(learner.observe(sample, evaluated_at=now))
        item = {'time_basis': time_basis, 'source': source}
        if item not in provenance:
            provenance.append(item)
    estimate = learner.estimate(evaluated_at=now)
    return {'status': 'VERIFIED', 'evidence_level': 'VERIFIED',
            'reason_codes': [estimate.reason], 'observations': results, 'provenance': provenance,
            'estimate': {'observation_count': estimate.observation_count, 'sag_v': estimate.sag_v,
                         'uncertainty_v': estimate.uncertainty_v, 'confidence': estimate.confidence,
                         'load_range_w': estimate.load_range_w,
                         'temperature_range_c': estimate.temperature_range_c,
                         'soc_range_pct': estimate.soc_range_pct, 'stable_updates': estimate.stable_updates,
                         'usable': estimate.usable, 'reason': estimate.reason}}


def evaluate_collected_history(rows, now, previous=None, *, historical_battery_v=26.0,
                               verified_observations=None, learner=None):
    """Describe stored observations without treating collection time as device time."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('aware evaluation time required')
    now = now.astimezone(timezone.utc)
    valid = [dict(row) for row in rows if isinstance(row.get('time'), datetime)
             and row['time'].tzinfo is not None and row['time'].utcoffset() is not None
             and now - WINDOW <= row['time'] <= now]
    valid.sort(key=lambda row: row['time'])
    previous = previous if isinstance(previous, dict) and previous.get('algorithm_version') == VERSION else {}
    latest = valid[-1] if valid else {}
    reasons = ['DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED', 'RECEIPT_TIME_UNAVAILABLE',
               'BMS_LIMITS_UNKNOWN', 'CURRENT_THRESHOLD_SETTINGS_UNKNOWN']
    if not valid:
        reasons.append('NO_RECENT_COLLECTIONS')
    elif (now - latest['time']).total_seconds() > 300:
        reasons.append('LATEST_COLLECTION_STALE')
    for key in ('battery_voltage', 'battery_current_chg', 'battery_current_dis', 'pv_power_w', 'output_power_w'):
        if _number(latest.get(key)) is None:
            reasons.append('MISSING_' + key.upper())
    if latest.get('working_mode') not in ('Line Mode', 'Invert Mode'):
        reasons.append('WORKING_MODE_UNKNOWN')
    changes = 0
    previous_mode = None
    for row in valid:
        mode = row.get('working_mode')
        if mode not in ('Line Mode', 'Invert Mode'):
            previous_mode = None
        else:
            if previous_mode is not None and mode != previous_mode:
                changes += 1
            previous_mode = mode
    complete_latest = (latest.get('working_mode') in ('Line Mode', 'Invert Mode') and
        all(_number(latest.get(k)) is not None and latest[k] >= 0 for k in
            ('battery_voltage', 'battery_current_chg', 'battery_current_dis', 'pv_power_w', 'output_power_w')) and
        not (latest.get('battery_current_chg', 0) > 0 and latest.get('battery_current_dis', 0) > 0))
    exploratory = _explore_collections(valid, changes, now) if valid and complete_latest and (now - latest['time']).total_seconds() <= 300 else None
    if exploratory:
        reasons.extend(['COLLECTION_TIME_ONLY', 'HISTORICAL_BASELINE_ONLY', 'HYPOTHESIS_NOT_SAFETY_QUALIFIED'])
        if exploratory.get('latest_observation_not_comparable'):
            reasons.append('LATEST_OBSERVATION_NOT_COMPARABLE')
        earlier = previous.get('exploratory_voltage_contrast') if isinstance(previous, dict) else None
        old_response = _number(earlier.get('contrast_v')) if isinstance(earlier, dict) else None
        new_response = exploratory.get('contrast_v')
        old_uncertainty = _number(earlier.get('response_uncertainty_v')) if isinstance(earlier, dict) else None
        new_uncertainty = _number(exploratory.get('response_uncertainty_v'))
        change_limit = max(RESPONSE_CHANGE_MIN_V, 3 * max(old_uncertainty or 0.0, new_uncertainty or 0.0))
        changed = old_response is not None and new_response is not None and abs(new_response - old_response) >= change_limit
        exploratory['response_changed'] = changed
        if changed:
            exploratory['confidence'] = round(exploratory['confidence'] * 0.35, 3)
            reasons.append('EMPIRICAL_RESPONSE_CHANGED_CAUSE_UNKNOWN')
    else:
        changed = False
    collected = latest.get('time')
    # Collection time is usable only for idempotency and diagnostic display.
    key = collected.isoformat() if collected else now.replace(second=0, microsecond=0).isoformat()
    input_digest = hashlib.sha256(json.dumps([_observation_signature(r) for r in valid],
                                             separators=(',', ':'), allow_nan=False, default=str).encode()).hexdigest()
    state = previous.get('proposal_state') if isinstance(previous.get('proposal_state'), dict) else {}
    state = dict(state)
    baseline = _number(historical_battery_v)
    candidate = None
    if baseline is None:
        reasons.append('HISTORICAL_BASELINE_UNKNOWN')
    elif not (24 <= baseline <= 29 and baseline * 2 == round(baseline * 2)):
        reasons.append('HISTORICAL_BASELINE_OUT_OF_RANGE')
    if exploratory and baseline is not None and 24 <= baseline <= 29 and baseline * 2 == round(baseline * 2):
        contrast = exploratory['contrast_v']
        if (contrast is not None and contrast >= 0.2 and changes >= 3 and not changed
                and exploratory.get('confidence', 0) >= 0.5):
            candidate = ('HYPOTHETICAL_RAISE', baseline + 0.5)
        elif exploratory['recent_charging_samples'] >= 3 and not changed and exploratory.get('confidence', 0) >= 0.35:
            candidate = ('HYPOTHETICAL_LOWER', baseline - 0.5)
        # Historical utility is 25.5 V: preserve at least one catalogue step.
        if candidate and not (24 <= candidate[1] <= 29 and candidate[1] - 25.5 >= 0.5):
            candidate = None
            reasons.append('CATALOGUE_OR_HYSTERESIS_BLOCK')
    last_time = state.get('last_collection_time')
    collection_time = collected.isoformat() if collected else None
    signature = list(_observation_signature(latest)) if latest else None
    new_sample = bool(collection_time and collection_time != last_time and signature != state.get('last_sample_signature'))
    pending = (candidate[0] + ':' + str(candidate[1])) if candidate else None
    if new_sample:
        direction_compatible = (exploratory.get('latest_raise_compatible', False)
                                if candidate and candidate[0] == 'HYPOTHETICAL_RAISE'
                                else exploratory.get('latest_lower_compatible', False) if candidate else False)
        prior_response = _number(state.get('pending_response_v'))
        response_compatible = (not pending or prior_response is None or exploratory is None
                               or exploratory.get('contrast_v') is None
                               or abs(exploratory['contrast_v'] - prior_response) < RESPONSE_CHANGE_MIN_V)
        if pending and direction_compatible and response_compatible:
            state['stable_count'] = (min(2, state.get('stable_count', 0) + 1)
                                     if state.get('pending') == pending else 1)
            state['pending'] = pending
            state['pending_response_v'] = exploratory.get('contrast_v') if exploratory else None
        else:
            state['stable_count'] = 0
            state['pending'] = None
            state['pending_response_v'] = None
        state['last_collection_time'] = collection_time
        state['last_sample_signature'] = signature
    elif not candidate and state.get('pending'):
        # A contradictory evaluation cannot leave a stale proposal confirmed.
        state['stable_count'] = 0
        state['pending'] = None
        state['pending_response_v'] = None
    decision = 'HOLD' if exploratory else 'INSUFFICIENT_DATA'
    proposed = None
    stability = 'NO_CANDIDATE' if not candidate else 'AWAITING_CONFIRMATION'
    if candidate and state.get('stable_count', 0) == 0:
        stability = 'AWAITING_COMPATIBLE_OBSERVATION'
    if candidate and state.get('stable_count', 0) >= 2:
        last_change = state.get('last_change_at')
        try:
            last_change = datetime.fromisoformat(last_change) if last_change else None
        except (TypeError, ValueError):
            last_change = now
        if last_change and (now - last_change).total_seconds() < 3600 and state.get('active') != pending:
            stability = 'COOLDOWN'
            reasons.append('DRY_RUN_COOLDOWN')
        elif state.get('active') == pending:
            stability = 'UNCHANGED'
        else:
            decision, proposed, stability = candidate[0], candidate[1], 'CONFIRMED'
            state['active'], state['last_change_at'] = pending, now.isoformat()
    if candidate and stability in ('AWAITING_CONFIRMATION', 'AWAITING_COMPATIBLE_OBSERVATION'):
        reasons.append('PROPOSAL_AWAITING_COMPATIBLE_CONFIRMATION')
    cooldown_remaining = 0
    if state.get('last_change_at'):
        try:
            last_change_time = datetime.fromisoformat(state['last_change_at'])
            cooldown_remaining = max(0, int(3600 - (now - last_change_time).total_seconds()))
        except (TypeError, ValueError):
            cooldown_remaining = 0
    verified = _verified_sag_result(verified_observations, learner, now)
    verified_estimate = verified.get('estimate') if isinstance(verified, dict) else None
    if (verified.get('status') != 'VERIFIED' or not verified_estimate or not verified_estimate.get('usable')):
        for reason in verified.get('reason_codes', ['VERIFIED_LEARNING_BLOCKED']):
            if reason not in reasons:
                reasons.append(reason)
    collection_window = int((valid[-1]['time'] - valid[0]['time']).total_seconds()) if len(valid) > 1 else 0
    effective_window = exploratory.get('effective_window_seconds', 0) if exploratory else 0
    overall_confidence = exploratory.get('confidence', 0.0) if exploratory else 0.0
    explanation = ('Collection-time research only: mode/charging patterns are uncertain, timestamps may reflect cached readings, baseline is historical, and BMS limits and current settings are unverified. Candidate status: ' + stability if exploratory else 'Learning and threshold recommendations are blocked: device measurement time, receipt provenance, approved battery limits and current settings are not established.')
    if changed:
        explanation += ' Empirical response changed; cause is unknown and this is not proof that batteries were added or changed.'
    outcome = json.dumps({'baseline': baseline, 'decision': decision, 'proposed_voltage': proposed,
                          'stability': stability, 'reason_codes': reasons}, sort_keys=True,
                         separators=(',', ':'), allow_nan=False)
    ident = hashlib.sha256((VERSION + ':' + key + ':' + input_digest + ':' + outcome).encode()).hexdigest()
    return {
        'evaluation_id': ident, 'evaluated_at': now.isoformat(), 'algorithm_version': VERSION,
        'decision': decision, 'threshold': 'battery' if candidate else None, 'proposed_voltage': proposed,
        'current_settings': {'utility': None, 'battery': None, 'provenance': 'unknown'},
        'historical_settings': {'utility': 25.5, 'battery': baseline, 'provenance': 'historical or illustrative; not current'},
        'observation_time': None, 'observation_age_seconds': None, 'receipt_time': None,
        'collection_time': collected.isoformat() if collected else None,
        'mode': latest.get('working_mode') if latest.get('working_mode') in ('Line Mode', 'Invert Mode') else None,
        'pv_w': _number(latest.get('pv_power_w')),
        'load_w': _number(latest.get('output_power_w')), 'battery_v': _number(latest.get('battery_voltage')),
        'charge_a': _number(latest.get('battery_current_chg')), 'discharge_a': _number(latest.get('battery_current_dis')),
        'soc': _number(latest.get('battery_soc')), 'soc_provenance': 'reported; not verified SOC',
        'recent_reported_mode_changes': changes, 'transitions_provenance': 'collection order; not measurement time',
        'short_cycles': None, 'sag_estimate_v': None, 'rebound_estimate_v': None,
        'window_seconds': int(WINDOW.total_seconds()), 'effective_window_seconds': max(0, effective_window),
        'collection_window_seconds': max(0, collection_window),
        'collection_count': len(valid), 'unique_observation_count': exploratory.get('samples', 0) if exploratory else 0,
        'confidence': overall_confidence,
        'time_provenance': 'COLLECTION_TIME_ONLY', 'evidence_level': 'EXPLORATORY', 'exploratory_voltage_contrast': exploratory,
        'verified_sag': verified,
        'proposal_state': state, 'stability': stability, 'candidate_voltage': candidate[1] if candidate else None,
        'actionability': 'NON_ACTIONABLE', 'confirmation_progress': {'confirmed': state.get('stable_count', 0), 'required': 2},
        'cooldown_remaining_seconds': cooldown_remaining,
        'reason_codes': reasons, 'safety_blockers': reasons,
        'explanation': explanation,
        'hardware_write_attempted': False,
    }


async def run_evaluation(pool, now, *, verified_observations=None, learner=None, historical_battery_v=26.0):
    """One idempotent transaction shared by cooperating replicas."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval('SELECT pg_try_advisory_xact_lock($1)', LOCK_ID):
                return False
            rows = await conn.fetch('''SELECT time, working_mode, battery_voltage, battery_soc,
                battery_current_chg, battery_current_dis, pv_power_w, output_power_w
                FROM inverter_metrics WHERE time >= $1 AND time <= $2 ORDER BY time LIMIT 10000''', now - WINDOW, now)
            previous_json = await conn.fetchval('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1')
            previous = json.loads(previous_json) if previous_json else None
            entry = evaluate_collected_history(rows, now, previous,
                                               historical_battery_v=historical_battery_v,
                                               verified_observations=verified_observations, learner=learner)
            verified = entry['verified_sag']
            estimate = verified.get('estimate') if isinstance(verified, dict) else None
            blocking = None
            if verified.get('status') != 'VERIFIED':
                blocking = (verified.get('reason_codes') or ['VERIFIED_LEARNING_BLOCKED'])[0]
            elif not estimate or not estimate.get('usable'):
                blocking = estimate.get('reason') if estimate else 'SAG_ESTIMATE_UNAVAILABLE'
            entry['worker_health'] = {
                'enabled': True,
                'status': 'degraded' if blocking else 'healthy',
                'last_evaluation_at': now.isoformat(),
                'last_successful_evaluation_at': now.isoformat(),
                'last_error': None,
                'blocking_reason': blocking,
                'observation_count': entry['unique_observation_count'],
                'collection_count': entry['collection_count'],
                'effective_window_seconds': entry['effective_window_seconds'],
            }
            entry['evaluation_id'] = hashlib.sha256(
                f"{VERSION}:{now.isoformat()}:{entry['evaluation_id']}".encode()).hexdigest()
            await conn.execute('''INSERT INTO adaptive_decision_journal
                (evaluation_id,evaluated_at,entry) VALUES ($1,$2,$3::jsonb)
                ON CONFLICT (evaluation_id) DO NOTHING''', entry['evaluation_id'], now, json.dumps(entry, allow_nan=False))
            await conn.execute("DELETE FROM adaptive_decision_journal WHERE evaluated_at < $1", now - timedelta(days=30))
            return True


async def _record_worker_failure(pool, now, error_type):
    """Persist a failure only when the validated journal itself remains writable."""
    safe_error = error_type if isinstance(error_type, str) and re.fullmatch(r'[A-Za-z0-9_]{1,80}', error_type) else 'UnknownError'
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval('SELECT pg_try_advisory_xact_lock($1)', LOCK_ID):
                return False
            previous_json = await conn.fetchval('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1')
            previous = json.loads(previous_json) if previous_json else {}
            old_health = previous.get('worker_health') if isinstance(previous, dict) else {}
            last_success = old_health.get('last_successful_evaluation_at') if isinstance(old_health, dict) else None
            entry = {
                'evaluation_id': hashlib.sha256(f'{VERSION}:worker-error:{now.isoformat()}:{safe_error}'.encode()).hexdigest(),
                'evaluated_at': now.isoformat(), 'algorithm_version': VERSION, 'decision': 'BLOCKED',
                'evidence_level': 'EXPLORATORY', 'time_provenance': 'COLLECTION_TIME_ONLY',
                'confidence': 0.0, 'reason_codes': ['WORKER_EVALUATION_FAILED'],
                'explanation': 'The learning worker could not complete this evaluation.',
                'hardware_write_attempted': False,
                'worker_health': {'enabled': True, 'status': 'degraded', 'last_evaluation_at': now.isoformat(),
                    'last_successful_evaluation_at': last_success, 'last_error': safe_error,
                    'blocking_reason': 'WORKER_EVALUATION_FAILED', 'observation_count': None, 'collection_count': None,
                    'effective_window_seconds': None},
            }
            await conn.execute('''INSERT INTO adaptive_decision_journal
                (evaluation_id,evaluated_at,entry) VALUES ($1,$2,$3::jsonb)
                ON CONFLICT (evaluation_id) DO NOTHING''', entry['evaluation_id'], now, json.dumps(entry, allow_nan=False))
            return True


async def learning_loop(database_url, stop_event):
    """Explicit opt-in only; failures never reach inverter monitoring."""
    pool = None
    try:
        import asyncpg
        LOGGER.warning('[OUTPUT_LEARNING] collection-time exploration only; verified learning is blocked')
        while not stop_event.is_set():
            schema_validated = False
            try:
                if pool is None:
                    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2, command_timeout=10)
                await validate_journal_schema(pool)
                schema_validated = True
                await run_evaluation(pool, datetime.now(timezone.utc),
                                     historical_battery_v=_configured_historical_baseline())
            except Exception as exc:
                if pool is not None and schema_validated:
                    try:
                        await _record_worker_failure(pool, datetime.now(timezone.utc), type(exc).__name__)
                    except Exception:
                        pass
                LOGGER.warning('[OUTPUT_LEARNING] diagnostic journal unavailable (%s)', type(exc).__name__)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass
    finally:
        if pool is not None:
            await pool.close()
