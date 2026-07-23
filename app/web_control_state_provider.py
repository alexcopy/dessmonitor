"""Runtime read-only control state provider for dessmonitor.

runtime-read-only-provider caller-provided-runtime-state
no-shared-state-read no-device-read no-tuya-hardware
no-execution no-write-api real-provider-injected
provider-errors-hidden operator-writes-through-control-layer

Provides an injectable read-only runtime state provider that adapts
caller-provided runtime state mappings into ``ControlStateSnapshot``
via the existing runtime snapshot adapter (PR 0024), without reading
global state, devices, Tuya, or hardware.

When no runtime state provider is injected, the endpoint returns
``UNAVAILABLE`` (backward-compatible with PR 0028b). When a real
runtime state provider is wired, the endpoint returns a meaningful
control state snapshot.
"""

from __future__ import annotations

from typing import Any, Callable

from app.control.runtime_snapshot_adapter import (
    RuntimeControlSnapshotAdapterInput,
    RuntimeLoadState,
    build_runtime_control_snapshot,
)


# ---------------------------------------------------------------------------
# build_control_state_snapshot_from_runtime_state
# ---------------------------------------------------------------------------


def build_control_state_snapshot_from_runtime_state(
    runtime_state: dict[str, Any] | None,
) -> Any | None:  # ControlStateSnapshot | None (avoids import at module level)
    """Build a ControlStateSnapshot from a caller-provided runtime state mapping.

    Pure, deterministic, side-effect-free. Does NOT read global state,
    devices, Tuya, or hardware.

    Recognized keys (all optional):
        snapshot_id (str)
        created_at (str)
        loads (list of RuntimeLoadState | dict)
        load_states (list of RuntimeLoadState | dict)
        policy_decision (PolicyDecisionResult)
        command_proposal (CommandProposal)
        safety_gate_result (CommandSafetyGateResult)
        execution_eligibility (ExecutionEligibilityResult)
        manual_queue_snapshot (ManualControlQueueSnapshot)
        energy_budget (EnergyBudget)
        battery_window (BatteryOperatingWindow)
        mode (RuntimeControlModeState)
        notes (tuple[str, ...])
        autonomous_enabled, operator_overrides_enabled,
        controlled_execution_enabled, dry_run_only,
        max_total_load_watts, current_total_load_watts,
        available_load_budget_watts, battery_voltage

    Args:
        runtime_state: Caller-provided mapping of runtime state keys.
                       None or empty dict returns None.

    Returns:
        ControlStateSnapshot if state is available, None otherwise.
    """
    if not runtime_state:
        return None

    # --- snapshot_id and created_at ---
    snapshot_id = str(runtime_state.get("snapshot_id", ""))
    created_at = str(runtime_state.get("created_at", ""))

    # --- parse loads / load_states (best-effort) ---
    raw_loads = runtime_state.get("loads") or runtime_state.get("load_states")
    loads: list[RuntimeLoadState] = _parse_loads(raw_loads)

    # --- direct pipeline objects (pass through) ---
    policy_decision = runtime_state.get("policy_decision")
    command_proposal = runtime_state.get("command_proposal")
    safety_gate_result = runtime_state.get("safety_gate_result")
    execution_eligibility = runtime_state.get("execution_eligibility")
    manual_queue_snapshot = runtime_state.get("manual_queue_snapshot")
    energy_budget = runtime_state.get("energy_budget")
    battery_window = runtime_state.get("battery_window")
    mode = runtime_state.get("mode")
    notes = runtime_state.get("notes")

    # --- build adapter input ---
    adapter_input = RuntimeControlSnapshotAdapterInput(
        snapshot_id=snapshot_id,
        created_at=created_at,
        runtime_state=runtime_state,
        loads=tuple(loads),
        policy_decision=policy_decision,
        command_proposal=command_proposal,
        safety_gate_result=safety_gate_result,
        execution_eligibility=execution_eligibility,
        manual_queue_snapshot=manual_queue_snapshot,
        energy_budget=energy_budget,
        battery_window=battery_window,
        mode=mode,
        notes=tuple(notes) if isinstance(notes, (list, tuple)) else (),
    )

    # --- build snapshot via adapter ---
    result = build_runtime_control_snapshot(adapter_input)
    return result.snapshot


# ---------------------------------------------------------------------------
# create_runtime_control_state_snapshot_provider
# ---------------------------------------------------------------------------


def create_runtime_control_state_snapshot_provider(
    runtime_state_provider: Callable[[], dict[str, Any] | None] | None = None,
) -> Callable[[], Any | None]:
    """Create a snapshot provider callable from an optional runtime state provider.

    The returned callable is suitable for passing to
    ``create_control_state_read_router()`` in ``app.control.web_ui_read_endpoint``.

    Behavior:
      - If ``runtime_state_provider`` is None, the returned provider always
        returns None (backward-compatible with PR 0028b placeholder).
      - If ``runtime_state_provider`` returns None or an empty mapping,
        the returned provider returns None.
      - If ``runtime_state_provider`` raises an exception, the returned
        provider returns None and does NOT leak exception text.
      - Otherwise, calls
        :func:`build_control_state_snapshot_from_runtime_state` on the
        returned mapping.

    Args:
        runtime_state_provider: Optional callable that returns a runtime
            state mapping or None.

    Returns:
        A callable that returns ControlStateSnapshot or None.
    """
    if runtime_state_provider is None:
        return lambda: None

    def _provider() -> Any | None:
        try:
            state = runtime_state_provider()
        except Exception:
            # Silently hide all exceptions — do not leak error text
            return None
        if not state:
            return None
        return build_control_state_snapshot_from_runtime_state(state)

    return _provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_loads(
    raw: object | None,
) -> list[RuntimeLoadState]:
    """Parse a list of loads (RuntimeLoadState or dict) into RuntimeLoadState list.

    Best-effort: RuntimeLoadState objects pass through, dicts are converted,
    invalid entries are silently skipped.
    """
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        return []

    result: list[RuntimeLoadState] = []
    for item in raw:
        if isinstance(item, RuntimeLoadState):
            result.append(item)
        elif isinstance(item, dict):
            try:
                roles_raw = item.get("roles", ())
                if isinstance(roles_raw, (list, tuple)):
                    roles = tuple(str(r) for r in roles_raw)
                elif isinstance(roles_raw, str):
                    roles = (roles_raw,)
                else:
                    roles = ()

                # currently_on: True=ON, False=OFF, None=UNKNOWN
                raw_on = item.get("currently_on")
                if raw_on is True:
                    currently_on: bool | None = True
                elif raw_on is False:
                    currently_on = False
                else:
                    currently_on = None

                rl = RuntimeLoadState(
                    load_id=str(item.get("load_id", "")),
                    display_name=str(item.get("display_name", "")),
                    configured_load_watts=float(item.get("configured_load_watts", 0)),
                    currently_on=currently_on,
                    controllable=bool(item.get("controllable", True)),
                    is_life_support=bool(item.get("is_life_support", False)),
                    roles=roles,
                    status=str(item.get("status", "unknown")),
                    notes=str(item.get("notes", "")),
                    observed_state=item.get("observed_state"),
                    observed_at=item.get("observed_at"),
                    observation_source=item.get("observation_source"),
                    freshness=item.get("freshness"),
                )
                result.append(rl)
            except (TypeError, ValueError):
                # Skip invalid entries silently
                continue
        # Skip unrecognized types silently

    return result
