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

VERSION = 'online-exploratory-5'
# Proposal confirmation/cooldown state written by these versions is carried forward
# so an upgrade cannot silently reset a durable dry-run cooldown.
STATE_COMPATIBLE_VERSIONS = frozenset(('online-exploratory-4', VERSION))
WINDOW = timedelta(hours=3)
LOGGER = logging.getLogger('IMPORTANT')
LOCK_ID = 5276389241085182
PAIR_MAX_GAP = timedelta(minutes=60)
RESPONSE_CHANGE_MIN_V = 0.25
RESPONSE_BOUND_V = 2.0
MIN_CANDIDATE_PAIRS = 3
# Bounded analysis memory: three hours at a 15 s cadence. The previous fixed cap of
# 24 unique observations (~35 min at the production cadence) discarded most of the
# window, including earlier Line-not-charging readings and mode transitions.
MAX_ANALYSIS_OBSERVATIONS = 720
SHORT_RUN_MAX = timedelta(minutes=20)
CYCLING_MIN_TRANSITIONS = 4
CYCLING_MIN_SHORT_RUNS = 2
COLLECTION_GAP_LIMIT = timedelta(minutes=10)
MODES = ('Line Mode', 'Invert Mode')
NUMERIC_KEYS = ('battery_voltage', 'battery_current_chg', 'battery_current_dis', 'pv_power_w', 'output_power_w')
EVIDENCE_CLASSES = ('INVERT_DISCHARGING', 'LINE_CHARGING', 'LINE_NOT_CHARGING', 'LINE_DISCHARGING',
                    'INVERT_CHARGING', 'INVERT_IDLE', 'CONFLICTING_CURRENT', 'UNKNOWN_CURRENT')
# Why each class cannot enter the primary (sag-proxy) voltage contrast.
PRIMARY_EXCLUSION = {
    'LINE_CHARGING': 'LINE_MODE_CHARGING_CONFOUNDED',
    'LINE_DISCHARGING': 'LINE_MODE_DISCHARGING_CONFOUNDED',
    'INVERT_CHARGING': 'INVERT_MODE_NOT_DISCHARGING',
    'INVERT_IDLE': 'INVERT_MODE_NOT_DISCHARGING',
    'CONFLICTING_CURRENT': 'CONFLICTING_CURRENT_STATE',
    'UNKNOWN_CURRENT': 'CURRENT_STATE_UNKNOWN',
}


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


def _evidence_class(row):
    """Classify one collection by reported mode and battery current direction."""
    mode = row.get('working_mode')
    charge, discharge = _number(row.get('battery_current_chg')), _number(row.get('battery_current_dis'))
    if mode not in MODES or charge is None or discharge is None or charge < 0 or discharge < 0:
        return 'UNKNOWN_CURRENT'
    if charge > 0 and discharge > 0:
        return 'CONFLICTING_CURRENT'
    if mode == 'Line Mode':
        return 'LINE_CHARGING' if charge > 0 else 'LINE_DISCHARGING' if discharge > 0 else 'LINE_NOT_CHARGING'
    return 'INVERT_DISCHARGING' if discharge > 0 else 'INVERT_CHARGING' if charge > 0 else 'INVERT_IDLE'


