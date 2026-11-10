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
from app.battery_intervals import (battery_voltage_presentation, calculate_battery_intervals,
                                   valid_battery_voltage, MIN_SLOPE_SECONDS)

VERSION = 'online-exploratory-6'
# Proposal confirmation/cooldown state written by these versions is carried forward
# so an upgrade cannot silently reset a durable dry-run cooldown.
STATE_COMPATIBLE_VERSIONS = frozenset(('online-exploratory-4', 'online-exploratory-5', VERSION))
WINDOW = timedelta(hours=3)
DAILY_REVIEW_WINDOW = timedelta(hours=24)
FAST_WINDOW = timedelta(minutes=20)
FAST_MAX_OBSERVATIONS = 6
LOGGER = logging.getLogger('IMPORTANT')
LOCK_ID = 5276389241085182
PAIR_MAX_GAP = timedelta(minutes=30)
RESPONSE_CHANGE_MIN_V = 0.25
RESPONSE_BOUND_V = 2.0
MIN_CANDIDATE_PAIRS = 3
# Bounded analysis memory: three hours at a 15 s cadence. The previous fixed cap of
# 24 unique observations (~35 min at the production cadence) discarded most of the
# window, including earlier Line-not-charging readings and mode transitions.
MAX_ANALYSIS_OBSERVATIONS = 720
MAX_DAILY_REVIEW_OBSERVATIONS = 2000
MAX_MODE_RUNS = 720
SHORT_RUN_MAX = timedelta(minutes=20)
CYCLING_MIN_TRANSITIONS = 4
CYCLING_MIN_SHORT_RUNS = 2
COLLECTION_GAP_LIMIT = timedelta(minutes=10)
# Collections closer than this (the collector polls every 10 s while switching) are
# not treated as independent slope evidence: a 0.01 V quantization step over 1 s
# would read as 36 V/h. Integration still uses any positive interval.
MIN_SLOPE_INTERVAL_S = MIN_SLOPE_SECONDS
MODES = ('Line Mode', 'Invert Mode')
HISTORICAL_UTILITY_V = 25.5
APPLICATION_THRESHOLD_STEP_V = 0.5
APPLICATION_UTILITY_RANGE_V = (21.0, 25.5)
APPLICATION_BATTERY_RANGE_V = (24.0, 29.0)
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


def _battery_signature(row):
    """Signature only the battery measurements used by the passive response model."""
    return tuple(_number(row.get(key)) for key in
                 ('battery_voltage', 'battery_current_chg', 'battery_current_dis'))


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
        bad = [k for k in NUMERIC_KEYS if _number(r.get(k)) is None or r[k] < 0
               or (k == 'battery_voltage' and valid_battery_voltage(r[k]) is None)]
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


_MISSING = object()


def _mode_continuity(rows):
    """Decide, per row, whether continuity with the previous supplied row is broken.

    A row's raw predecessor is ``previous_collection_time`` when supplied (the database
    boundary query sets it from the unfiltered collection stream) and otherwise the
    previous supplied row. Continuity breaks before a row when it has no predecessor,
    when the predecessor is more than COLLECTION_GAP_LIMIT earlier, or when the
    predecessor is not the previous supplied row of a different run (rows omitted).
    Unknown/NULL/conflicting modes are handled by callers as additional barriers.
    A known mode change across a gap is reported as uncertain, never as a transition.
    """
    ordered = sorted(rows, key=lambda r: r['time'])
    limit = COLLECTION_GAP_LIMIT.total_seconds()
    breaks, gaps, uncertain = [], [], []
    previous = None
    for r in ordered:
        predecessor = r.get('previous_collection_time', _MISSING)
        if predecessor is _MISSING:
            predecessor = previous['time'] if previous is not None else None
        gap = (r['time'] - predecessor).total_seconds() if isinstance(predecessor, datetime) else None
        adjacent = previous is not None and predecessor == previous['time']
        same_run = (previous is not None and r.get('mode_run') is not None
                    and r.get('mode_run') == previous.get('mode_run'))
        if gap is None:
            brk = True
        elif gap > limit:
            brk = True
            before_mode = previous.get('working_mode') if adjacent else None
            after_mode = r.get('working_mode')
            gaps.append({'start': predecessor.isoformat(), 'end': r['time'].isoformat(), 'seconds': int(gap),
                         'mode_before': before_mode if before_mode in MODES else None,
                         'mode_after': after_mode if after_mode in MODES else None})
            if before_mode in MODES and after_mode in MODES and before_mode != after_mode:
                uncertain.append({'from_mode': before_mode, 'to_mode': after_mode,
                                  'unobserved_start': predecessor.isoformat(), 'unobserved_end': r['time'].isoformat(),
                                  'unobserved_seconds': int(gap),
                                  'interpretation': 'ENDPOINT_MODES_DIFFER_ACROSS_UNOBSERVED_INTERVAL; '
                                                    'not a directly observed transition'})
        else:
            brk = not adjacent and not same_run
        breaks.append(brk)
        previous = r
    return {'rows': ordered, 'breaks_before': breaks, 'gaps': gaps, 'gap_count': len(gaps),
            'max_gap_s': max((g['seconds'] for g in gaps), default=None), 'uncertain_changes': uncertain}


def _count_supported_mode_changes(rows):
    """Direct Line/Invert changes between continuous, known-mode neighbours only."""
    continuity = _mode_continuity(rows)
    changes, previous_mode = 0, None
    for r, brk in zip(continuity['rows'], continuity['breaks_before']):
        mode = r.get('working_mode')
        if brk or mode not in MODES:
            previous_mode = mode if (mode in MODES) else None
            continue
        if previous_mode is not None and mode != previous_mode:
            changes += 1
        previous_mode = mode
    return changes, continuity


def _reported_cycling(valid, now):
    """Detect reported Line/Invert alternation independently of voltage-pair eligibility.

    Uses every supplied in-window mode observation (numeric fields may be missing),
    after dropping repeated cached payloads. The database worker supplies first/last
    samples for bounded mode runs so the raw telemetry row cap cannot hide older
    transitions. Each transition is bracketed by collection times, not device time.
    """
    continuity = _mode_continuity(valid)
    sequence, groups, duplicates = [], [], 0
    group = []
    last_signature = None
    # The payload's first collection time survives gap breaks (a cached value can sit
    # unchanged across a collection outage); an unknown-mode row resets it.
    carry_signature, carry_since = None, None
    for r, brk in zip(continuity['rows'], continuity['breaks_before']):
        if brk and group:
            # Observation gaps, omitted rows and the window edge end a segment; no
            # transition is ever inferred across them.
            groups.append(group)
            group = []
            last_signature = None
        if r.get('working_mode') not in MODES:
            if group:
                groups.append(group)
                group = []
            last_signature = None
            carry_signature, carry_since = None, None
            continue
        signature = _observation_signature(r)
        # Do not erase a legitimate return to a previous Line/Invert payload after
        # an intervening observation; cached repeats are consecutive in collection order.
        # A repeat is not an independent measurement, but its collection time is kept
        # as the latest time the reported mode was still being collected.
        if signature == last_signature:
            duplicates += 1
            group[-1]['_last_collected'] = r['time']
            continue
        kept = dict(r)
        kept['_last_collected'] = r['time']
        kept['_payload_since'] = carry_since if signature == carry_signature else r['time']
        carry_signature, carry_since = signature, kept['_payload_since']
        sequence.append(kept)
        group.append(kept)
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
            cached_before = last['_last_collected'] != last['_payload_since']
            transitions.append({
                'direction': 'INVERT_TO_LINE' if mode_after == 'Line Mode' else 'LINE_TO_INVERT',
                # Reported-mode boundary in collection order: last collection still
                # reporting the old mode, first collection reporting the new one.
                'bracket_start': last['_last_collected'].isoformat(), 'bracket_end': first['time'].isoformat(),
                'bracket_seconds': int((first['time'] - last['_last_collected']).total_seconds()),
                'last_distinct_payload_before': last['_payload_since'].isoformat(),
                'payload_unchanged_seconds_before': int((last['_last_collected'] - last['_payload_since']).total_seconds()),
                'observation_time_confidence': ('LOW_CACHED_PAYLOAD_REPEATED_BEFORE_SWITCH' if cached_before
                                                else 'COLLECTION_TIME_ONLY'),
                'physical_switch_time': 'UNKNOWN; not established by the collection bracket',
                'state_before': _evidence_class(last), 'state_after': _evidence_class(first),
                'battery_v_before': valid_battery_voltage(last.get('battery_voltage')),
                'battery_v_after': valid_battery_voltage(first.get('battery_voltage')),
                'load_w': _number(first.get('output_power_w')), 'pv_w': _number(first.get('pv_power_w'))})
        for index in range(1, len(runs) - 1):
            members = runs[index][1]
            lower = (members[-1]['_last_collected'] - members[0]['time']).total_seconds()
            upper = (runs[index + 1][1][0]['time'] - runs[index - 1][1][-1]['_last_collected']).total_seconds()
            complete_runs.append((runs[index][0], lower, upper))
    short = [run for run in complete_runs if run[2] <= SHORT_RUN_MAX.total_seconds()]
    gaps = [(b['time'] - a['_last_collected']).total_seconds() for a, b in zip(sequence, sequence[1:])]
    span = (sequence[-1]['_last_collected'] - sequence[0]['time']).total_seconds() if sequence else 0
    # Segments are continuous by construction (no gap above COLLECTION_GAP_LIMIT inside).
    covered = sum((g[-1]['_last_collected'] - g[0]['time']).total_seconds() for g in groups)
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
        'observation_gap_limit_s': int(COLLECTION_GAP_LIMIT.total_seconds()),
        'observation_gaps': continuity['gap_count'],
        'max_observation_gap_s': continuity['max_gap_s'],
        'recent_observation_gaps': continuity['gaps'][-3:],
        'uncertain_mode_changes_across_gaps': len(continuity['uncertain_changes']),
        'recent_uncertain_mode_changes': continuity['uncertain_changes'][-3:],
        'continuity_segments': len(groups),
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
            weights = [0.5 ** ((now - p['time']).total_seconds() / 1200.0) for p in retained]
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
        confidence = min(1.0, len(retained) / 3) * pair_fit * 0.5 ** (newest_pair_age / 1800)
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


