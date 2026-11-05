"""Pure analysis of collected inverter observations."""

import statistics
from typing import Any

OBSERVED_TELEMETRY_GAP_SECONDS = 60 * 60
_KNOWN_MODES = frozenset(('Line Mode', 'Battery Mode', 'PV Mode', 'Power Saving Mode',
                          'Standby Mode', 'Bypass Mode', 'Fault Mode', 'Invert Mode'))


def _valid_mode(mode):
    return isinstance(mode, str) and mode in _KNOWN_MODES


def _observable_mode(mode):
    return (isinstance(mode, str) and bool(mode.strip())
            and mode.strip().casefold() not in ('unknown', 'unknown mode', 'n/a', 'none', 'null', '—', '-'))


def _row_time(row: Any) -> Any:
    """Return a row timestamp without changing its timezone or precision."""
    return row["time"]


def _row_value(row: Any, key: str) -> Any:
    """Read a value from either an asyncpg Record or a plain test mapping."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def build_observed_output_transitions(
    rows: list[Any],
    max_gap_seconds: int = OBSERVED_TELEMETRY_GAP_SECONDS,
) -> list[dict[str, Any]]:
    """Build transitions from adjacent known working-mode observations.

    A transition is observed at the first stored observation in the new mode.
    Dwell is only reported when every sample-to-sample gap until the next
    transition is within the conservative telemetry-gap threshold.
    """
    ordered = sorted(rows, key=lambda row: _row_time(row))
    transitions: list[dict[str, Any]] = []
    previous_mode: str | None = None
    previous_index: int | None = None

    for index, row in enumerate(ordered):
        mode = _row_value(row, "working_mode")
        if not _observable_mode(mode):
            previous_mode = None
            previous_index = None
            continue
        if previous_mode is not None and mode != previous_mode and previous_index is not None:
            transition = {
                "observed_at": _row_time(row),
                "from_mode": previous_mode,
                "to_mode": mode,
                "battery_voltage": _row_value(row, "battery_voltage"),
                "battery_soc": _row_value(row, "battery_soc"),
                "battery_current_chg": _row_value(row, "battery_current_chg"),
                "battery_current_dis": _row_value(row, "battery_current_dis"),
                "pv_power_w": _row_value(row, "pv_power_w"),
                "output_power_w": _row_value(row, "output_power_w"),
                "ac_input_voltage": _row_value(row, "ac_input_voltage"),
                "output_voltage": _row_value(row, "output_voltage"),
                "charger_priority": _row_value(row, "charger_priority"),
                "output_priority_raw": _row_value(row, "output_priority_raw"),
                "mains_status": _row_value(row, "mains_status"),
                "observed_dwell": None,
                "_row_index": index,
            }
            transitions.append(transition)
        previous_mode = mode
        previous_index = index

    for current, following in zip(transitions, transitions[1:]):
        start = current["_row_index"]
        end = following["_row_index"]
        continuous = True
        for left, right in zip(ordered[start:end], ordered[start + 1:end + 1]):
            gap = (_row_time(right) - _row_time(left)).total_seconds()
            if (gap > max_gap_seconds or not _valid_mode(_row_value(left, 'working_mode'))
                    or not _valid_mode(_row_value(right, 'working_mode'))):
                continuous = False
                break
        if continuous:
            current["observed_dwell"] = (
                following["observed_at"] - current["observed_at"]
            ).total_seconds()

    for transition in transitions:
        transition.pop("_row_index", None)
    return transitions


def _count_known_changes(rows: list[Any], field: str) -> int:
    """Count changes between adjacent observations with known values."""
    ordered = sorted(rows, key=lambda row: _row_time(row))
    changes = 0
    previous: Any = None
    for row in ordered:
        value = _row_value(row, field)
        if value is None:
            previous = None
            continue
        if previous is not None and value != previous:
            changes += 1
        previous = value
    return changes


def _transition_summary(rows: list[Any]) -> dict[str, Any]:
    transitions = build_observed_output_transitions(rows)
    dwells = [
        transition["observed_dwell"]
        for transition in transitions
        if transition["observed_dwell"] is not None
    ]
    return {
        "output_mode_changes": len(transitions),
        "line_to_invert": sum(
            t["from_mode"] == "Line Mode" and t["to_mode"] == "Invert Mode"
            for t in transitions
        ),
        "invert_to_line": sum(
            t["from_mode"] == "Invert Mode" and t["to_mode"] == "Line Mode"
            for t in transitions
        ),
        "shortest_observed_dwell": min(dwells) if dwells else None,
        "median_observed_dwell": statistics.median(dwells) if dwells else None,
        "charger_source_changes": _count_known_changes(rows, "charger_priority"),
    }


def analyze_history(rows, window_start, as_of, summary_start):
    rows = sorted(rows, key=lambda row: row["time"], reverse=True)
    selected_rows = [row for row in rows if not row["is_context"]]
    context_rows = [row for row in rows if row["is_context"]]
    observation_fields = (
        "time", "working_mode", "charger_priority", "charger_priority_raw", "output_priority_raw", "mains_status",
        "battery_voltage", "battery_soc", "battery_current_chg",
        "battery_current_dis", "pv_power_w", "output_power_w", "output_voltage", "ac_input_voltage", "ac_output_load_pct",
    )
    observations = []
    for row in selected_rows:
        observation = {field: row[field] for field in observation_fields}
        observations.append(observation)
    # A bounded predecessor preserves a boundary transition while
    # keeping the reported observed_at at its recorded sample time.
    transitions = [
        transition for transition in build_observed_output_transitions(context_rows + selected_rows)
        if window_start <= transition["observed_at"] <= as_of
    ]
    # The 24-hour summary is independent of the selected display range.
    summary_rows = [row for row in selected_rows if row["time"] >= summary_start]
    summary_candidates = [row for row in context_rows + selected_rows if row["time"] < summary_start]
    summary_predecessor = max(summary_candidates, key=lambda row: row["time"]) if summary_candidates else None
    summary_input = ([summary_predecessor] if summary_predecessor is not None else []) + summary_rows
    summary = _transition_summary(summary_input)
    return observations, transitions, summary