def _prepare_observations(valid):
    """Stage-by-stage filtering with an explicit count for every rejected collection."""
    rejected = {'unknown_mode': 0, 'missing_or_invalid_numeric': 0}
    missing = {}
    complete = []
    for r in valid:
        if r.get('working_mode') not in MODES:
            rejected['unknown_mode'] += 1
            continue
        bad = [k for k in NUMERIC_KEYS if _number(r.get(k)) is None or r[k] < 0]
        if bad:
            rejected['missing_or_invalid_numeric'] += 1
            for key in bad:
                missing[key] = missing.get(key, 0) + 1
            continue
        complete.append(r)
    # Exact signatures are excluded from quantitative analysis across the window so
    # replayed/cached values cannot manufacture independent voltage evidence.
    unique, seen = [], set()
    for r in complete:
        signature = _observation_signature(r)
        if signature not in seen:
            unique.append(r)
            seen.add(signature)
    analysed = unique[-MAX_ANALYSIS_OBSERVATIONS:]
    funnel = {'in_window_collections': len(valid),
              'rejected_unknown_mode': rejected['unknown_mode'],
              'rejected_missing_or_invalid_numeric': rejected['missing_or_invalid_numeric'],
              'missing_fields': dict(sorted(missing.items())),
              'valid_mode_and_numeric': len(complete),
              'rejected_duplicate_cached_payload': len(complete) - len(unique),
              'unique_observations': len(unique),
              'rejected_analysis_cap': len(unique) - len(analysed),
              'analysis_cap': MAX_ANALYSIS_OBSERVATIONS,
              'analysed_observations': len(analysed)}
    return analysed, funnel


def _range(values, digits):
    return [round(min(values), digits), round(max(values), digits)] if values else None


def _class_summary(analysed):
    """Voltage, load and PV context per operating state; descriptive only."""
    summary = {}
    for name in EVIDENCE_CLASSES:
        members = [r for r in analysed if _evidence_class(r) == name]
        if not members:
            continue
        volts = [r['battery_voltage'] for r in members]
        summary[name] = {'count': len(members), 'voltage_range_v': _range(volts, 2),
                         'median_voltage_v': round(median(volts), 3),
                         'load_range_w': _range([r['output_power_w'] for r in members], 0),
                         'pv_range_w': _range([r['pv_power_w'] for r in members], 0),
                         'median_charge_a': round(median(r['battery_current_chg'] for r in members), 1),
                         'median_discharge_a': round(median(r['battery_current_dis'] for r in members), 1)}
    return summary


