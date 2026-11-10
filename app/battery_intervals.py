"""Conservative, pure EASUN reported-channel integration for worker and grids.

Collection timestamps do not prove device observation time. Exact consecutive
battery payload repeats can be cached, so neither adjacent interval is counted
as observed throughput. Missing/invalid rows are *barriers*, never skipped over.
These results are EASUN-channel proxies, NOT total battery capacity/energy.
"""
from datetime import datetime
import math

MAX_INTERVAL_SECONDS = 900
MIN_SLOPE_SECONDS = 60
_FIELDS = ('battery_voltage', 'battery_current_chg', 'battery_current_dis')
_REASONS = ('NON_POSITIVE_INTERVAL', 'GAP_OVER_15M', 'CONFLICTING_CURRENT',
            'MISSING_CURRENT', 'MISSING_VOLTAGE', 'INVALID_NEGATIVE_MEASUREMENT',
            'INVALID_ZERO_VOLTAGE', 'AMBIGUOUS_DUPLICATE_TIMESTAMP', 'CACHED_REPEAT', 'INVALID_COLLECTION_TIME')


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def valid_battery_voltage(value):
    """Return a battery voltage usable for physical inference, else None.

    The single shared rule for every battery-response consumer (worker,
    grids, fast/multi-source context, virtual threshold replay): a reading
    must be a finite real number strictly above 0 V. 0.0 V, negative,
    NaN/inf, bool, str and None are not battery measurements; the raw value
    may still be shown as telemetry, but it never feeds slopes, Wh,
    coverage or threshold crossings.
    """
    voltage = number(value)
    return voltage if voltage is not None and voltage > 0 else None


BATTERY_VOLTAGE_STATUSES = ('VALID', 'MISSING', 'INVALID_ZERO_VOLTAGE', 'INVALID_NEGATIVE_VOLTAGE',
                            'INVALID_NON_FINITE_VOLTAGE', 'INVALID_NON_NUMERIC_VOLTAGE')
_RAW_TEXT_LIMIT = 64


def battery_voltage_status(value):
    """Classify a reported battery voltage with the same rule as valid_battery_voltage."""
    if value is None:
        return 'MISSING'
    if type(value) not in (int, float):
        return 'INVALID_NON_NUMERIC_VOLTAGE'
    if not math.isfinite(value):
        return 'INVALID_NON_FINITE_VOLTAGE'
    if value == 0:
        return 'INVALID_ZERO_VOLTAGE'
    if value < 0:
        return 'INVALID_NEGATIVE_VOLTAGE'
    return 'VALID'


def json_safe_raw(value):
    """Raw telemetry as strict-JSON data (NaN/inf as text) for diagnostics only."""
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        if math.isnan(value):
            return 'NaN'
        if math.isinf(value):
            return 'Infinity' if value > 0 else '-Infinity'
        return value
    text = value if isinstance(value, str) else repr(value)
    return text[:_RAW_TEXT_LIMIT]


def battery_voltage_presentation(value):
    """Separate the inference-valid voltage from the raw reported value.

    ``battery_v`` is the valid measurement or None (never a substituted 0);
    ``battery_v_reported_raw`` keeps what was received, for diagnostics only;
    ``battery_v_status`` says why the raw value is or is not a measurement.
    """
    return {'battery_v': valid_battery_voltage(value),
            'battery_v_reported_raw': json_safe_raw(value),
            'battery_v_status': battery_voltage_status(value)}


def battery_signature(row):
    return tuple(number(row.get(key)) for key in _FIELDS)


def row_barrier(row):
    voltage = number(row.get('battery_voltage'))
    charge = number(row.get('battery_current_chg'))
    discharge = number(row.get('battery_current_dis'))
    if charge is None or discharge is None:
        return 'MISSING_CURRENT'
    if voltage is None:
        return 'MISSING_VOLTAGE'
    if voltage < 0 or charge < 0 or discharge < 0:
        return 'INVALID_NEGATIVE_MEASUREMENT'
    if valid_battery_voltage(voltage) is None:
        # 0.0 V is physically meaningless for a connected bank: a barrier,
        # never usable evidence (and never bridged as merely missing).
        return 'INVALID_ZERO_VOLTAGE'
    if charge > 0 and discharge > 0:
        return 'CONFLICTING_CURRENT'
    return None


def aware_time(row):
    stamp = row.get('time')
    if (not isinstance(stamp, datetime) or stamp.tzinfo is None
            or stamp.utcoffset() is None):
        return None
    return stamp


def summarize_intervals(intervals):
    """Summarize a contiguous slice of prevalidated, globally classified pairs."""
    used = [item for item in intervals if item['reason'] is None]
    reasons = sorted({item['reason'] for item in intervals if item['reason']})
    covered = sum(item['seconds'] for item in used)
    elapsed = sum(max(0.0, item['seconds'] or 0) for item in intervals)
    complete = bool(used) and not reasons and elapsed > 0 and abs(covered - elapsed) <= 1e-6
    totals = {key: sum(item.get(key, 0.0) for item in used)
              for key in ('charge_ah', 'discharge_ah', 'charge_wh', 'discharge_wh')}
    return {'covered_seconds': round(covered, 3), 'elapsed_seconds': round(elapsed, 3),
            'coverage_ratio': round(covered / elapsed, 4) if elapsed > 0 else None,
            'reasons': reasons, 'status': ('COMPLETE' if complete else
                                         'PARTIAL_COVERAGE; THROUGHPUT_NOT_REPORTED' if used else 'NOT_INTEGRATED'),
            **{key: round(value, 5 if key.endswith('ah') else 4) if complete else None
               for key, value in totals.items()}}