def _fast_context(rows, now):
    """Summarize fast collection context; payload changes are not verified device samples."""
    collections = sorted((r for r in rows if now - FAST_WINDOW <= r['time'] <= now), key=lambda r: r['time'])
    distinct = []
    last_signature = None
    for item in collections:
        signature = _observation_signature(item)
        if signature != last_signature:
            distinct.append(item)
        last_signature = signature
    distinct = distinct[-FAST_MAX_OBSERVATIONS:]
    # Continuity is judged on raw collections (cached repeats still show the collector
    # was running); unknown/missing/conflicting modes and gaps above
    # COLLECTION_GAP_LIMIT break it. Only the reported distinct-payload span is counted.
    fast_rows = [r for r in collections if not distinct or r['time'] >= distinct[0]['time']]
    changes, fast_continuity = _count_supported_mode_changes(fast_rows)
    times = [r['time'] for r in distinct]
    pv = [_number(r.get('pv_power_w')) for r in distinct]
    pv = [value for value in pv if value is not None]
    load = [_number(r.get('output_power_w')) for r in distinct]
    load = [value for value in load if value is not None]
    voltage_intervals = calculate_battery_intervals(collections)['successful']
    newest_collection_age = (now - collections[-1]['time']).total_seconds() if collections else None
    newest_distinct_age = (now - distinct[-1]['time']).total_seconds() if distinct else None
    gaps = [(b-a).total_seconds() for a, b in zip(times, times[1:])]
    supported_slopes = [interval['voltage_slope_v_per_hour'] for interval in voltage_intervals
                        if interval['voltage_slope_v_per_hour'] is not None]
    latest_slope = round(supported_slopes[-1], 4) if supported_slopes else None
    charge_values = [_number(r.get('battery_current_chg')) for r in distinct]
    discharge_values = [_number(r.get('battery_current_dis')) for r in distinct]
    charge_values = [v for v in charge_values if v is not None]
    discharge_values = [v for v in discharge_values if v is not None]
    current_direction = 'UNKNOWN'
    if distinct:
        latest_charge = _number(distinct[-1].get('battery_current_chg'))
        latest_discharge = _number(distinct[-1].get('battery_current_dis'))
        if latest_charge is not None and latest_discharge is not None:
            if latest_charge < 0 or latest_discharge < 0:
                current_direction = 'INVALID_REPORTED_CURRENT'
            elif latest_charge > 0 and latest_discharge > 0:
                current_direction = 'CONFLICTING_REPORTED_CURRENTS'
            elif latest_charge > 0:
                current_direction = 'CHARGE_REPORTED'
            elif latest_discharge > 0:
                current_direction = 'DISCHARGE_REPORTED'
            else:
                current_direction = 'IDLE_REPORTED'
    return {
        'window_seconds': int(FAST_WINDOW.total_seconds()),
        'maximum_reported_observations': FAST_MAX_OBSERVATIONS,
        'collections_in_window': len(collections), 'distinct_consecutive_payloads': len(distinct),
        'reported_mode_changes': changes,
        'observation_gaps': fast_continuity['gap_count'],
        'uncertain_mode_changes_across_gaps': len(fast_continuity['uncertain_changes']),
        'latest_collection_age_seconds': int(newest_collection_age) if newest_collection_age is not None else None,
        'latest_distinct_payload_age_seconds': int(newest_distinct_age) if newest_distinct_age is not None else None,
        'max_distinct_payload_gap_seconds': int(max(gaps)) if gaps else None,
        'pv_range_w': [min(pv), max(pv)] if pv else None,
        'load_range_w': [min(load), max(load)] if load else None,
        'battery_voltage_slope_v_per_hour': latest_slope,
        'charge_current_range_a_reported': [min(charge_values), max(charge_values)] if charge_values else None,
        'discharge_current_range_a_reported': [min(discharge_values), max(discharge_values)] if discharge_values else None,
        'latest_reported_current_direction': current_direction,
        'collection_recent': newest_collection_age is not None and newest_collection_age <= 300,
        'freshness': 'COLLECTION_TIME_ONLY; DEVICE_FRESHNESS_UNVERIFIED',
        'independent_sample_claim': 'PAYLOAD_CHANGE_ONLY; DEVICE_SAMPLE_ID_UNAVAILABLE',
        'time_provenance': 'COLLECTION_TIME_ONLY'}