def _reported_cycling(valid, now):
    """Detect reported Line/Invert alternation independently of voltage-pair eligibility.

    Uses every in-window collection with a known mode (numeric fields may be missing),
    after dropping repeated cached payloads. Each transition is bracketed by the last
    collection before and the first after the reported change; collection time is not
    device observation time and cached payloads may lag, so physical switch times are unknown.
    """
    sequence, groups, duplicates = [], [], 0
    group = []
    last_signature = None
    for r in valid:
        if r.get('working_mode') not in MODES:
            if group:
                groups.append(group)
                group = []
            last_signature = None
            continue
        signature = _observation_signature(r)
        # Do not erase a legitimate return to a previous Line/Invert payload after
        # an intervening observation; cached repeats are consecutive in collection order.
        if signature == last_signature:
            duplicates += 1
            continue
        sequence.append(r)
        group.append(r)
        last_signature = signature
    if group:
        groups.append(group)
    transitions = []
    complete_runs = []
    for group in groups:
        runs = []
        for r in group:
            if runs and runs[-1][0] == r['working_mode']:
                runs[-1][1].append(r)
            else:
                runs.append((r['working_mode'], [r]))
        for (mode_before, before), (mode_after, after) in zip(runs, runs[1:]):
            last, first = before[-1], after[0]
            transitions.append({
                'direction': 'INVERT_TO_LINE' if mode_after == 'Line Mode' else 'LINE_TO_INVERT',
                'bracket_start': last['time'].isoformat(), 'bracket_end': first['time'].isoformat(),
                'bracket_seconds': int((first['time'] - last['time']).total_seconds()),
                'state_before': _evidence_class(last), 'state_after': _evidence_class(first),
                'battery_v_before': _number(last.get('battery_voltage')),
                'battery_v_after': _number(first.get('battery_voltage')),
                'load_w': _number(first.get('output_power_w')), 'pv_w': _number(first.get('pv_power_w'))})
        for index in range(1, len(runs) - 1):
            members = runs[index][1]
            lower = (members[-1]['time'] - members[0]['time']).total_seconds()
            upper = (runs[index + 1][1][0]['time'] - runs[index - 1][1][-1]['time']).total_seconds()
            complete_runs.append((runs[index][0], lower, upper))
    short = [run for run in complete_runs if run[2] <= SHORT_RUN_MAX.total_seconds()]
    gaps = [(b['time'] - a['time']).total_seconds() for a, b in zip(sequence, sequence[1:])]
    span = (sequence[-1]['time'] - sequence[0]['time']).total_seconds() if len(sequence) > 1 else 0
    covered = sum((b['time'] - a['time']).total_seconds() for group in groups
                  for a, b in zip(group, group[1:])
                  if (b['time'] - a['time']).total_seconds() <= COLLECTION_GAP_LIMIT.total_seconds())
    coverage = covered / span if span else 0.0
    detected = len(transitions) >= CYCLING_MIN_TRANSITIONS and len(short) >= CYCLING_MIN_SHORT_RUNS
    confidence = 0.0
    if transitions:
        duplicate_share = duplicates / max(1, duplicates + len(sequence))
        confidence = (min(1.0, len(transitions) / 8) * min(1.0, len(short) / 4)
                      * max(0.0, coverage) * (1 - duplicate_share / 2))
    states_at = {}
    for item in transitions:
        key = item['direction'] + ':' + item['state_after']
        states_at[key] = states_at.get(key, 0) + 1

    def bounds(items, mode):
        selected = [run for run in items if run[0] == mode]
        if not selected:
            return None
        return {'complete_runs': len(selected),
                'median_dwell_lower_bound_s': int(median(run[1] for run in selected)),
                'median_dwell_upper_bound_s': int(median(run[2] for run in selected))}
    return {
        'detected': detected,
        'detection_confidence': round(max(0.0, min(1.0, confidence)), 3),
        'confidence_interpretation': 'HEURISTIC_NOT_CALIBRATED_PROBABILITY',
        'criteria': {'min_transitions': CYCLING_MIN_TRANSITIONS, 'min_short_runs': CYCLING_MIN_SHORT_RUNS,
                     'short_run_max_seconds': int(SHORT_RUN_MAX.total_seconds())},
        'reported_mode_changes': len(transitions),
        'complete_runs': len(complete_runs), 'short_runs': len(short),
        'line_runs': bounds(complete_runs, 'Line Mode'), 'invert_runs': bounds(complete_runs, 'Invert Mode'),
        'shortest_run_upper_bound_s': int(min(run[2] for run in complete_runs)) if complete_runs else None,
        'transition_bracket_median_s': int(median(t['bracket_seconds'] for t in transitions)) if transitions else None,
        'transition_states': dict(sorted(states_at.items())),
        'recent_transitions': transitions[-3:],
        'collections_considered': len(sequence), 'duplicate_payloads_ignored': duplicates,
        'max_collection_gap_s': int(max(gaps)) if gaps else None,
        'collection_coverage': round(max(0.0, coverage), 3),
        'time_provenance': 'COLLECTION_ORDER_ONLY',
        'uncertainty': 'SWITCH_TIME_BETWEEN_BRACKETING_COLLECTIONS_CACHE_LAG_UNKNOWN',
    }