def calculate_battery_intervals(rows):
    """Analyze *ordered* raw collection rows, earliest first; do not reorder here.

    Each raw neighboring pair is validated. Ambiguous timestamps, missing
    measurements and cached repeats interrupt integration, including the
    interval immediately following an uncertain cached payload.
    """
    ordered = list(rows)
    totals = {'charge_ah': 0.0, 'discharge_ah': 0.0,
              'charge_wh': 0.0, 'discharge_wh': 0.0}
    rejected = {key: 0 for key in _REASONS}
    excluded_seconds = {key: 0.0 for key in _REASONS}
    excluded_observations = {'MISSING_CURRENT': 0, 'MISSING_VOLTAGE': 0,
                             'INVALID_NEGATIVE_MEASUREMENT': 0, 'INVALID_ZERO_VOLTAGE': 0,
                             'CONFLICTING_CURRENT': 0,
                             'INVALID_COLLECTION_TIME': 0}
    stamps = [aware_time(row) for row in ordered]
    barriers = [row_barrier(row) for row in ordered]
    for idx, (stamp, reason) in enumerate(zip(stamps, barriers)):
        if stamp is None:
            excluded_observations['INVALID_COLLECTION_TIME'] += 1
        elif reason:
            excluded_observations[reason] += 1
    signatures = [battery_signature(row) for row in ordered]
    cached = [False] * len(ordered)
    ambiguous = [False] * len(ordered)
    for i in range(1, len(ordered)):
        if stamps[i] is not None and stamps[i - 1] is not None and stamps[i] == stamps[i - 1]:
            ambiguous[i - 1] = ambiguous[i] = True
        if signatures[i] == signatures[i - 1] and stamps[i] is not None and stamps[i - 1] is not None:
            cached[i] = True
    intervals = []
    covered = 0.0
    for i, (older, newer) in enumerate(zip(ordered, ordered[1:])):
        seconds = ((stamps[i + 1] - stamps[i]).total_seconds()
                   if stamps[i] is not None and stamps[i + 1] is not None else None)
        if seconds is None:
            reason = 'INVALID_COLLECTION_TIME'
        elif seconds <= 0:
            reason = 'NON_POSITIVE_INTERVAL'
        elif barriers[i] or barriers[i + 1]:
            reason = barriers[i] or barriers[i + 1]
        elif ambiguous[i] or ambiguous[i + 1]:
            reason = 'AMBIGUOUS_DUPLICATE_TIMESTAMP'
        elif seconds > MAX_INTERVAL_SECONDS:
            reason = 'GAP_OVER_15M'
        elif cached[i] or cached[i + 1]:
            reason = 'CACHED_REPEAT'
        else:
            reason = None
        result = {'older': older, 'newer': newer, 'seconds': seconds,
                  'reason': reason, 'voltage_slope_v_per_hour': None}
        if reason:
            rejected[reason] += 1
            if seconds is not None and seconds > 0:
                excluded_seconds[reason] += seconds
        else:
            hours = seconds / 3600.0
            cv0, cv1 = older['battery_current_chg'], newer['battery_current_chg']
            dv0, dv1 = older['battery_current_dis'], newer['battery_current_dis']
            v0, v1 = older['battery_voltage'], newer['battery_voltage']
            values = {
                'charge_ah': (cv0 + cv1) / 2 * hours,
                'discharge_ah': (dv0 + dv1) / 2 * hours,
                'charge_wh': (cv0 * v0 + cv1 * v1) / 2 * hours,
                'discharge_wh': (dv0 * v0 + dv1 * v1) / 2 * hours,
            }
            for key, value in values.items():
                totals[key] += value
            covered += seconds
            result.update(values)
            result['voltage_slope_v_per_hour'] = ((v1 - v0) / hours
                if seconds >= MIN_SLOPE_SECONDS else None)
        intervals.append(result)
    elapsed = ((stamps[-1] - stamps[0]).total_seconds()
               if len(stamps) > 1 and stamps[0] is not None and stamps[-1] is not None
               else 0.0)
    elapsed = max(0.0, elapsed)
    successful = [i for i in intervals if i['reason'] is None]
    complete = (bool(successful) and not any(rejected.values())
                and not any(excluded_observations.values())
                and abs(covered - elapsed) <= 1e-6 and elapsed > 0)
    status = 'COMPLETE' if complete else 'PARTIAL' if successful else 'NONE'
    return {'intervals': intervals, 'successful': successful, 'totals': totals,
            'covered_seconds': covered, 'elapsed_seconds': elapsed,
            'excluded_seconds': excluded_seconds, 'rejected': rejected,
            'excluded_observations': excluded_observations,
            'coverage_ratio': covered / elapsed if elapsed > 0 else None,
            'status': status, 'complete': complete,
            'valid_observations': sum(stamp is not None and reason is None
                                      for stamp, reason in zip(stamps, barriers)),
            'cached_observations': sum(cached)}