def _battery_response_model(rows, now):
    """Conservative finite-difference diagnostics from distinct collection payloads.

    Current fields are reported inverter telemetry. Because the source schema has no
    verified device-observation timestamp, chemistry, bank capacity or temperature,
    these are labeled reported-throughput proxies and never promoted to SagLearner.
    """
    # Exactly the same conservative interval semantics as the authenticated grid.
    # In particular, never bridge missing rows or cached battery payloads.
    ordered = sorted((row for row in rows if isinstance(row.get('time'), datetime)),
                     key=lambda r: r['time'])
    integration = calculate_battery_intervals(ordered)
    valid_rows = [row for row in ordered if valid_battery_voltage(row.get('battery_voltage')) is not None
                  and _number(row.get('battery_current_chg')) is not None
                  and _number(row.get('battery_current_dis')) is not None
                  and min(row['battery_current_chg'], row['battery_current_dis']) >= 0]
    # Distinct battery payloads count only once as response observations,
    # even if PV or AC load changed. Interval coverage is computed separately
    # from *all* raw collections by the shared integrator above.
    usable = []
    prior_sig = None
    valid_ids = {id(row) for row in valid_rows}
    for row in ordered:
        if id(row) not in valid_ids:
            prior_sig = None
            continue
        sig = _battery_signature(row)
        if sig != prior_sig:
            usable.append({'row': row})
        prior_sig = sig
    segments = []
    for interval in integration['successful']:
        left, right = interval['older'], interval['newer']
        seconds = interval['seconds']
        hours = seconds / 3600.0
        charge_left, charge_right = left['battery_current_chg'], right['battery_current_chg']
        discharge_left, discharge_right = left['battery_current_dis'], right['battery_current_dis']
        voltage_left, voltage_right = left['battery_voltage'], right['battery_voltage']
        voltage_delta = voltage_right - voltage_left
        load_left, load_right = _number(left.get('output_power_w')), _number(right.get('output_power_w'))
        pv_left, pv_right = _number(left.get('pv_power_w')), _number(right.get('pv_power_w'))
        load_delta = load_right - load_left if load_left is not None and load_right is not None else None
        discharge_delta = discharge_right - discharge_left
        net_current_delta = ((discharge_right - charge_right) - (discharge_left - charge_left))
        charge_active = charge_left > 0 or charge_right > 0
        recovery = (voltage_delta > 0 and not charge_active and
                    (discharge_delta < 0 or (load_delta is not None and load_delta < 0)))
        segments.append({
            'start_collection_time': left['time'].isoformat(), 'end_collection_time': right['time'].isoformat(),
            'seconds': round(seconds, 3), 'voltage_delta_v': round(voltage_delta, 4),
            'voltage_slope_v_per_hour': (round(interval['voltage_slope_v_per_hour'], 4)
                                         if interval['voltage_slope_v_per_hour'] is not None else None),
            'slope_status': ('COMPUTED' if interval['voltage_slope_v_per_hour'] is not None
                             else 'INTERVAL_TOO_SHORT_FOR_SLOPE'),
            'charge_current_delta_a_reported': round(charge_right - charge_left, 3),
            'discharge_current_delta_a_reported': round(discharge_delta, 3),
            'charge_ah_proxy': round(interval['charge_ah'], 4),
            'discharge_ah_proxy': round(interval['discharge_ah'], 4),
            'charge_wh_proxy': round(interval['charge_wh'], 3),
            'discharge_wh_proxy': round(interval['discharge_wh'], 3),
            'voltage_per_reported_net_current_delta_proxy_v_per_a': round(voltage_delta / net_current_delta, 4)
                if net_current_delta != 0 else None,
            'current_response_interpretation': 'RELATIVE_REPORTED_FIELD_PROXY; NOT VERIFIED IMPEDANCE',
            'mode_from': left.get('working_mode') if left.get('working_mode') in MODES else 'UNKNOWN',
            'mode_to': right.get('working_mode') if right.get('working_mode') in MODES else 'UNKNOWN',
            'reported_mode_changed': left.get('working_mode') != right.get('working_mode'),
            'load_delta_w_reported': round(load_delta, 1) if load_delta is not None else None,
            'pv_delta_w_reported': round(pv_right - pv_left, 1) if pv_left is not None and pv_right is not None else None,
            'voltage_increase_confounded_by_reported_charging': voltage_delta > 0 and charge_active,
            'voltage_recovery_with_reported_discharge_or_load_reduction': recovery,
            'interpretation': 'COLLECTION_TIME_ASSOCIATION_ONLY; NO_CAUSAL_OR_TRANSIENT_CLAIM'})
    charged_ah = integration['totals']['charge_ah']
    discharged_ah = integration['totals']['discharge_ah']
    charged_wh = integration['totals']['charge_wh']
    discharged_wh = integration['totals']['discharge_wh']
    integrated_seconds = integration['covered_seconds']
    rejected = integration['rejected']
    excluded_seconds = integration['excluded_seconds']
    excluded_observations = {key: integration['excluded_observations'].get(key, 0)
                             for key in ('MISSING_CURRENT', 'MISSING_VOLTAGE',
                                         'INVALID_NEGATIVE_MEASUREMENT', 'INVALID_ZERO_VOLTAGE')}
    latest_age = (now - usable[-1]['row']['time']).total_seconds() if usable else None
    recent = segments[-5:]
    slopes = [segment['voltage_slope_v_per_hour'] for segment in segments
              if segment['voltage_slope_v_per_hour'] is not None]
    recent_slopes = [segment['voltage_slope_v_per_hour'] for segment in recent
                     if segment['voltage_slope_v_per_hour'] is not None]
    reported_soc = sum(_number(item['row'].get('battery_soc')) is not None for item in usable)
    elapsed_seconds = integration['elapsed_seconds']
    unsupported = {key: count for key, count in rejected.items() if count}
    complete = integration['complete']
    if not usable:
        throughput_status = 'NO_USABLE_OBSERVATIONS'
    elif not segments:
        throughput_status = 'INSUFFICIENT_MEASUREMENT_COVERAGE'
    elif charged_ah == 0 and discharged_ah == 0:
        throughput_status = ('OBSERVED_ZERO_REPORTED_CHANNEL_THROUGHPUT' if complete else
                             'PARTIAL_COVERAGE_ZERO_REPORTED_CHANNEL_THROUGHPUT')
    else:
        throughput_status = ('OBSERVED_REPORTED_CHANNEL_THROUGHPUT' if complete else
                             'PARTIAL_COVERAGE_REPORTED_CHANNEL_THROUGHPUT')
    throughput = bool(segments)
    return {
        'observations': len(usable), 'intervals_integrated': len(segments),
        'integrated_seconds': round(integrated_seconds, 3),
        'elapsed_collection_seconds': round(elapsed_seconds, 3),
        'unintegrated_seconds': round(max(0.0, elapsed_seconds - integrated_seconds), 3),
        'integration_coverage_ratio': round(integration['coverage_ratio'], 4) if integration['coverage_ratio'] is not None else None,
        'throughput_coverage': integration['status'],
        'excluded_interval_seconds': {key: round(value, 3) for key, value in excluded_seconds.items() if value},
        'excluded_observations': excluded_observations,
        'continuity_barrier_policy': 'MISSING_OR_INVALID_MEASUREMENT_INTERRUPTS_INTEGRATION; NEVER_BRIDGED',
        'minimum_slope_interval_seconds': MIN_SLOPE_INTERVAL_S,
        'throughput_status': throughput_status,
        'charged_ah_proxy': round(charged_ah, 4) if throughput else None,
        'discharged_ah_proxy': round(discharged_ah, 4) if throughput else None,
        'charged_wh_proxy': round(charged_wh, 2) if throughput else None,
        'discharged_wh_proxy': round(discharged_wh, 2) if throughput else None,
        'easun_reported_charged_ah_proxy': round(charged_ah, 4) if throughput else None,
        'easun_reported_discharged_ah_proxy': round(discharged_ah, 4) if throughput else None,
        'easun_reported_charged_wh_proxy': round(charged_wh, 2) if throughput else None,
        'easun_reported_discharged_wh_proxy': round(discharged_wh, 2) if throughput else None,
        'total_battery_current_observed': False,
        'total_battery_charged_ah': None, 'total_battery_discharged_ah': None,
        'total_battery_throughput_status': 'UNOBSERVED; EASUN_CHANNELS_ARE_NOT_TOTAL_BATTERY_CURRENT',
        'external_charging_assumed_zero': False,
        'throughput_scope': 'EASUN-REPORTED CHANNELS ONLY; additional charging paths may be unobserved',
        'recent_intervals': recent,
        'latest_voltage_slope_v_per_hour': recent_slopes[-1] if recent_slopes else None,
        'median_voltage_slope_v_per_hour': round(median(slopes), 4) if slopes else None,
        'latest_collection_age_seconds': int(latest_age) if latest_age is not None else None,
        'freshness': ('COLLECTION_RECENT; DEVICE_FRESHNESS_UNVERIFIED'
                      if latest_age is not None and latest_age <= 600 else 'STALE_OR_UNAVAILABLE'),
        'capacity_status': 'CAPACITY_NOT_IDENTIFIABLE',
        'capacity_limitations': ['SOC_ANCHORS_UNVERIFIED', 'BATTERY_CHEMISTRY_UNKNOWN',
            'BATTERY_TEMPERATURE_UNAVAILABLE', 'CURRENT_SOURCE_SEMANTICS_UNVERIFIED',
            'DEVICE_OBSERVATION_TIME_UNAVAILABLE'],
        'additional_data_required_for_capacity': ['trusted SOC anchors over a substantial charge/discharge interval',
            'verified battery chemistry and manufacturer limits', 'verified battery-current semantics and units',
            'battery temperature', 'device measurement timestamps'],
        'reported_soc_values_available': reported_soc,
        'soc_semantics': 'Battery Capacity label is mapped to battery_soc; percent display does not verify SOC accuracy',
        'battery_temperature': 'NOT_AVAILABLE_IN_INVERTER_METRICS_SCHEMA',
        'current_provenance': 'REPORTED_INVERTER_TELEMETRY_UNVERIFIED; labels imply A but source semantics are unverified',
        'energy_provenance': 'TRAPEZOIDAL_REPORTED_CURRENT_TIMES_REPORTED_VOLTAGE; DC_PROXY_ONLY',
        'impedance_status': 'NOT_IDENTIFIABLE; voltage/current ratio is only a reported-field proxy',
        'power_domains': {'pv_power_w': 'PV total Power; PV-side reported W',
            'output_power_w': 'Output Active Power; AC-output active-power report',
            'battery_energy': 'computed from reported battery current and voltage only'},
        'pv_and_output_power_used_for_battery_energy': False,
        'confidence': 'LOW_UNVERIFIED_SOURCE' if segments else 'INSUFFICIENT_INTERVALS',
        'confidence_interpretation': 'QUALITATIVE_DATA_QUALITY_LABEL_NOT_CALIBRATED_PROBABILITY',
        'rejected_intervals': rejected, 'gaps_over_15m': rejected['GAP_OVER_15M']}


