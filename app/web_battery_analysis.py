"""Pure response shaping for the authenticated battery observation grids.

All times remain collection times. EASUN current fields are reported channels,
not asserted to be total battery current; externally supplied current is unknown.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from app.battery_intervals import (calculate_battery_intervals, summarize_intervals,
                                   valid_battery_voltage, MIN_SLOPE_SECONDS, MAX_INTERVAL_SECONDS)


_PAYLOAD_FIELDS = (
    'working_mode', 'battery_voltage', 'battery_soc', 'battery_current_chg',
    'battery_current_dis', 'pv_power_w', 'output_power_w',
)
_BATTERY_FIELDS = ('battery_voltage', 'battery_current_chg', 'battery_current_dis')
# Matches online_output_learning.MIN_SLOPE_INTERVAL_S: closer collections (the
# collector polls every 10 s while switching) are not independent slope evidence.
MIN_SLOPE_INTERVAL_S = MIN_SLOPE_SECONDS
MAX_INTEGRATION_INTERVAL_S = MAX_INTERVAL_SECONDS
# Every visible row is derived from itself plus at most this many immediately
# older stored rows -- never more, whatever the page size -- so a stored
# observation yields the same derived values at limit=1, 2, 3, 20 or on any
# cursor page. The route fetches exactly this many context rows below each
# page (ignoring the range lower bound) and never renders them. One row of the
# window is reserved to classify the oldest interval (cached repeat /
# ambiguous time needs that interval's own predecessor), so a previous valid
# voltage is searched for in at most GRID_CONTEXT_ROWS - 1 older rows.
GRID_CONTEXT_ROWS = 8


def _integrate_span(span):
    """Compatibility wrapper around the shared worker/grid integration policy.

    ``span`` is newest first and is already bounded by its caller. A cached
    boundary requires wider page context, handled in build_battery_grid below.
    """
    result = calculate_battery_intervals(list(reversed(span)))
    return summarize_intervals(result['intervals'])


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _signature(row, fields):
    return tuple(_number(row.get(key)) if key != 'working_mode' else row.get(key) for key in fields)


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else None


def _charging_status(row):
    charge = _number(row.get('battery_current_chg'))
    discharge = _number(row.get('battery_current_dis'))
    if charge is not None and discharge is not None and charge > 0 and discharge > 0:
        reported = 'EASUN_CHARGE_AND_DISCHARGE_CHANNELS_BOTH_NONZERO; SEMANTICS_UNVERIFIED'
    elif charge is not None and charge > 0:
        reported = 'EASUN_CHARGING_CURRENT_REPORTED'
    elif discharge is not None and discharge > 0:
        reported = 'EASUN_DISCHARGING_CURRENT_REPORTED'
    elif charge == 0 and discharge == 0:
        reported = 'NO_EASUN_CURRENT_REPORTED'
    else:
        reported = 'EASUN_CURRENT_UNKNOWN'
    return reported + '; EXTERNAL_CHARGING_POSSIBLE; TOTAL_BATTERY_CURRENT_UNOBSERVED'


def _freshness(now, stamp):
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        return 'COLLECTION_TIME_INVALID; DEVICE_FRESHNESS_UNVERIFIED'
    age = max(0, int((now - stamp.astimezone(timezone.utc)).total_seconds()))
    return ('COLLECTION_RECENT' if age <= 300 else 'COLLECTION_STALE') + '; DEVICE_FRESHNESS_UNVERIFIED'


def build_battery_grid(rows, *, limit, has_more, now, context=None, older_context_complete=True):
    """Build bounded observation and derived-interval rows from one query page.

    ``rows`` is newest first: the first ``limit`` records are rendered; any
    further records are context only and never rendered. ``context`` may
    instead pass the older context rows separately (then only ``rows[:limit]``
    are page rows, so a short final page never renders context). Each visible row's
    derived values depend only on that row and its own GRID_CONTEXT_ROWS older
    neighbours, so they are independent of the page size and cursor position.

    ``older_context_complete`` states that no stored rows exist beyond the
    supplied ones (end of history). When it is False and a row's window is
    short, derived evidence is reported unavailable rather than assumed.
    ``has_more`` is accepted for API compatibility; context use does not depend on it.
    """
    if type(limit) is not int or limit < 1:
        raise ValueError('invalid page limit')
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('aware evaluation time required')
    if context is None:
        ordered = [dict(row) for row in rows]
        visible = ordered[:limit]
    else:
        visible = [dict(row) for row in list(rows)[:limit]]
        ordered = visible + [dict(row) for row in context]

    observations = []
    behaviors = []
    for index, row in enumerate(visible):
        stamp = row.get('time')
        window = ordered[index:index + 1 + GRID_CONTEXT_ROWS]
        window_full = len(window) == 1 + GRID_CONTEXT_ROWS
        context_known = window_full or older_context_complete
        older_rows = window[1:]
        # Keep the last window row for classifying the oldest interval.
        searchable = older_rows[:GRID_CONTEXT_ROWS - 1] if window_full else older_rows
        immediate_older = older_rows[0] if older_rows else None
        same_payload = (immediate_older is not None and
                        _signature(row, _PAYLOAD_FIELDS) == _signature(immediate_older, _PAYLOAD_FIELDS))

        previous_voltage = None
        previous_voltage_offset = None
        previous_battery = None
        for offset, older in enumerate(searchable):
            if valid_battery_voltage(older.get('battery_voltage')) is not None:
                if previous_voltage is None:
                    previous_voltage = older
                    previous_voltage_offset = offset
            battery_signature = _signature(older, _BATTERY_FIELDS)
            if previous_battery is None and any(value is not None for value in battery_signature):
                previous_battery = older
            if previous_voltage is not None and previous_battery is not None:
                break
        if previous_voltage is not None and not context_known and \
                previous_voltage_offset + 2 >= len(older_rows) + 1:
            # The oldest interval's predecessor was not supplied: cannot classify it.
            previous_voltage = previous_voltage_offset = None

        battery_signature = _signature(row, _BATTERY_FIELDS)
        repeated_battery = (previous_battery is not None and
                            battery_signature == _signature(previous_battery, _BATTERY_FIELDS))
        raw_voltage = _number(row.get('battery_voltage'))
        voltage = valid_battery_voltage(raw_voltage)
        older_voltage = (valid_battery_voltage(previous_voltage.get('battery_voltage'))
                         if previous_voltage else None)
        interval_seconds = None
        if previous_voltage is not None:
            interval_status = 'NO_PREVIOUS_VALID_VOLTAGE'
        elif not context_known:
            interval_status = 'PREVIOUS_CONTEXT_UNAVAILABLE; INTERVAL_NOT_DERIVED'
        elif window_full:
            interval_status = 'NO_VALID_VOLTAGE_WITHIN_BOUNDED_CONTEXT; INTERVAL_NOT_DERIVED'
        else:
            interval_status = 'NO_PREVIOUS_VALID_VOLTAGE'
        voltage_delta = None
        if (stamp and previous_voltage and previous_voltage.get('time') and
                isinstance(stamp, datetime) and isinstance(previous_voltage['time'], datetime)):
            # Keep fractional seconds: truncation turned a 500 ms interval into 0 s.
            seconds = (stamp - previous_voltage['time']).total_seconds()
            if seconds < 0:
                interval_status = 'OUT_OF_ORDER_COLLECTION_TIME; INTERVAL_NOT_DERIVED'
            elif seconds == 0:
                interval_status = 'DUPLICATE_COLLECTION_TIME; INTERVAL_NOT_DERIVED'
            else:
                interval_seconds = round(seconds, 3)
                interval_status = ('INTERVAL_AVAILABLE' if seconds >= MIN_SLOPE_INTERVAL_S else
                                   'INTERVAL_TOO_SHORT_FOR_SLOPE; NOT_INDEPENDENT_EVIDENCE')
                if voltage is not None and older_voltage is not None:
                    voltage_delta = round(voltage - older_voltage, 4)

        missing = [name for name, value in (
            ('battery_voltage', raw_voltage),
            ('battery_current_chg', _number(row.get('battery_current_chg'))),
            ('battery_current_dis', _number(row.get('battery_current_dis'))),
            ('pv_power_w', _number(row.get('pv_power_w'))),
            ('output_power_w', _number(row.get('output_power_w'))),
        ) if value is None]
        invalid = (['battery_voltage'] if raw_voltage is not None and voltage is None else [])
        quality = ('REPEATED_CACHED_PAYLOAD' if same_payload else
                   'BATTERY_FIELDS_REPEATED; NOT_COUNTED_AS_INDEPENDENT_BATTERY_RESPONSE'
                   if repeated_battery else 'PAYLOAD_CHANGED; DEVICE_SAMPLE_ID_UNAVAILABLE')
        if invalid:
            quality += '; INVALID_BATTERY_VOLTAGE_EXCLUDED_FROM_INFERENCE'
        provenance = 'COLLECTION_TIME_ONLY; DEVICE_MEASUREMENT_TIME_UNVERIFIED'
        observations.append({
            'data_kind': 'RECORDED_OBSERVATION',
            'collection_time': _iso(stamp),
            'mode': row.get('working_mode'),
            'battery_voltage_v': voltage,
            # Raw finite telemetry kept for diagnostics only (e.g. 0.0 V); never used for inference.
            'battery_voltage_v_reported_raw': raw_voltage,
            'invalid_fields': invalid,
            'battery_soc_reported': _number(row.get('battery_soc')),
            'easun_charge_current_a_reported': _number(row.get('battery_current_chg')),
            'easun_discharge_current_a_reported': _number(row.get('battery_current_dis')),
            'pv_power_w_reported': _number(row.get('pv_power_w')),
            'load_power_w_reported': _number(row.get('output_power_w')),
            'voltage_change_since_previous_valid_v': voltage_delta,
            'interval_seconds': interval_seconds,
            'interval_status': interval_status,
            'external_charging_status': _charging_status(row),
            'data_provenance': provenance,
            'data_quality': quality,
            'missing_fields': missing,
        })

        # Exact repeated battery values are visible above but do not create a new
        # derived battery-response row. A changed PV/load payload does not prove a
        # new battery measurement, so such rows remain explicitly qualified.
        if repeated_battery:
            continue
        previous = previous_voltage
        if not previous or interval_seconds is None or voltage_delta is None:
            continue
        charge_now = _number(row.get('battery_current_chg'))
        discharge_now = _number(row.get('battery_current_dis'))
        charge_before = _number(previous.get('battery_current_chg'))
        discharge_before = _number(previous.get('battery_current_dis'))
        load_now = _number(row.get('output_power_w'))
        load_before = _number(previous.get('output_power_w'))
        pv_now = _number(row.get('pv_power_w'))
        pv_before = _number(previous.get('pv_power_w'))
        current_conflict = ((charge_now is not None and discharge_now is not None
                             and charge_now > 0 and discharge_now > 0)
                            or (charge_before is not None and discharge_before is not None
                                and charge_before > 0 and discharge_before > 0))
        # Integrate over every consecutive collection between the two voltage rows,
        # so a skipped row with missing current interrupts rather than gets bridged.
        # Classify within this row's own fixed window (plus the predecessor of
        # its oldest row when present) so cached repeats before the selected
        # interval are seen identically on every page size.
        span_end = previous_voltage_offset + 1          # window index of previous_voltage
        span_rows = window[:span_end + 2]               # + predecessor, if available
        shared = calculate_battery_intervals(list(reversed(span_rows)))
        intervals = shared['intervals']
        interval_slice = intervals[len(intervals) - span_end:]
        integration = summarize_intervals(interval_slice)
        # Preserve the grid's established operator-facing reason names while
        # keeping the same canonical barrier decisions as the worker.
        display_reasons = {
            'MISSING_CURRENT': 'MISSING_CURRENT_INTERRUPTS_CONTINUITY',
            'MISSING_VOLTAGE': 'MISSING_VOLTAGE_INTERRUPTS_CONTINUITY',
            'INVALID_ZERO_VOLTAGE': 'INVALID_ZERO_VOLTAGE_INTERRUPTS_CONTINUITY',
            'GAP_OVER_15M': 'COLLECTION_GAP_OVER_15M',
            'CONFLICTING_CURRENT': 'CONFLICTING_CURRENT_CHANNELS_EXCLUDED',
        }
        reasons = [display_reasons.get(reason, reason) for reason in integration['reasons']]
        if any(item['seconds'] == 0 for item in interval_slice):
            reasons.append('AMBIGUOUS_DUPLICATE_TIMESTAMP')
        # A voltage delta remains visible, but a slope is reported only when
        # every interval in the span satisfies the shared measurement policy.
        # The worker derives slopes only from intervals accepted by the shared
        # policy. Keep the grid aligned: no slope across missing channels,
        # repeated payloads, ambiguous times, invalid voltage or long gaps.
        # The slope itself is taken from the shared policy's single accepted
        # interval; the grid never recomputes it.
        shared_slope = (interval_slice[0]['voltage_slope_v_per_hour']
                        if len(interval_slice) == 1 and interval_slice[0]['reason'] is None else None)
        slope_computable = (MIN_SLOPE_INTERVAL_S <= interval_seconds <= MAX_INTEGRATION_INTERVAL_S
                            and integration['status'] == 'COMPLETE' and shared_slope is not None)
        slope = round(shared_slope, 4) if slope_computable else None
        charge_ah, discharge_ah = integration['charge_ah'], integration['discharge_ah']
        charge_wh, discharge_wh = integration['charge_wh'], integration['discharge_wh']
        charge_mean = ((charge_before + charge_now) / 2
                       if charge_before is not None and charge_now is not None
                       and charge_before >= 0 and charge_now >= 0 else None)
        discharge_mean = ((discharge_before + discharge_now) / 2
                          if discharge_before is not None and discharge_now is not None
                          and discharge_before >= 0 and discharge_now >= 0 else None)
        both_channels = current_conflict
        charge_only = (charge_mean is not None and discharge_mean is not None
                       and charge_mean > 0 and discharge_mean == 0)
        if both_channels:
            current_context = 'EASUN_CHARGE_AND_DISCHARGE_CHANNELS_BOTH_NONZERO; SEMANTICS_UNVERIFIED; TOTAL_CURRENT_UNOBSERVED'
        elif charge_only:
            current_context = 'EASUN_CHARGING_CURRENT_REPORTED; TOTAL_CURRENT_UNOBSERVED; EXTERNAL_CHARGING_POSSIBLE'
        elif charge_mean is not None and discharge_mean is not None and charge_mean > 0 and discharge_mean > 0:
            current_context = 'EASUN_CURRENT_CONTEXT_CHANGED_ACROSS_INTERVAL; SEMANTICS_UNVERIFIED; TOTAL_CURRENT_UNOBSERVED'
        elif discharge_mean is not None and discharge_mean > 0:
            current_context = 'EASUN_DISCHARGING_CURRENT_REPORTED; TOTAL_CURRENT_UNOBSERVED; EXTERNAL_CHARGING_POSSIBLE'
        elif charge_mean == 0 and discharge_mean == 0:
            current_context = 'NO_EASUN_CURRENT_REPORTED; EXTERNAL_CHARGING_POSSIBLE'
        else:
            current_context = 'EASUN_CURRENT_UNKNOWN; EXTERNAL_CHARGING_POSSIBLE'
        response = 'VOLTAGE_CHANGE_OBSERVED; CAUSE_UNIDENTIFIED'
        if voltage_delta > 0 and both_channels:
            response = 'VOLTAGE_RISE_OBSERVED; BOTH_EASUN_CURRENT_CHANNELS_NONZERO; SEMANTICS_UNVERIFIED'
        elif voltage_delta > 0 and charge_only:
            response = 'VOLTAGE_RISE_OBSERVED; EASUN_CHARGING_CURRENT_REPORTED; CAUSE_UNIDENTIFIED'
        elif voltage_delta > 0 and charge_mean is not None and discharge_mean is not None and charge_mean > 0 and discharge_mean > 0:
            response = 'VOLTAGE_RISE_OBSERVED; EASUN_CURRENT_CONTEXT_CHANGED_ACROSS_INTERVAL; CAUSE_UNIDENTIFIED'
        elif voltage_delta > 0 and discharge_mean is not None and discharge_mean > 0:
            response = 'VOLTAGE_RISE_OBSERVED; EASUN_DISCHARGE_REPORTED; EXTERNAL_CHARGING_POSSIBLE; CAUSE_UNIDENTIFIED'
        elif voltage_delta > 0 and charge_mean == 0 and discharge_mean == 0:
            response = 'VOLTAGE_RISE_OBSERVED; NO_EASUN_CURRENT_REPORTED; EXTERNAL_CHARGING_POSSIBLE; CAUSE_UNIDENTIFIED'
        elif voltage_delta > 0:
            response = 'VOLTAGE_RISE_OBSERVED; CURRENT_CONTEXT_UNCERTAIN; CAUSE_UNIDENTIFIED'
        elif voltage_delta < 0 and discharge_mean is not None and discharge_mean > 0:
            response = 'VOLTAGE_DECLINE_WITH_EASUN_DISCHARGE_REPORTED; NOT_VERIFIED_SAG'
        if (voltage_delta > 0 and load_now is not None and load_before is not None
                and load_now < load_before and not charge_only):
            response = 'VOLTAGE_RECOVERY_POSSIBLE_WITH_LOAD_REDUCTION; NOT_CAUSAL'
        if not slope_computable:
            if interval_seconds < MIN_SLOPE_INTERVAL_S:
                response = 'INTERVAL_TOO_SHORT_FOR_SLOPE; NOT_INDEPENDENT_VOLTAGE_EVIDENCE'
            elif current_conflict:
                response += '; INTERVAL_CONTINUITY_UNVERIFIED; NOT_INDEPENDENT_VOLTAGE_EVIDENCE'
            else:
                response = 'INTERVAL_CONTINUITY_UNVERIFIED; NOT_INDEPENDENT_VOLTAGE_EVIDENCE'
        interval_quality = ('COLLECTION_GAP_OVER_15M; THROUGHPUT_NOT_INTEGRATED'
                            if interval_seconds > MAX_INTEGRATION_INTERVAL_S else
                            'THROUGHPUT_' + integration['status']
                            if integration['status'] != 'COMPLETE' else 'COLLECTION_INTERVAL_AVAILABLE')
        behaviors.append({
            'interval_end_collection_time': _iso(stamp),
            'operating_context': '%s; PV %s W; load %s W' % (
                row.get('working_mode') or 'UNKNOWN', 'unknown' if pv_now is None else pv_now,
                'unknown' if load_now is None else load_now),
            'voltage_slope_v_per_hour': slope,
            'slope_status': ('COMPUTED' if slope_computable else 'INTERVAL_TOO_SHORT_FOR_SLOPE'
                             if interval_seconds < MIN_SLOPE_INTERVAL_S else 'CONTINUITY_NOT_VERIFIED'),
            'interval_seconds': interval_seconds,
            'voltage_delta_v': voltage_delta,
            'observed_easun_charge_a_reported': charge_now,
            'observed_easun_discharge_a_reported': discharge_now,
            'reported_soc_percent_unverified': _number(row.get('battery_soc')),
            'load_w_reported': load_now,
            'pv_delta_w_reported': round(pv_now - pv_before, 2) if pv_now is not None and pv_before is not None else None,
            'easun_charge_ah_proxy': charge_ah,
            'easun_discharge_ah_proxy': discharge_ah,
            'easun_charge_wh_proxy': charge_wh,
            'easun_discharge_wh_proxy': discharge_wh,
            'easun_throughput_status': integration['status'],
            'easun_throughput_covered_seconds': integration['covered_seconds'],
            'easun_throughput_elapsed_seconds': integration['elapsed_seconds'],
            'easun_throughput_coverage_ratio': integration['coverage_ratio'],
            'easun_throughput_exclusions': sorted(set(reasons)),
            'total_battery_throughput_ah': None,
            'estimated_voltage_response': response,
            'charging_interpretation': current_context,
            'capacity_or_response_proxy': 'CAPACITY_NOT_IDENTIFIABLE; EASUN-REPORTED CHANNELS ONLY; TOTAL BATTERY CURRENT UNOBSERVED',
            'confidence': 'LOW_UNVERIFIED_SOURCE; qualitative, not calibrated probability',
            'freshness': _freshness(now, stamp),
            'limitation': interval_quality + '; external charging and device sample identity are unobserved.',
            'data_kind': 'DERIVED_ESTIMATE',
        })

    labels = ['EXTERNAL_CHARGING_POSSIBLE', 'TOTAL_BATTERY_CURRENT_UNOBSERVED',
              'CHARGING_RELATIONSHIP_UNVERIFIED', 'CAPACITY_NOT_IDENTIFIABLE']
    if any(item['voltage_delta_v'] > 0 and item['slope_status'] == 'COMPUTED' for item in behaviors):
        labels.append('VOLTAGE_RISE_OBSERVED')
    if any(item['estimated_voltage_response'].startswith('VOLTAGE_RECOVERY_POSSIBLE') for item in behaviors):
        labels.append('VOLTAGE_RECOVERY_POSSIBLE')
    return {'observations': observations, 'battery_behavior': behaviors,
            'external_charging_labels': labels,
            'external_charging_status': 'POSSIBLE; NOT DETECTED AT A SPECIFIC MOMENT',
            'sample_time_provenance': 'COLLECTION_TIME_ONLY; DEVICE_MEASUREMENT_TIME_UNVERIFIED',
            'pv_source_coverage': 'EASUN_VISIBLE_PV_ONLY; OTHER_ORIENTATIONS_AND_CONTROLLERS_UNOBSERVED'}