def _pair_contrast(line, invert, tolerances, now):
    """Match Line/Invert observations under comparable load, PV and time; count every rejection."""
    load_tolerance, pv_tolerance = tolerances
    rejected = {'TIME_GAP_EXCEEDED': 0, 'LOAD_NOT_COMPARABLE': 0, 'PV_NOT_COMPARABLE': 0,
                'NOT_SELECTED_ONE_TO_ONE': 0, 'OUTLIER_REJECTED': 0, 'INSUFFICIENT_PAIRS': 0}
    candidates = []
    line_with_comparable_candidate = set()
    for line_row in line:
        for invert_row in invert:
            load_delta = abs(line_row['output_power_w'] - invert_row['output_power_w'])
            pv_delta = abs(line_row['pv_power_w'] - invert_row['pv_power_w'])
            elapsed = abs((line_row['time'] - invert_row['time']).total_seconds())
            if elapsed > PAIR_MAX_GAP.total_seconds():
                rejected['TIME_GAP_EXCEEDED'] += 1
            elif load_delta > load_tolerance:
                rejected['LOAD_NOT_COMPARABLE'] += 1
            elif pv_delta > pv_tolerance:
                rejected['PV_NOT_COMPARABLE'] += 1
            else:
                distance = load_delta / load_tolerance + pv_delta / pv_tolerance + elapsed / PAIR_MAX_GAP.total_seconds()
                candidates.append((distance, line_row, invert_row, load_delta, pv_delta))
                line_with_comparable_candidate.add(id(line_row))
    candidates.sort(key=lambda item: (item[0], -max(item[1]['time'], item[2]['time']).timestamp()))
    used_line, used_invert, pairs = set(), set(), []
    for distance, line_row, invert_row, load_delta, pv_delta in candidates:
        if id(line_row) in used_line or id(invert_row) in used_invert:
            rejected['NOT_SELECTED_ONE_TO_ONE'] += 1
            continue
        used_line.add(id(line_row)); used_invert.add(id(invert_row))
        pairs.append({'response_v': line_row['battery_voltage'] - invert_row['battery_voltage'],
                      'time': max(line_row['time'], invert_row['time']),
                      'distance': distance, 'load_delta_w': load_delta, 'pv_delta_w': pv_delta,
                      'signatures': (_observation_signature(line_row), _observation_signature(invert_row))})
    line_without_partner = sum(1 for r in line if id(r) not in line_with_comparable_candidate)
    line_not_selected = sum(1 for r in line if id(r) in line_with_comparable_candidate and id(r) not in used_line)
    raw = uncertainty = None
    retained = []
    if len(pairs) >= MIN_CANDIDATE_PAIRS:
        center = median(p['response_v'] for p in pairs)
        mad = median(abs(p['response_v'] - center) for p in pairs)
        outlier_limit = max(0.15, 3 * mad)
        retained = [p for p in pairs if abs(p['response_v'] - center) <= outlier_limit]
        rejected['OUTLIER_REJECTED'] = len(pairs) - len(retained)
        if len(retained) >= 2:
            weights = [0.5 ** ((now - p['time']).total_seconds() / 2700.0) for p in retained]
            raw = sum(p['response_v'] * w for p, w in zip(retained, weights)) / sum(weights)
            uncertainty = median(abs(p['response_v'] - median(q['response_v'] for q in retained)) for p in retained)
        else:
            rejected['INSUFFICIENT_PAIRS'] = len(retained)
            retained = []
    else:
        rejected['INSUFFICIENT_PAIRS'] = len(pairs)
    confidence = None
    if retained:
        pair_fit = max(0.2, 1 - 0.3 * median(p['distance'] for p in retained) / 3)
        newest_pair_age = max(0.0, (now - max(p['time'] for p in retained)).total_seconds())
        confidence = min(1.0, len(retained) / 3) * pair_fit * 0.5 ** (newest_pair_age / 5400)
        if abs(raw) > RESPONSE_BOUND_V:
            confidence *= 0.25
    return {'pairs': pairs, 'retained': retained, 'raw': raw, 'uncertainty': uncertainty,
            'confidence': confidence, 'combinations': len(line) * len(invert),
            'line_observations': len(line), 'invert_observations': len(invert),
            'line_without_comparable_partner': line_without_partner,
            'line_not_selected_one_to_one': line_not_selected, 'rejected': rejected}