def _multi_source_charging_context(rows, now):
    """Describe EASUN-visible charging relationships without imputing other sources.

    Interval validity, continuity and voltage slopes come ONLY from the shared
    policy in app.battery_intervals (the same one used by the worker response
    model, fast context and grids). Raw neighbouring collections are never
    re-paired here: cached repeats, missing/invalid measurements (incl. 0 V),
    conflicting channels, ambiguous times and gaps over 15 min are barriers,
    and intervals shorter than MIN_SLOPE_INTERVAL_S give no slope or
    voltage-trend evidence. A voltage rise is an observation, not proof of
    charging; external charging stays possible and unmeasured.
    """
    ordered = sorted((row for row in rows if isinstance(row.get('time'), datetime)
                      and row['time'].tzinfo is not None and row['time'].utcoffset() is not None
                      and now - WINDOW <= row['time'] <= now), key=lambda row: row['time'])
    distinct = []
    repeated = 0
    previous_signature = None
    for row in ordered:
        signature = _observation_signature(row)
        if signature == previous_signature:
            repeated += 1
            continue
        distinct.append(row)
        previous_signature = signature

    shared = calculate_battery_intervals(ordered)
    accepted = shared['successful']
    rejected_counts = {key: count for key, count in shared['rejected'].items() if count}
    interval_groups = {}
    slopes_by_group = {}
    rising_without_easun_charge = 0
    voltage_rise_observed = 0
    voltage_rises_with_easun_charge_reported = 0
    recovery_with_load_reduction = 0
    soc_changes_without_easun_current = 0
    pv_rises = pv_falls = load_changes = 0
    too_short_for_slope = 0
    collection_hours = set()
    interval_count = 0
    for interval in accepted:
        left, right = interval['older'], interval['newer']
        slope = interval['voltage_slope_v_per_hour']
        if slope is None:
            # Accepted for throughput, but too short to be independent voltage evidence.
            too_short_for_slope += 1
            continue
        interval_count += 1
        delta_v = right['battery_voltage'] - left['battery_voltage']
        # The shared policy guarantees finite, non-negative, non-conflicting channels.
        charge_mean = (left['battery_current_chg'] + right['battery_current_chg']) / 2
        discharge_mean = (left['battery_current_dis'] + right['battery_current_dis']) / 2
        if charge_mean > 0 and discharge_mean > 0:
            group = 'EASUN_CURRENT_CONTEXT_CHANGED_ACROSS_INTERVAL'
        elif charge_mean > 0:
            group = 'EASUN_CHARGE_REPORTED'
        elif discharge_mean > 0:
            group = 'EASUN_DISCHARGE_REPORTED'
        else:
            group = 'NO_EASUN_CURRENT_REPORTED'
        interval_groups[group] = interval_groups.get(group, 0) + 1
        slopes_by_group.setdefault(group, []).append(slope)
        soc_left, soc_right = _number(left.get('battery_soc')), _number(right.get('battery_soc'))
        if (soc_left is not None and soc_right is not None and soc_left != soc_right
                and charge_mean == 0 and discharge_mean == 0):
            soc_changes_without_easun_current += 1
        exclusive_charge = charge_mean > 0 and discharge_mean == 0
        if delta_v > 0:
            voltage_rise_observed += 1
            if exclusive_charge:
                voltage_rises_with_easun_charge_reported += 1
            elif charge_mean == 0:
                rising_without_easun_charge += 1
        pv_left, pv_right = _number(left.get('pv_power_w')), _number(right.get('pv_power_w'))
        if pv_left is not None and pv_right is not None:
            pv_delta = pv_right - pv_left
            pv_rises += pv_delta > 0
            pv_falls += pv_delta < 0
        load_left, load_right = _number(left.get('output_power_w')), _number(right.get('output_power_w'))
        if load_left is not None and load_right is not None and load_left != load_right:
            load_changes += 1
            if delta_v > 0 and load_right < load_left and not exclusive_charge:
                recovery_with_load_reduction += 1
        collection_hours.add(right['time'].astimezone(timezone.utc).hour)
    # Conflicting channels are a shared-policy barrier: counted, never used as a trend.
    simultaneous_easun_channels = shared['rejected']['CONFLICTING_CURRENT']
    gap_intervals = shared['rejected']['GAP_OVER_15M'] + shared['rejected']['NON_POSITIVE_INTERVAL']

    labels = ['EXTERNAL_CHARGING_POSSIBLE', 'TOTAL_BATTERY_CURRENT_UNOBSERVED',
              'CHARGING_RELATIONSHIP_UNVERIFIED', 'CAPACITY_NOT_IDENTIFIABLE']
    if voltage_rise_observed:
        labels.append('VOLTAGE_RISE_OBSERVED')
    if recovery_with_load_reduction:
        labels.append('VOLTAGE_RECOVERY_POSSIBLE')
    if soc_changes_without_easun_current:
        labels.append('SOC_CHANGE_NOT_VALIDATED_BY_EASUN_THROUGHPUT')
    medians = {key: round(median(values), 4) for key, values in sorted(slopes_by_group.items()) if values}
    return {
        'status': 'QUALITATIVE_COLLECTION_TIME_RELATIONSHIPS_ONLY',
        'evidence_labels': labels,
        'external_charging_status': 'POSSIBLE; NOT DETECTED AT A SPECIFIC MOMENT',
        'external_charging_assumed_zero': False,
        'easun_current_semantics_verified': False,
        'easun_current_equals_total_battery_current': False,
        'soc_accuracy_verified': False,
        'total_battery_current_observed': False,
        'charging_relationship': 'CHARGING_RELATIONSHIP_UNVERIFIED',
        'pv_source_coverage': 'EASUN_VISIBLE_PV_ONLY; OTHER_ORIENTATIONS_AND_CONTROLLERS_UNOBSERVED',
        'solar_topology': 'UNKNOWN; MULTIPLE_ORIENTATIONS_OR_CHARGING_PATHS_MAY_EXIST',
        'site_timezone_known': False,
        'collection_utc_hours_seen': sorted(collection_hours),
        'distinct_payloads': len(distinct), 'consecutive_cached_payloads': repeated,
        'intervals_with_voltage_response': interval_count,
        'intervals_by_easun_reported_current_context': interval_groups,
        'median_voltage_slope_v_per_hour_by_easun_context': medians,
        'voltage_rise_observed_intervals': voltage_rise_observed,
        'voltage_rises_with_easun_charge_reported': voltage_rises_with_easun_charge_reported,
        # Both-channel intervals are rejected by the shared policy, so no rise is attributed to them.
        'voltage_rises_with_both_easun_channels_reported': 0,
        'voltage_rises_without_easun_charge_reported': rising_without_easun_charge,
        'recovery_associated_with_load_reduction': recovery_with_load_reduction,
        'simultaneous_nonzero_easun_charge_and_discharge_channel_intervals': simultaneous_easun_channels,
        'soc_changes_without_easun_current_reported': soc_changes_without_easun_current,
        'easun_visible_pv_rising_intervals': pv_rises,
        'easun_visible_pv_falling_intervals': pv_falls,
        'load_change_intervals': load_changes,
        'gaps_or_nonpositive_intervals': gap_intervals,
        'interval_policy': 'SHARED_BATTERY_INTERVAL_POLICY; SAME_AS_WORKER_AND_GRID',
        'raw_collection_intervals': len(shared['intervals']),
        'intervals_accepted_by_shared_policy': len(accepted),
        'intervals_rejected_by_shared_policy': rejected_counts,
        'accepted_intervals_too_short_for_slope': too_short_for_slope,
        'voltage_trend_evidence': ('ACCEPTED_INTERVALS_ONLY' if interval_count else
                                   'NONE; NO_INTERVAL_ACCEPTED_FOR_SLOPE_BY_SHARED_POLICY'),
        'confidence': 'LOW_UNVERIFIED_SOURCE; NOT A CALIBRATED PROBABILITY',
        'limitations': ['No verified battery-current sensor semantics or total-current measurement.',
            'External charging may continue while EASUN reports zero charge current.',
            'EASUN-visible PV is not separated by array orientation or controller.',
            'PV-side power and AC load cannot be converted to battery current without topology and losses.',
            'Collection time is not device measurement time; site timezone is unknown.']}


def _daily_behavior_review(rows, now, *, truncated=False):
    """Compare a recent 3 h response summary with the preceding 21 h, collection-time only."""
    valid = sorted((r for r in rows if isinstance(r.get('time'), datetime)
                    and r['time'].tzinfo is not None and r['time'].utcoffset() is not None
                    and now - DAILY_REVIEW_WINDOW <= r['time'] <= now), key=lambda r: r['time'])
    recent = [r for r in valid if r['time'] >= now - WINDOW]
    prior = [r for r in valid if r['time'] < now - WINDOW]
    recent_model = _battery_response_model(recent, now)
    prior_model = _battery_response_model(prior, now)
    recent_slope = recent_model['median_voltage_slope_v_per_hour']
    prior_slope = prior_model['median_voltage_slope_v_per_hour']
    return {
        'review_date_utc': now.date().isoformat(),
        'status': 'REVIEWED' if len(valid) >= 2 else 'INSUFFICIENT_DATA',
        'window_seconds': int(DAILY_REVIEW_WINDOW.total_seconds()),
        'collection_count': len(valid), 'recent_3h_collection_count': len(recent),
        'preceding_21h_collection_count': len(prior),
        'effective_collection_window_seconds': int((valid[-1]['time'] - valid[0]['time']).total_seconds())
            if len(valid) > 1 else 0,
        'history_truncated_at_bound': bool(truncated),
        'coverage': 'TRUNCATED_AT_ROW_BOUND' if truncated else 'AVAILABLE_ROWS_WITHIN_24H',
        'recent_3h_response': recent_model, 'preceding_21h_response': prior_model,
        'median_voltage_slope_change_v_per_hour': round(recent_slope - prior_slope, 4)
            if recent_slope is not None and prior_slope is not None else None,
        'comparison_basis': 'COLLECTION_TIME_ONLY; OBSERVATIONAL_CHANGE_NOT_CAUSAL',
        'charging_context_review': _multi_source_charging_context(valid, now),
        'capacity_status': 'CAPACITY_NOT_IDENTIFIABLE',
        'learning_state_reset': False}


def _sampled_voltage_crossings(rows, threshold):
    values = []
    previous_signature = None
    crossings = 0
    previous_side = None
    previous_time = None
    for item in sorted(rows, key=lambda r: r['time']):
        stamp = item['time']
        voltage = valid_battery_voltage(item.get('battery_voltage'))
        if voltage is None:
            # Invalid voltage (incl. 0.0 V) is a barrier: never a boundary side.
            previous_side = None
            previous_time = stamp
            previous_signature = None
            continue
        if previous_time is not None and (stamp - previous_time).total_seconds() > 900:
            previous_side = None
        signature = voltage
        if signature != previous_signature:
            values.append((stamp, voltage))
        side = -1 if voltage < threshold else 1 if voltage > threshold else 0
        if side:
            if previous_side is not None and side != previous_side:
                crossings += 1
            previous_side = side
        previous_signature = signature
        previous_time = stamp
    return crossings, values