def _explore_collections(usable, changes, now):
    """Recency-weighted research contrast after de-duplication and matched operating conditions.

    Only Line-not-charging vs Invert-discharging pairs form the primary contrast that may
    support a hypothesis. Line-charging pairs are reported separately as a
    charging-confounded description and never support sag, rebound or a proposal.
    """
    if len(usable) < 6:
        return None
    classes = {id(r): _evidence_class(r) for r in usable}
    line = [r for r in usable if classes[id(r)] == 'LINE_NOT_CHARGING']
    line_charging = [r for r in usable if classes[id(r)] == 'LINE_CHARGING']
    invert = [r for r in usable if classes[id(r)] == 'INVERT_DISCHARGING']
    typical_load = median(r['output_power_w'] for r in usable)
    typical_pv = median(r['pv_power_w'] for r in usable)
    load_tolerance = max(75.0, min(200.0, typical_load * 0.15))
    pv_tolerance = max(75.0, min(300.0, typical_pv * 0.20))
    primary = _pair_contrast(line, invert, (load_tolerance, pv_tolerance), now)
    confounded = _pair_contrast(line_charging, invert, (load_tolerance, pv_tolerance), now)
    retained, raw_response = primary['retained'], primary['raw']
    weighted_response = max(-RESPONSE_BOUND_V, min(RESPONSE_BOUND_V, raw_response)) if raw_response is not None else None

    weighted = [(r, 0.5 ** ((now - r['time']).total_seconds() / 3600)) for r in usable]
    def average(key):
        return sum(r[key] * w for r, w in weighted) / sum(w for _, w in weighted)
    recent = usable[-4:]
    charging = sum(r['battery_current_chg'] > 0 and r['battery_current_dis'] == 0 for r in recent)
    # Observation sufficiency says how much data exists; it is not voltage-estimate confidence.
    sufficiency = min(0.45, len(usable) / 12)
    contrast_confidence = primary['confidence']
    latest_signature = _observation_signature(usable[-1])
    paired_signatures = {signature for pair in retained for signature in pair['signatures']}
    latest_class = classes[id(usable[-1])]
    latest_charging = latest_class == 'LINE_CHARGING'
    latest_discharging = latest_class == 'INVERT_DISCHARGING'
    latest_raise_compatible = latest_signature in paired_signatures
    # Gate used by the unchanged hypothesis rules (identical to online-exploratory-4).
    gate = contrast_confidence if contrast_confidence is not None else sufficiency
    if latest_discharging and not latest_raise_compatible:
        gate *= 0.5
        if contrast_confidence is not None:
            contrast_confidence *= 0.5
    exclusions = {}
    for r in usable:
        reason = PRIMARY_EXCLUSION.get(classes[id(r)])
        if reason:
            exclusions[reason] = exclusions.get(reason, 0) + 1
    if primary['line_without_comparable_partner']:
        exclusions['LINE_NOT_CHARGING_WITHOUT_COMPARABLE_PARTNER'] = primary['line_without_comparable_partner']
    if primary['line_not_selected_one_to_one']:
        exclusions['LINE_NOT_CHARGING_NOT_SELECTED_ONE_TO_ONE'] = primary['line_not_selected_one_to_one']
    confounded_values = [p['response_v'] for p in confounded['pairs']]
    return {'contrast_v': round(weighted_response, 3) if weighted_response is not None else None,
            'contrast_basis': 'LINE_NOT_CHARGING_MINUS_INVERT_DISCHARGING',
            'unbounded_response_v': round(raw_response, 3) if raw_response is not None else None,
            'response_bounds_v': [-RESPONSE_BOUND_V, RESPONSE_BOUND_V],
            'response_uncertainty_v': round(primary['uncertainty'], 3) if primary['uncertainty'] is not None else None,
            'comparable_load_tolerance_w': round(load_tolerance, 1),
            'comparable_pv_tolerance_w': round(pv_tolerance, 1),
            'comparable_pairs': len(retained), 'candidate_pairs': len(primary['pairs']),
            'min_candidate_pairs': MIN_CANDIDATE_PAIRS,
            'confidence': round(max(0.0, min(1.0, contrast_confidence)), 3) if contrast_confidence is not None else None,
            'confidence_basis': 'VOLTAGE_CONTRAST' if contrast_confidence is not None else 'NO_VOLTAGE_ESTIMATE',
            'observation_sufficiency': round(sufficiency, 3),
            'hypothesis_gate_confidence': round(max(0.0, min(1.0, gate)), 3),
            'pair_rejections': primary['rejected'],
            'primary_eligible': {'line_not_charging': primary['line_observations'],
                                 'invert_discharging': primary['invert_observations'],
                                 'combinations': primary['combinations']},
            'primary_exclusions': dict(sorted(exclusions.items())),
            'charging_confounded_contrast': {
                'label': 'CHARGING_CONFOUNDED_DESCRIPTIVE',
                'basis': 'LINE_CHARGING_MINUS_INVERT_DISCHARGING',
                'interpretation': 'CHARGE_CURRENT_RAISES_LINE_VOLTAGE_NOT_SAG_OR_REBOUND',
                'eligible_for_hypothesis': False,
                'line_charging_observations': confounded['line_observations'],
                'matched_pairs': len(confounded['pairs']),
                'median_difference_v': round(median(confounded_values), 3) if confounded_values else None,
                'difference_range_v': _range(confounded_values, 3),
                'median_line_charge_a': round(median(r['battery_current_chg'] for r in line_charging), 1) if line_charging else None,
                'pair_rejections': {k: v for k, v in confounded['rejected'].items()
                                    if k not in ('OUTLIER_REJECTED', 'INSUFFICIENT_PAIRS')}},
            'latest_state': latest_class,
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


def _decision_diagnostics(cycling, exploratory, evidence_classes, funnel, candidate, blocked_candidate,
                          changed, changes, decision):
    """Operator-readable account of why a hypothesis is or is not supported."""
    counts = {name: item['count'] for name, item in evidence_classes.items()}
    line_charging = counts.get('LINE_CHARGING', 0)
    line_quiet = counts.get('LINE_NOT_CHARGING', 0)
    discharging = counts.get('INVERT_DISCHARGING', 0)
    blocked_by = []
    if exploratory is None:
        if funnel['analysed_observations'] < 6:
            blocked_by.append('FEWER_THAN_6_UNIQUE_COMPLETE_OBSERVATIONS')
        else:
            blocked_by.append('LATEST_COLLECTION_INCOMPLETE_STALE_OR_CONFLICTING')
    elif exploratory['contrast_v'] is None:
        if line_charging and line_quiet < MIN_CANDIDATE_PAIRS:
            blocked_by.append('LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE')
        if line_quiet < MIN_CANDIDATE_PAIRS:
            blocked_by.append('TOO_FEW_LINE_NOT_CHARGING_OBSERVATIONS')
        if discharging < MIN_CANDIDATE_PAIRS:
            blocked_by.append('TOO_FEW_INVERT_DISCHARGING_OBSERVATIONS')
        if exploratory['candidate_pairs'] >= MIN_CANDIDATE_PAIRS:
            blocked_by.append('PAIRS_DISAGREE_OUTLIERS_REJECTED')
        blocked_by.append('INSUFFICIENT_COMPARABLE_PAIRS')
    confounded = exploratory.get('charging_confounded_contrast') if exploratory else None
    raise_checks = None
    unsupported = []
    if exploratory:
        contrast = exploratory['contrast_v']
        raise_checks = {
            'contrast_at_least_0_2_v': contrast is not None and contrast >= 0.2,
            'reported_mode_changes_at_least_3': changes >= 3,
            'voltage_contrast_confidence_at_least_0_5': (exploratory.get('confidence') or 0) >= 0.5,
            'response_stable': not changed}
        if not candidate:
            if contrast is None:
                unsupported.append('NO_VOLTAGE_CONTRAST_ESTIMATE')
            unsupported.extend('RAISE_REQUIRES_' + name.upper() for name, ok in raise_checks.items()
                               if not ok and not (name.startswith('contrast') and contrast is None))
    else:
        unsupported.append('EXPLORATORY_ANALYSIS_UNAVAILABLE')
    if blocked_candidate:
        unsupported.append('CANDIDATE_' + blocked_candidate['decision'] + '_' + blocked_candidate['reason'])
    holding = decision in ('HOLD', 'INSUFFICIENT_DATA')
    hold_reasons = []
    if holding:
        if cycling['detected']:
            hold_reasons.append('CYCLING_DETECTED')
        if 'LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE' in blocked_by:
            hold_reasons.append('LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE')
        if exploratory is not None and exploratory['contrast_v'] is None:
            hold_reasons.append('INSUFFICIENT_COMPARABLE_PAIRS')
        if not candidate:
            hold_reasons.append('INSUFFICIENT_HYPOTHESIS_EVIDENCE')

    parts = []
    if cycling['detected']:
        parts.append('Frequent reported Line/Invert cycling detected: %d mode changes, %d of %d bounded runs '
                     'shorter than %d min (collection-time brackets; physical switch times unknown; '
                     'detection confidence %.2f).' % (cycling['reported_mode_changes'], cycling['short_runs'],
                                                       cycling['complete_runs'], SHORT_RUN_MAX.total_seconds() // 60,
                                                       cycling['detection_confidence']))
    else:
        parts.append('Frequent cycling not established: %d reported mode changes, %d short runs.'
                     % (cycling['reported_mode_changes'], cycling['short_runs']))
    parts.append('Evidence: %d Invert discharging, %d Line charging, %d Line not charging, %d other/unknown '
                 'of %d analysed unique observations (%d duplicates, %d incomplete rejected).'
                 % (discharging, line_charging, line_quiet,
                    funnel['analysed_observations'] - discharging - line_charging - line_quiet,
                    funnel['analysed_observations'], funnel['rejected_duplicate_cached_payload'],
                    funnel['rejected_missing_or_invalid_numeric'] + funnel['rejected_unknown_mode']))
    if exploratory and exploratory['contrast_v'] is not None:
        parts.append('Exploratory Line-not-charging minus Invert-discharging contrast %+.3f V from %d pairs '
                     '(confidence %.2f); collection-time evidence, not verified sag.'
                     % (exploratory['contrast_v'], exploratory['comparable_pairs'], exploratory['confidence'] or 0))
    else:
        parts.append('Voltage comparison blocked: ' + ', '.join(blocked_by) + '.')
    if confounded and confounded['median_difference_v'] is not None:
        parts.append('Charging-confounded descriptive difference (Line charging minus Invert discharging) '
                     'median %+.3f V over %d pairs; charge current lifts Line voltage, so this is neither '
                     'sag nor rebound.' % (confounded['median_difference_v'], confounded['matched_pairs']))
    if candidate:
        parts.append('Hypothetical %s under confirmation rules; non-actionable.' % candidate[0])
    else:
        parts.append('No threshold hypothesis is supported (' + ', '.join(unsupported) + '). '
                     'Frequent switching alone does not establish which threshold, if any, should change.')
    return {
        'summary': ' '.join(parts),
        'cycling': cycling,
        'voltage_comparison': {
            'status': 'ESTIMATED' if exploratory and exploratory['contrast_v'] is not None else 'BLOCKED',
            'blocked_by': blocked_by,
            'basis': 'LINE_NOT_CHARGING_MINUS_INVERT_DISCHARGING',
            'comparable_pairs': exploratory['comparable_pairs'] if exploratory else 0,
            'candidate_pairs': exploratory['candidate_pairs'] if exploratory else 0,
            'required_pairs': MIN_CANDIDATE_PAIRS,
            # Per-pair rejection counts, observation exclusions and the charging-confounded
            # description live in exploratory_voltage_contrast to avoid duplicating them.
            'details': 'exploratory_voltage_contrast'},
        'evidence_classes': evidence_classes,
        'sample_counts': funnel,
        'hypothesis_support': {'supported': bool(candidate),
                               'candidate': candidate[0] if candidate else None,
                               'blocked_candidate': blocked_candidate,
                               'raise_requirements': raise_checks,
                               'unsupported_because': unsupported if not candidate else [],
                               'cycling_used_as_threshold_evidence': False},
        'hold_reasons': hold_reasons,
    }, hold_reasons


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
    raw_count = len(rows)
    previous = (previous if isinstance(previous, dict)
                and previous.get('algorithm_version') in STATE_COMPATIBLE_VERSIONS else {})
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
    analysed, funnel = _prepare_observations(valid)
    funnel = {'raw_collections': raw_count, 'rejected_outside_window_or_invalid_time': raw_count - len(valid), **funnel}
    cycling = _reported_cycling(valid, now)
    evidence_classes = _class_summary(analysed)
    exploratory = _explore_collections(analysed, changes, now) if valid and complete_latest and (now - latest['time']).total_seconds() <= 300 else None
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
            exploratory['hypothesis_gate_confidence'] = round(exploratory['hypothesis_gate_confidence'] * 0.35, 3)
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
    blocked_candidate = None
    if baseline is None:
        reasons.append('HISTORICAL_BASELINE_UNKNOWN')
    elif not (24 <= baseline <= 29 and baseline * 2 == round(baseline * 2)):
        reasons.append('HISTORICAL_BASELINE_OUT_OF_RANGE')
    if exploratory and baseline is not None and 24 <= baseline <= 29 and baseline * 2 == round(baseline * 2):
        contrast = exploratory['contrast_v']
        if (contrast is not None and contrast >= 0.2 and changes >= 3 and not changed
                and (exploratory.get('confidence') or 0) >= 0.5):
            candidate = ('HYPOTHETICAL_RAISE', baseline + 0.5)
        elif (exploratory['recent_charging_samples'] >= 3 and not changed
              and exploratory.get('hypothesis_gate_confidence', 0) >= 0.35):
            candidate = ('HYPOTHETICAL_LOWER', baseline - 0.5)
        # Historical utility is 25.5 V: preserve at least one catalogue step.
        if candidate and not (24 <= candidate[1] <= 29 and candidate[1] - 25.5 >= 0.5):
            blocked_candidate = {'decision': candidate[0], 'voltage': candidate[1],
                                 'reason': 'CATALOGUE_OR_HYSTERESIS_BLOCK'}
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
    contrast_confidence = exploratory.get('confidence') if exploratory else None
    gate_confidence = exploratory.get('hypothesis_gate_confidence') if exploratory else None
    # The headline confidence belongs to the stated basis: the voltage estimate, or for a
    # candidate the gate that admitted it. No estimate means no voltage confidence.
    if candidate:
        overall_confidence, confidence_basis = gate_confidence, (
            'VOLTAGE_CONTRAST' if contrast_confidence is not None else 'OBSERVATION_SUFFICIENCY_ONLY')
    elif contrast_confidence is not None:
        overall_confidence, confidence_basis = contrast_confidence, 'VOLTAGE_CONTRAST'
    else:
        overall_confidence, confidence_basis = None, 'NO_VOLTAGE_ESTIMATE'
    confidence_breakdown = {
        'basis': confidence_basis,
        'cycling_detection': cycling['detection_confidence'],
        'voltage_contrast': contrast_confidence,
        'observation_sufficiency': exploratory.get('observation_sufficiency') if exploratory else None,
        'hypothesis_gate': gate_confidence}
    diagnostics, hold_reasons = _decision_diagnostics(cycling, exploratory, evidence_classes, funnel, candidate,
                                                      blocked_candidate, changed, changes, decision)
    for reason in hold_reasons:
        if reason not in reasons:
            reasons.append(reason)
    explanation = ('Collection-time research only: mode/charging patterns are uncertain, timestamps may reflect cached readings, baseline is historical, and BMS limits and current settings are unverified. Candidate status: ' + stability if exploratory else 'Learning and threshold recommendations are blocked: device measurement time, receipt provenance, approved battery limits and current settings are not established.')
    if changed:
        explanation += ' Empirical response changed; cause is unknown and this is not proof that batteries were added or changed.'
    explanation += ' ' + diagnostics.pop('summary')
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
        'confidence': overall_confidence, 'confidence_breakdown': confidence_breakdown,
        'decision_diagnostics': diagnostics,
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