def _virtual_threshold_experiment(rows, now, historical_battery_v, battery_response=None):
    """Replay only sampled voltage boundaries; never infer actual switching or safety."""
    raw_radius = os.environ.get('ADAPTIVE_OUTPUT_VIRTUAL_RADIUS_STEPS', '1')
    try:
        radius = int(raw_radius)
    except (TypeError, ValueError):
        radius = -1
    blockers = ['LIVE_INVERTER_THRESHOLDS_UNKNOWN', 'CURRENT_DEVICE_CATALOGUE_NOT_VERIFIED',
        'BMS_AND_MANUFACTURER_LIMITS_UNKNOWN', 'DEVICE_MEASUREMENT_TIME_UNVERIFIED',
        'INVERTER_PRIORITY_AND_SWITCHING_LOGIC_NOT_MODELED', 'BATTERY_EXPOSURE_UNDER_LOAD_NOT_IDENTIFIABLE',
        'TOTAL_BATTERY_CURRENT_UNOBSERVED', 'EXTERNAL_CHARGING_POSSIBLE',
        'CHARGING_RELATIONSHIP_UNVERIFIED']
    base = {'status': 'BLOCKED', 'actionability': 'NON_ACTIONABLE', 'recommendation': None,
        'historical_references': {'back_to_utility_v': HISTORICAL_UTILITY_V,
            'back_to_battery_v': historical_battery_v, 'provenance': 'HISTORICAL_BASELINE; not current settings'},
        'candidate_generation': {'application_increment_v': APPLICATION_THRESHOLD_STEP_V,
            'increment_provenance': 'APPLICATION_ALLOWLIST_ONLY; exact live catalogue value not verified',
            'candidate_radius_steps': radius if 0 <= radius <= 2 else None,
            'utility_application_range_v': list(APPLICATION_UTILITY_RANGE_V),
            'battery_application_range_v': list(APPLICATION_BATTERY_RANGE_V),
            'minimum_pair_gap_v': APPLICATION_THRESHOLD_STEP_V,
            'gap_rule': 'POSITIVE_GAP_OF_ONE_APPLICATION_INCREMENT; not a manufacturer safety limit'},
        'sampled_voltage_provenance': 'COLLECTION_TIME_ONLY; DEVICE_OBSERVATION_TIME_UNVERIFIED',
        'switching_frequency': None, 'potential_excessive_cycling': 'NOT_ESTIMABLE',
        'battery_exposure_under_load': None,
        'limitations': ['Voltage samples are bracketed collection payloads, not verified device measurements.',
            'Voltage threshold crossings are not observed or predicted inverter switches.',
            'PV, AC output load and battery DC energy cannot be equated without validated system flow and losses.'],
        'blockers': blockers, 'candidates': [],
        'comparison_status': 'ALL_CANDIDATES_EXPLORATORY_NO_WINNER'}
    increment = APPLICATION_THRESHOLD_STEP_V
    if radius < 0 or radius > 2:
        base['blockers'] = blockers + ['VIRTUAL_CANDIDATE_CONFIGURATION_INVALID']
        return base
    if historical_battery_v is None:
        base['blockers'] = blockers + ['HISTORICAL_BATTERY_REFERENCE_UNKNOWN']
        return base
    step_position = (historical_battery_v - APPLICATION_BATTERY_RANGE_V[0]) / increment
    if (not APPLICATION_BATTERY_RANGE_V[0] <= historical_battery_v <= APPLICATION_BATTERY_RANGE_V[1]
            or not math.isclose(step_position, round(step_position), abs_tol=1e-9)):
        base['blockers'] = blockers + ['HISTORICAL_BATTERY_REFERENCE_OUTSIDE_APPLICATION_RANGE_OR_INCREMENT']
        return base
    utility_values = sorted({HISTORICAL_UTILITY_V + offset * increment
        for offset in range(-radius, radius + 1)
        if APPLICATION_UTILITY_RANGE_V[0] <= HISTORICAL_UTILITY_V + offset * increment <= APPLICATION_UTILITY_RANGE_V[1]})
    battery_values = sorted({historical_battery_v + offset * increment
        for offset in range(-radius, radius + 1)
        if APPLICATION_BATTERY_RANGE_V[0] <= historical_battery_v + offset * increment <= APPLICATION_BATTERY_RANGE_V[1]})
    pairs = [(utility, battery) for utility in utility_values for battery in battery_values
             if battery - utility >= increment]
    candidates = []
    sampled_values = []
    loaded_voltages = [valid_battery_voltage(row.get('battery_voltage')) for row in rows
                       if (_number(row.get('output_power_w')) is not None and row['output_power_w'] > 0
                           and valid_battery_voltage(row.get('battery_voltage')) is not None)]
    loaded_powers = [_number(row.get('output_power_w')) for row in rows
                     if _number(row.get('output_power_w')) is not None and row['output_power_w'] > 0]
    pv_samples = [(row['time'], _number(row.get('pv_power_w'))) for row in rows
                  if _number(row.get('pv_power_w')) is not None]
    pv_values = [value for _, value in pv_samples]
    pv_directions = []
    for (left_time, left), (right_time, right) in zip(pv_samples, pv_samples[1:]):
        if 0 < (right_time - left_time).total_seconds() <= 900:
            delta = right - left
            if delta:
                pv_directions.append(1 if delta > 0 else -1)
    pv_direction_changes = sum(left != right for left, right in zip(pv_directions, pv_directions[1:]))
    recent_intervals = battery_response.get('recent_intervals', []) if isinstance(battery_response, dict) else []
    recent_recoveries = sum(bool(item.get('voltage_recovery_with_reported_discharge_or_load_reduction'))
                            for item in recent_intervals if isinstance(item, dict))
    recent_slope = battery_response.get('latest_voltage_slope_v_per_hour') if isinstance(battery_response, dict) else None
    for utility, battery in pairs:
        utility_crossings, values = _sampled_voltage_crossings(rows, utility)
        battery_crossings, _ = _sampled_voltage_crossings(rows, battery)
        sampled_values = values
        voltages = [value for _, value in values]
        candidates.append({'back_to_utility_v': utility, 'back_to_battery_v': battery,
            'hysteresis_width_v': round(battery - utility, 3),
            'sampled_utility_boundary_crossings': utility_crossings if len(values) >= 2 else None,
            'sampled_battery_boundary_crossings': battery_crossings if len(values) >= 2 else None,
            'sampled_boundary_crossing_count': (utility_crossings + battery_crossings) if len(values) >= 2 else None,
            'sampled_boundary_crossings_per_hour': round((utility_crossings + battery_crossings) /
                max(1e-9, (values[-1][0] - values[0][0]).total_seconds() / 3600), 3)
                if len(values) >= 2 and values[-1][0] > values[0][0] else None,
            'sampled_voltage_range_v': [min(voltages), max(voltages)] if voltages else None,
            'easun_visible_pv_range_w': [min(pv_values), max(pv_values)] if pv_values else None,
            'easun_visible_pv_direction_changes': pv_direction_changes,
            'pv_interpretation': 'EASUN_VISIBLE_COLLECTION_TREND_ONLY; OTHER_ARRAYS_UNOBSERVED',
            'observed_voltage_at_positive_reported_load_range_v': [min(loaded_voltages), max(loaded_voltages)]
                if loaded_voltages else None,
            'observed_reported_load_range_w': [min(loaded_powers), max(loaded_powers)] if loaded_powers else None,
            'sampled_min_voltage_minus_utility_threshold_v': round(min(loaded_voltages) - utility, 3)
                if loaded_voltages else None,
            'sampled_max_voltage_minus_battery_threshold_v': round(max(loaded_voltages) - battery, 3)
                if loaded_voltages else None,
            'margin_interpretation': 'OBSERVED_SAMPLE_DIFFERENCE_ONLY; NOT A SAFETY OR FUTURE-OPERATION MARGIN',
            'latest_observed_voltage_slope_v_per_hour': recent_slope,
            'recent_load_or_discharge_recovery_associations': recent_recoveries,
            'potential_switching_frequency': None, 'battery_exposure_under_load': None,
            'potential_excessive_cycling': 'NOT_ESTIMABLE; DEVICE_SWITCHING_LOGIC_NOT_MODELED',
            'oscillation_risk': 'NOT_IDENTIFIABLE; sampled boundary-crossing rate is only a qualitative proxy',
            'predicted_benefit': 'NOT_IDENTIFIABLE_FROM_OBSERVATIONAL_REPLAY',
            'predicted_risk': 'UNKNOWN; EXTERNAL CHARGING, DEVICE SWITCHING LOGIC, AND TOTAL CURRENT UNOBSERVED',
            'external_charging_assessment': 'EXTERNAL_CHARGING_POSSIBLE; TOTAL_BATTERY_CURRENT_UNOBSERVED',
            'quality': 'LOW_COLLECTION_TIME_ONLY', 'actionability': 'NON_ACTIONABLE',
            'candidate_status': 'EXPLORATORY_COMPARISON_NO_WINNER',
            'reasons': ['SAMPLED_VOLTAGE_BOUNDARIES_ONLY', 'NO_VALIDATED_WINNER_METHOD'],
            'blockers': list(blockers)})
    base['sampled_voltage_observations'] = len(sampled_values)
    base['latest_sampled_collection_age_seconds'] = int((now - sampled_values[-1][0]).total_seconds()) if sampled_values else None
    if len(sampled_values) < 2:
        base['candidates'] = candidates
        base['status'] = 'BLOCKED_INSUFFICIENT_VOLTAGE_SAMPLES'
        base['blockers'] = blockers + ['INSUFFICIENT_DISTINCT_VOLTAGE_SAMPLES']
        base['comparison_status'] = 'ALL_CANDIDATES_EXPLORATORY_NO_WINNER'
        return base
    candidates.sort(key=lambda c: (c['back_to_utility_v'], c['back_to_battery_v']))
    base['candidates'] = candidates
    base['status'] = 'VIRTUAL_REPLAY_ONLY_BLOCKED_FROM_RECOMMENDATION' if len(candidates) else 'BLOCKED'
    base['comparison_status'] = 'ALL_CANDIDATES_EXPLORATORY_NO_WINNER'
    base['comparison_order'] = 'NUMERIC_THRESHOLD_PAIR_ORDER; NO PREFERENCE'
    return base


def _telemetry_audit():
    """Summarize repository mappings without claiming source-side validation."""
    return {
        'schema': 'inverter_metrics',
        'pv_power_w': {'source_label': 'PV total Power', 'unit_label': 'W', 'domain': 'PV-side report', 'source_semantics_verified': False},
        'output_power_w': {'source_label': 'Output Active Power', 'unit_label': 'W', 'domain': 'AC-output report', 'source_semantics_verified': False},
        'battery_voltage': {'source_label': 'Battery Voltage', 'unit_label': 'V', 'source_semantics_verified': False},
        'battery_current_chg': {'source_label': 'Battery Charging Current', 'unit_label': 'A by application display', 'source_semantics_verified': False},
        'battery_current_dis': {'source_label': 'Battery Discharge Current', 'unit_label': 'A by application display', 'source_semantics_verified': False},
        'battery_soc': {'source_label': 'Battery Capacity', 'application_mapping': 'battery_capacity to battery_soc',
            'unit_label': 'percent by application display', 'soc_accuracy_verified': False},
        'battery_temperature_available': False,
        'external_battery_charging_current_available': False,
        'external_pv_power_available': False,
        'solar_controller_or_orientation_identity_available': False,
        'device_measurement_timestamp_available_in_inverter_metrics': False,
        'collection_timestamp': {'field': 'inverter_metrics.time', 'source': 'datetime.now(UTC) in collector',
            'device_measurement_time': False},
        'easun_timestamp': {'mapping_exists': True, 'semantics_verified': False, 'persisted_to_inverter_metrics': False},
        'pv_ac_load_dc_battery_energy_combined': False,
        'limitations': ['No verified device observation ID or measurement timestamp.',
            'Reported current direction and units are not independently validated.',
            'Other charging controllers and solar orientations are not represented in this schema.',
            'No trusted SOC anchors, chemistry, battery temperature or manufacturer limits.']}


_PENDING_PATTERN = re.compile(r'HYPOTHETICAL_(?:RAISE|LOWER):\d+(?:\.\d+)?')


def _normalize_pending_confirmation(state):
    """Coerce durable pending-confirmation fields; malformed or legacy values never count.

    A count is only meaningful together with the exact candidate it was earned for, so
    an invalid pending key, a non-integer/boolean/out-of-range count, or a count with
    no pending key all collapse to "no pending confirmation".
    """
    pending = state.get('pending')
    count = state.get('stable_count')
    valid_pending = isinstance(pending, str) and _PENDING_PATTERN.fullmatch(pending) is not None
    valid_count = type(count) is int and 0 <= count <= 2
    if not valid_pending or not valid_count or count == 0:
        state['pending'] = pending if valid_pending and valid_count else None
        state['stable_count'] = 0
        if state['pending'] is None:
            state['pending_response_v'] = None
    if _number(state.get('pending_response_v')) is None:
        state['pending_response_v'] = None



def _validated_cooldown(state, now):
    """Return (remaining_seconds, uncertain) without trusting naive/future timestamps.

    A corrupted or unverifiable persisted cooldown must never authorize a
    different candidate as CONFIRMED. No timezone is guessed for naive data.
    """
    raw = state.get('last_change_at')
    if raw is None:
        return (None, True) if state.get('active') else (0, False)
    try:
        if not isinstance(raw, str):
            return None, True
        change = datetime.fromisoformat(raw)
        if change.tzinfo is None or change.utcoffset() is None or change > now:
            return None, True
        return max(0, int(3600 - (now - change).total_seconds())), False
    except (TypeError, ValueError, OverflowError):
        return None, True


def evaluate_collected_history(rows, now, previous=None, *, historical_battery_v=26.0,
                               verified_observations=None, learner=None, daily_review_due=False,
                               daily_review_truncated=False, mode_observations=None,
                               mode_history_truncated=False, mode_run_count=None):
    """Describe stored observations without treating collection time as device time."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('aware evaluation time required')
    now = now.astimezone(timezone.utc)
    valid = [dict(row) for row in rows if isinstance(row.get('time'), datetime)
             and row['time'].tzinfo is not None and row['time'].utcoffset() is not None
             and now - WINDOW <= row['time'] <= now]
    valid.sort(key=lambda row: row['time'])
    raw_count = len(rows)
    # Raw-row bound for the three-hour quantitative window (before duplicate filtering).
    # More in-window rows than the bound means older observations were omitted, both
    # when the SQL query fetched one extra row and when a wider (daily) fetch is reused.
    in_window_rows = len(valid)
    quantitative_truncated = in_window_rows > MAX_ANALYSIS_OBSERVATIONS
    if quantitative_truncated:
        valid = valid[-MAX_ANALYSIS_OBSERVATIONS:]
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
    if (_number(latest.get('battery_voltage')) is not None
            and valid_battery_voltage(latest.get('battery_voltage')) is None):
        reasons.append('INVALID_BATTERY_VOLTAGE')
    if latest.get('working_mode') not in ('Line Mode', 'Invert Mode'):
        reasons.append('WORKING_MODE_UNKNOWN')
    mode_rows = valid if mode_observations is None else [dict(row) for row in mode_observations
        if isinstance(row.get('time'), datetime) and row['time'].tzinfo is not None
        and row['time'].utcoffset() is not None and now - WINDOW <= row['time'] <= now]
    mode_rows.sort(key=lambda row: row['time'])
    if mode_history_truncated:
        reasons.append('MODE_HISTORY_TRUNCATED')
    if mode_observations is None:
        # In-memory rows are the raw collection stream; never mutate the caller's/valid rows.
        mode_rows = [dict(row) for row in mode_rows]
    changes, mode_continuity = _count_supported_mode_changes(mode_rows)
    if mode_continuity['gap_count']:
        reasons.append('MODE_HISTORY_OBSERVATION_GAP')
    complete_latest = (latest.get('working_mode') in ('Line Mode', 'Invert Mode') and
        all(_number(latest.get(k)) is not None and latest[k] >= 0 for k in
            ('battery_voltage', 'battery_current_chg', 'battery_current_dis', 'pv_power_w', 'output_power_w')) and
        valid_battery_voltage(latest.get('battery_voltage')) is not None and
        not (latest.get('battery_current_chg', 0) > 0 and latest.get('battery_current_dis', 0) > 0))
    analysed, funnel = _prepare_observations(valid)
    battery_model = _battery_response_model(valid, now)
    charging_context = _multi_source_charging_context(valid, now)
    funnel = {'raw_collections': raw_count,
              'rejected_outside_window_or_invalid_time': raw_count - in_window_rows,
              'rejected_raw_row_bound': in_window_rows - len(valid), **funnel}
    window_start = now - WINDOW
    quantitative_coverage = {
        'raw_row_limit': MAX_ANALYSIS_OBSERVATIONS,
        'in_window_rows_seen': in_window_rows,
        'in_window_rows_seen_is_lower_bound': quantitative_truncated,
        'raw_rows_used': len(valid),
        'raw_row_truncated': quantitative_truncated,
        'older_observations_omitted': quantitative_truncated,
        'analysis_cap_after_duplicate_filtering': MAX_ANALYSIS_OBSERVATIONS,
        'analysis_cap_rejected': funnel['rejected_analysis_cap'],
        'window_start': window_start.isoformat(),
        'covered_from': valid[0]['time'].isoformat() if valid else None,
        'uncovered_window_seconds': (int((valid[0]['time'] - window_start).total_seconds())
                                     if quantitative_truncated and valid else None),
        'coverage': ('NO_ROWS' if not valid else 'PARTIAL_WINDOW_RAW_ROW_BOUND' if quantitative_truncated
                     else 'ALL_STORED_ROWS_IN_WINDOW'),
        'interpretation': ('Older observations in the three-hour window were omitted by the raw-row bound; '
                           'quantitative results describe only the retained recent suffix.'
                           if quantitative_truncated else
                           'All stored in-window rows were read; stored rows may still be cached or sparse.')}
    if quantitative_truncated:
        reasons.append('QUANTITATIVE_HISTORY_TRUNCATED')
    cycling = _reported_cycling(mode_rows, now)
    fast_context = _fast_context(valid, now)
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
    if daily_review_due:
        daily_review = _daily_behavior_review(rows, now, truncated=daily_review_truncated)
        state['daily_review_date_utc'] = now.date().isoformat()
        state['daily_behavior_review'] = daily_review
    else:
        daily_review = state.get('daily_behavior_review')
        if not isinstance(daily_review, dict):
            daily_review = {'status': 'WAITING_FOR_DAILY_REVIEW',
                'window_seconds': int(DAILY_REVIEW_WINDOW.total_seconds()),
                'capacity_status': 'CAPACITY_NOT_IDENTIFIABLE', 'learning_state_reset': False}
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
    prior_signatures = state.get('confirmation_signatures', [])
    prior_signatures = prior_signatures[-32:] if isinstance(prior_signatures, list) else []
    # Carry the legacy single signature forward so upgrades cannot count its replay.
    if state.get('last_sample_signature') is not None and state['last_sample_signature'] not in prior_signatures:
        prior_signatures.append(state['last_sample_signature'])
    signature_key = json.dumps(signature, separators=(',', ':'), default=str) if signature is not None else None
    known_signature_keys = {json.dumps(value, separators=(',', ':'), default=str) for value in prior_signatures}
    # The current rolling history is another bounded source of replay protection:
    # a cached payload must not become "new" merely because its durable 32-signature
    # window has rolled forward while the original collection is still in this window.
    prior_window_signature_keys = {
        json.dumps(list(_observation_signature(row)), separators=(',', ':'), default=str)
        for row in valid[:-1]
    }
    new_sample = bool(collection_time and collection_time != last_time
                      and signature_key not in known_signature_keys
                      and signature_key not in prior_window_signature_keys)
    pending = (candidate[0] + ':' + str(candidate[1])) if candidate else None
    _normalize_pending_confirmation(state)
    if not new_sample and state['pending'] is not None and state['pending'] != pending:
        # A different (or no) candidate contradicts the pending one. Its confirmations
        # never transfer, and without a new accepted sample none can be earned here.
        state['stable_count'] = 0
        state['pending'] = None
        state['pending_response_v'] = None
    if new_sample:
        direction_compatible = (exploratory.get('latest_raise_compatible', False)
                                if candidate and candidate[0] == 'HYPOTHETICAL_RAISE'
                                else exploratory.get('latest_lower_compatible', False) if candidate else False)
        prior_response = (_number(state.get('pending_response_v'))
                          if state['pending'] == pending else None)
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
        if signature is not None:
            prior_signatures.append(signature)
            state['confirmation_signatures'] = prior_signatures[-32:]
    decision = 'HOLD' if exploratory else 'INSUFFICIENT_DATA'
    proposed = None
    # Confirmation progress belongs only to the exact pending candidate.
    candidate_count = state['stable_count'] if candidate and state['pending'] == pending else 0
    stability = 'NO_CANDIDATE' if not candidate else 'AWAITING_CONFIRMATION'
    if candidate and candidate_count == 0:
        stability = 'AWAITING_COMPATIBLE_OBSERVATION'
    cooldown_remaining, uncertain_cooldown = _validated_cooldown(state, now)
    if uncertain_cooldown:
        reasons.append('COOLDOWN_STATE_UNVERIFIED')
    if candidate and candidate_count >= 2:
        if state.get('active') == pending:
            stability = 'UNCHANGED'
        elif uncertain_cooldown:
            # Never convert malformed state to an expired cooldown.
            stability = 'COOLDOWN'
        elif cooldown_remaining > 0:
            stability = 'COOLDOWN'
            reasons.append('DRY_RUN_COOLDOWN')
        else:
            decision, proposed, stability = candidate[0], candidate[1], 'CONFIRMED'
            state['active'], state['last_change_at'] = pending, now.isoformat()
            cooldown_remaining = 3600
    if candidate and stability in ('AWAITING_CONFIRMATION', 'AWAITING_COMPATIBLE_OBSERVATION'):
        reasons.append('PROPOSAL_AWAITING_COMPATIBLE_CONFIRMATION')
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
    diagnostics['fast_context'] = fast_context
    diagnostics['battery_response'] = battery_model
    virtual_experiment = _virtual_threshold_experiment(valid, now, baseline, battery_model)
    hysteresis_widths = sorted({item['hysteresis_width_v'] for item in virtual_experiment['candidates']})
    adaptive_hysteresis = {
        'status': 'INSUFFICIENT_SAFETY_QUALIFIED_EVIDENCE; RECOMMENDATION_BLOCKED',
        'current_live_hysteresis_v': None,
        'historical_reference_width_v': round(baseline - HISTORICAL_UTILITY_V, 3)
            if baseline is not None else None,
        'virtual_hysteresis_widths_v': hysteresis_widths,
        'evidence_review': {'reported_mode_changes': changes,
            'reported_short_runs': cycling.get('short_runs'),
            'median_battery_voltage_slope_v_per_hour': battery_model.get('median_voltage_slope_v_per_hour'),
            'battery_capacity_status': battery_model.get('capacity_status'),
            'fast_pv_range_w': fast_context.get('pv_range_w'),
            'fast_load_range_w': fast_context.get('load_range_w'),
            'interpretation': 'COLLECTION_TIME_DESCRIPTION_ONLY; cannot establish a safe direction'},
        'blockers': virtual_experiment['blockers'],
        'actionability': 'NON_ACTIONABLE'}
    diagnostics['virtual_threshold_experiment'] = virtual_experiment
    diagnostics['adaptive_hysteresis'] = adaptive_hysteresis
    diagnostics['telemetry_audit'] = _telemetry_audit()
    diagnostics['long_context'] = {'window_seconds': int(WINDOW.total_seconds()),
        'collection_count': funnel['analysed_observations'],
        'freshness_basis': ('TRUNCATED_RECENT_SUFFIX_OF_THREE_HOUR_HISTORY' if quantitative_truncated
                            else 'THREE_HOUR_SUPPORTING_HISTORY'),
        'history_truncated': quantitative_truncated}
    diagnostics['quantitative_history_coverage'] = quantitative_coverage
    diagnostics['daily_behavior_review'] = daily_review
    from_sql = mode_observations is not None
    retained_runs = ({row.get('mode_run') for row in mode_rows} - {None}) if from_sql else set()
    selected_runs = len(retained_runs) if from_sql else None
    total_runs = mode_run_count if from_sql and mode_run_count is not None else None
    suffix_cut = bool(mode_history_truncated) or (not from_sql and quantitative_truncated)
    unknown_barriers = sum(1 for row in mode_rows if row.get('working_mode') not in MODES)
    continuity_breaks = []
    if suffix_cut:
        continuity_breaks.append('DISCONTINUITY_BEFORE_RETAINED_FROM')
    if mode_continuity['gap_count']:
        continuity_breaks.append('INTERRUPTED_BY_OBSERVATION_GAP')
    if unknown_barriers:
        continuity_breaks.append('INTERRUPTED_BY_UNKNOWN_MODE')
    diagnostics['mode_history_coverage'] = {
        'source': 'POSTGRES_MODE_RUN_BOUNDARIES' if from_sql else 'IN_MEMORY_COLLECTION_ROWS',
        'total_mode_run_count': total_runs,
        'selected_mode_run_count': selected_runs,
        'selected_boundary_rows': len(mode_rows),
        'reported_mode_changes': changes,
        'truncated': bool(mode_history_truncated),
        'limit_runs': MAX_MODE_RUNS if from_sql else None,
        'omitted_older_runs': (total_runs - selected_runs
                               if total_runs is not None and selected_runs is not None else None),
        'run_segmentation': 'MODE_CHANGE_OR_UNKNOWN_OR_GAP_ABOVE_LIMIT',
        'selection': ('CONTIGUOUS_RECENT_SUFFIX' if from_sql
                      else 'QUANTITATIVE_ROWS' + ('_TRUNCATED_SUFFIX' if quantitative_truncated else '')),
        'retained_from': mode_rows[0]['time'].isoformat() if mode_rows else None,
        'continuity_status': 'INTERRUPTED' if continuity_breaks else 'CONTINUOUS_WITHIN_WINDOW',
        'continuity_breaks': continuity_breaks,
        'continuity': ('; '.join(continuity_breaks) + '; no transition counted across any break'
                       if continuity_breaks else 'CONTINUOUS_WITHIN_WINDOW'),
        'observation_gap_limit_s': int(COLLECTION_GAP_LIMIT.total_seconds()),
        'observation_gaps': mode_continuity['gap_count'],
        'max_observation_gap_s': mode_continuity['max_gap_s'],
        'recent_observation_gaps': mode_continuity['gaps'][-3:],
        'uncertain_mode_changes_across_gaps': len(mode_continuity['uncertain_changes']),
        'recent_uncertain_mode_changes': mode_continuity['uncertain_changes'][-3:],
        'unknown_mode_barrier_rows': unknown_barriers,
        'time_provenance': 'COLLECTION_ORDER_ONLY; physical switch times unknown'}
    for reason in hold_reasons:
        if reason not in reasons:
            reasons.append(reason)
    explanation = ('Collection-time research only: mode/charging patterns are uncertain, timestamps may reflect cached readings, baseline is historical, and BMS limits and current settings are unverified. Candidate status: ' + stability if exploratory else 'Learning and threshold recommendations are blocked: device measurement time, receipt provenance, approved battery limits and current settings are not established.')
    explanation += (' Monitored threshold: Back to Battery; verified live setting: UNKNOWN; historical analytical references: '
                    'Back to Utility 25.5 V and Back to Battery %s V (HISTORICAL_BASELINE only).' %
                    ('unknown' if baseline is None else str(baseline)))
    if candidate:
        explanation += ' Candidate is relative to the historical baseline, not an observed inverter setting.'
    if fast_context['latest_collection_age_seconds'] is not None:
        explanation += ' Fast context covers the latest 20 minutes; newest collection is %s seconds old.' % fast_context['latest_collection_age_seconds']
    if changed:
        explanation += ' Empirical response changed; cause is unknown and this is not proof that batteries were added or changed.'
    if quantitative_truncated:
        explanation += (' Quantitative history is truncated: only the newest %d raw rows were analysed; '
                        'older observations in the three-hour window were omitted.' % MAX_ANALYSIS_OBSERVATIONS)
    if mode_history_truncated:
        explanation += ' Mode history is truncated to the newest %d runs; earlier transitions are not counted.' % MAX_MODE_RUNS
    explanation += ' ' + diagnostics.pop('summary')
    outcome = json.dumps({'baseline': baseline, 'decision': decision, 'proposed_voltage': proposed,
                          'stability': stability, 'reason_codes': reasons}, sort_keys=True,
                         separators=(',', ':'), allow_nan=False)
    ident = hashlib.sha256((VERSION + ':' + key + ':' + input_digest + ':' + outcome).encode()).hexdigest()
    return {
        'evaluation_id': ident, 'evaluated_at': now.isoformat(), 'algorithm_version': VERSION,
        'decision': decision, 'threshold': 'battery' if candidate else None, 'proposed_voltage': proposed,
        'monitored_threshold': {'key': 'battery', 'label': 'Back to Battery'},
        'current_settings': {'utility': None, 'battery': None, 'provenance': 'unknown'},
        'historical_settings': {'utility': 25.5, 'battery': baseline,
                                'provenance': 'HISTORICAL_BASELINE; not verified current settings'},
        'telemetry_audit': _telemetry_audit(),
        'daily_behavior_review': daily_review,
        'virtual_threshold_experiment': virtual_experiment,
        'adaptive_hysteresis': adaptive_hysteresis,
        'candidate_status': stability,
        'observation_time': None, 'observation_age_seconds': None, 'receipt_time': None,
        'collection_time': collected.isoformat() if collected else None,
        'mode': latest.get('working_mode') if latest.get('working_mode') in ('Line Mode', 'Invert Mode') else None,
        'pv_w': _number(latest.get('pv_power_w')),
        'load_w': _number(latest.get('output_power_w')), **battery_voltage_presentation(latest.get('battery_voltage')),
        'charge_a': _number(latest.get('battery_current_chg')), 'discharge_a': _number(latest.get('battery_current_dis')),
        'soc': _number(latest.get('battery_soc')), 'soc_provenance': 'reported; not verified SOC',
        'recent_reported_mode_changes': changes, 'transitions_provenance': 'collection order; not measurement time',
        'short_cycles': None, 'sag_estimate_v': None, 'rebound_estimate_v': None,
        'window_seconds': int(WINDOW.total_seconds()), 'effective_window_seconds': max(0, effective_window),
        'collection_window_seconds': max(0, collection_window),
        'collection_count': len(valid),
        'quantitative_history_truncated': quantitative_truncated,
        'quantitative_history_coverage': quantitative_coverage,
        'unique_observation_count': exploratory.get('samples', 0) if exploratory else 0,
        'confidence': overall_confidence, 'confidence_breakdown': confidence_breakdown,
        'decision_diagnostics': diagnostics,
        'battery_response': battery_model,
        'charging_context': charging_context,
        'time_provenance': 'COLLECTION_TIME_ONLY', 'evidence_level': 'EXPLORATORY', 'exploratory_voltage_contrast': exploratory,
        'verified_sag': verified,
        'proposal_state': state, 'stability': stability, 'candidate_voltage': candidate[1] if candidate else None,
        'actionability': 'NON_ACTIONABLE', 'confirmation_progress': {'confirmed': state.get('stable_count', 0), 'required': 2},
        'cooldown_remaining_seconds': cooldown_remaining,
        'reason_codes': reasons, 'safety_blockers': reasons,
        'explanation': explanation,
        'hardware_write_attempted': False,
    }


async def _fetch_mode_run_boundaries(conn, lower, upper, max_runs=MAX_MODE_RUNS):
    """Read first/last samples for a bounded, contiguous suffix of recent mode runs.

    This compact query keeps mode-transition evidence from being displaced by cached
    raw rows in the separate 720-row quantitative history query. Runs are numbered over
    the full window (unknown/NULL modes form their own runs and stay barriers), then
    only the newest ``max_runs`` consecutive runs are returned. No older run is kept
    alongside them, so two retained records are never separated by omitted runs and
    no transition can be inferred across the truncation boundary.
    """
    rows = await conn.fetch('''
        WITH mode_history AS (
            SELECT time, working_mode, battery_voltage, battery_soc, battery_current_chg,
                   battery_current_dis, pv_power_w, output_power_w,
                   LAG(working_mode) OVER (ORDER BY time) AS previous_mode,
                   LAG(time) OVER (ORDER BY time) AS previous_collection_time
            FROM inverter_metrics
            WHERE time >= $1 AND time <= $2
        ), numbered AS (
            -- A new run starts on a mode change (NULL/unknown included) and on any
            -- collection gap above COLLECTION_GAP_LIMIT, so runs never span a gap.
            SELECT *, SUM(CASE WHEN working_mode IS DISTINCT FROM previous_mode
                                 OR previous_collection_time IS NULL
                                 OR time - previous_collection_time > $4 THEN 1 ELSE 0 END)
                OVER (ORDER BY time ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS mode_run
            FROM mode_history
        ), ranked_samples AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY mode_run ORDER BY time) AS first_in_run,
                      ROW_NUMBER() OVER (PARTITION BY mode_run ORDER BY time DESC) AS last_in_run
            FROM numbered
        ), mode_runs AS (
            SELECT mode_run, MIN(time) AS first_time, MAX(time) AS last_time
            FROM numbered GROUP BY mode_run
        ), ranked_runs AS (
            SELECT *, ROW_NUMBER() OVER (ORDER BY last_time DESC) AS run_rank,
                      COUNT(*) OVER () AS total_runs
            FROM mode_runs
        ), selected_runs AS (
            SELECT * FROM ranked_runs WHERE run_rank <= $3
        )
        SELECT samples.time, samples.working_mode, samples.battery_voltage, samples.battery_soc,
               samples.battery_current_chg, samples.battery_current_dis, samples.pv_power_w,
               samples.output_power_w, samples.previous_collection_time, samples.mode_run,
               selected.total_runs
        FROM ranked_samples AS samples
        JOIN selected_runs AS selected USING (mode_run)
        WHERE samples.first_in_run = 1 OR samples.last_in_run = 1
        ORDER BY samples.time
    ''', lower, upper, max_runs, COLLECTION_GAP_LIMIT, timeout=10)
    total_runs = int(rows[0]['total_runs']) if rows else 0
    mode_rows = [{key: row[key] for key in row.keys() if key != 'total_runs'} for row in rows]
    return mode_rows, total_runs, total_runs > max_runs


async def run_evaluation(pool, now, *, verified_observations=None, learner=None, historical_battery_v=26.0):
    """One idempotent transaction shared by cooperating replicas."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval('SELECT pg_try_advisory_xact_lock($1)', LOCK_ID):
                return False
            previous_json = await conn.fetchval('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1')
            previous = json.loads(previous_json) if previous_json else None
            if (not isinstance(previous, dict) or
                    previous.get('algorithm_version') not in STATE_COMPATIBLE_VERSIONS or
                    not isinstance(previous.get('proposal_state'), dict)):
                # Legacy failure rows contain no proposal state; recover the last
                # committed learning state instead of resetting cooldown/confirmation.
                saved_json = await conn.fetchval('''SELECT entry FROM adaptive_decision_journal
                    WHERE entry ? 'proposal_state' AND entry->>'algorithm_version' = ANY($1::text[])
                    ORDER BY evaluated_at DESC LIMIT 1''', list(STATE_COMPATIBLE_VERSIONS))
                previous = json.loads(saved_json) if saved_json else None
            prior_state = previous.get('proposal_state') if isinstance(previous, dict) else None
            daily_review_due = not isinstance(prior_state, dict) or prior_state.get('daily_review_date_utc') != now.date().isoformat()
            history_window = DAILY_REVIEW_WINDOW if daily_review_due else WINDOW
            # One extra row in both paths makes truncation observable rather than silent;
            # evaluate_collected_history keeps only the newest MAX_ANALYSIS_OBSERVATIONS
            # in-window rows and reports when older ones were omitted.
            history_limit = (MAX_DAILY_REVIEW_OBSERVATIONS + 1
                             if daily_review_due else MAX_ANALYSIS_OBSERVATIONS + 1)
            rows = await conn.fetch('''SELECT time, working_mode, battery_voltage, battery_soc,
                battery_current_chg, battery_current_dis, pv_power_w, output_power_w
                FROM inverter_metrics WHERE time >= $1 AND time <= $2 ORDER BY time DESC LIMIT $3''',
                now - history_window, now, history_limit)
            rows = list(reversed(rows))
            daily_review_truncated = daily_review_due and len(rows) > MAX_DAILY_REVIEW_OBSERVATIONS
            if daily_review_truncated:
                rows = rows[-MAX_DAILY_REVIEW_OBSERVATIONS:]
            mode_rows, mode_run_count, mode_history_truncated = await _fetch_mode_run_boundaries(
                conn, now - WINDOW, now)
            entry = evaluate_collected_history(rows, now, previous,
                                               historical_battery_v=historical_battery_v,
                                               verified_observations=verified_observations, learner=learner,
                                               daily_review_due=daily_review_due,
                                               daily_review_truncated=daily_review_truncated,
                                               mode_observations=mode_rows,
                                               mode_history_truncated=mode_history_truncated,
                                               mode_run_count=mode_run_count)
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
            # Carry forward the last compatible durable proposal state, even if
            # the newest entry is itself a failure (or was written by old v1 code).
            saved_json = await conn.fetchval('''SELECT entry FROM adaptive_decision_journal
                WHERE entry ? 'proposal_state' AND entry->>'algorithm_version' = ANY($1::text[])
                ORDER BY evaluated_at DESC LIMIT 1''', list(STATE_COMPATIBLE_VERSIONS))
            saved = json.loads(saved_json) if saved_json else None
            saved_state = (saved.get('proposal_state') if isinstance(saved, dict)
                           and saved.get('algorithm_version') in STATE_COMPATIBLE_VERSIONS
                           and isinstance(saved.get('proposal_state'), dict) else None)
            if not last_success and isinstance(saved, dict):
                last_success = saved.get('evaluated_at')
            entry = {
                'evaluation_id': hashlib.sha256(f'{VERSION}:worker-error:{now.isoformat()}:{safe_error}'.encode()).hexdigest(),
                'evaluated_at': now.isoformat(), 'algorithm_version': VERSION, 'decision': 'BLOCKED',
                'evidence_level': 'EXPLORATORY', 'time_provenance': 'COLLECTION_TIME_ONLY',
                'confidence': 0.0, 'reason_codes': ['WORKER_EVALUATION_FAILED'],
                'explanation': 'The learning worker could not complete this evaluation.',
                'hardware_write_attempted': False,
                # Failure diagnostics must not become an empty proposal state.
                **({'proposal_state': dict(saved_state)} if saved_state is not None else {}),
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
