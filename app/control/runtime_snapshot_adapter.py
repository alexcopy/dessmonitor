"""Runtime read-only control snapshot adapter for dessmonitor.

Transforms caller-provided runtime/shared-state-like data into
ControlStateSnapshotInput and ControlStateSnapshot (from PR 0023).

This is preparation for future runtime integration and web UI visibility.
The adapter does NOT wire into runtime, import runtime modules, read live
shared_state globals, add API endpoints, call Tuya/hardware, or execute commands.

This module uses only:
  - Python standard library (dataclasses, enum, field)
  - app.control.policy_models types
  - app.control.manual_control_queue types
  - app.control.command_arbitration types
  - app.control.command_safety_gate types
  - app.control.execution_eligibility types
  - app.control.control_state_snapshot module
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.control.command_arbitration import (
    CommandProposal,
    CommandProposalStatus,
)
from app.control.command_safety_gate import (
    CommandSafetyGateResult,
    SafetyGateStatus,
)
from app.control.control_state_snapshot import (
    ControlModeSnapshot,
    ControlStateSnapshot,
    ControlStateSnapshotInput,
    build_control_state_snapshot,
)
from app.control.execution_eligibility import (
    ExecutionEligibilityMode,
    ExecutionEligibilityResult,
    ExecutionEligibilityStatus,
)
from app.control.manual_control_queue import ManualControlQueueSnapshot
from app.control.policy_models import (
    BatteryOperatingWindow,
    EnergyBudget,
    LoadCandidate,
    PolicyDecisionResult,
)

# ---------------------------------------------------------------------------
# RuntimeSnapshotAdapterStatus — adapter health
# ---------------------------------------------------------------------------


class RuntimeSnapshotAdapterStatus(Enum):
    """Overall status of the runtime snapshot adapter.

    OK: adapter built a valid snapshot.
    DEGRADED: partial state, missing some pieces.
    UNKNOWN: no input or no useful state.
    """
    OK = "ok"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


# ===================================================================
# Type 1: RuntimeLoadState
# ===================================================================


@dataclass(frozen=True)
class RuntimeLoadState:
    """A single load's state provided by the runtime caller.

    Pure data — no device reads, no side effects.
    """
    load_id: str = ""
    display_name: str = ""
    configured_load_watts: float = 0.0
    currently_on: bool | None = False
    controllable: bool = True
    is_life_support: bool = False
    roles: tuple[str, ...] = field(default_factory=tuple)
    status: str = "unknown"
    notes: str = ""
    observed_state: str | None = None
    observed_at: str | None = None
    observation_source: str | None = None
    freshness: str | None = None


# ===================================================================
# Type 2: RuntimeControlModeState
# ===================================================================


@dataclass(frozen=True)
class RuntimeControlModeState:
    """Control mode state provided by the runtime caller.

    Pure data — no side effects.
    """
    autonomous_enabled: bool = True
    operator_overrides_enabled: bool = True
    controlled_execution_enabled: bool = True
    dry_run_only: bool = False


# ===================================================================
# Type 3: RuntimeControlSnapshotAdapterInput
# ===================================================================


@dataclass(frozen=True)
class RuntimeControlSnapshotAdapterInput:
    """All input data for the runtime snapshot adapter.

    Pure data — no side effects.
    snapshot_id and created_at are caller-provided.
    runtime_state is an optional dict of caller-provided keys.
    All pipeline objects are caller-provided.
    """
    snapshot_id: str = ""
    created_at: str = ""
    runtime_state: dict[str, object] = field(default_factory=dict)
    loads: tuple[RuntimeLoadState, ...] = field(default_factory=tuple)
    policy_decision: PolicyDecisionResult | None = None
    command_proposal: CommandProposal | None = None
    safety_gate_result: CommandSafetyGateResult | None = None
    execution_eligibility: ExecutionEligibilityResult | None = None
    manual_queue_snapshot: ManualControlQueueSnapshot | None = None
    energy_budget: EnergyBudget | None = None
    battery_window: BatteryOperatingWindow | None = None
    mode: RuntimeControlModeState | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


# ===================================================================
# Type 4: RuntimeControlSnapshotAdapterResult
# ===================================================================


@dataclass(frozen=True)
class RuntimeControlSnapshotAdapterResult:
    """Result of the runtime snapshot adapter.

    status: overall adapter status.
    snapshot_input: ControlStateSnapshotInput for build_control_state_snapshot.
    snapshot: ControlStateSnapshot from build_control_state_snapshot.
    warnings: tuple of warning strings.
    notes: tuple of note strings.
    """
    status: RuntimeSnapshotAdapterStatus = RuntimeSnapshotAdapterStatus.UNKNOWN
    snapshot_input: ControlStateSnapshotInput | None = None
    snapshot: ControlStateSnapshot | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Helper: convert RuntimeLoadState → LoadCandidate
# ---------------------------------------------------------------------------

def _to_load_candidate(rl: RuntimeLoadState) -> LoadCandidate:
    """Convert a RuntimeLoadState to a LoadCandidate for the control layer."""
    return LoadCandidate(
        load_id=rl.load_id,
        display_name=rl.display_name,
        configured_load_watts=rl.configured_load_watts,
        currently_on=rl.currently_on,
        controllable=rl.controllable,
        is_life_support=rl.is_life_support,
        roles=rl.roles,
        observed_state=rl.observed_state,
        observed_at=rl.observed_at,
        observation_source=rl.observation_source,
        freshness=rl.freshness,
    )


# ---------------------------------------------------------------------------
# Helper: convert RuntimeControlModeState → ControlModeSnapshot
# ---------------------------------------------------------------------------

def _to_mode_snapshot(rm: RuntimeControlModeState) -> ControlModeSnapshot:
    """Convert a RuntimeControlModeState to a ControlModeSnapshot."""
    return ControlModeSnapshot(
        autonomous_enabled=rm.autonomous_enabled,
        operator_overrides_enabled=rm.operator_overrides_enabled,
        controlled_execution_enabled=rm.controlled_execution_enabled,
        dry_run_only=rm.dry_run_only,
        execution_allowed_now=False,  # always false per PR 0022
        eligible_for_future_executor=False,  # updated by build_control_state_snapshot
    )


# ---------------------------------------------------------------------------
# Helper: best-effort parse runtime_state dict for convenience keys
# ---------------------------------------------------------------------------

_RUNTIME_BOOL_KEYS = frozenset({
    "autonomous_enabled",
    "operator_overrides_enabled",
    "controlled_execution_enabled",
    "dry_run_only",
})

_RUNTIME_FLOAT_KEYS = frozenset({
    "max_total_load_watts",
    "current_total_load_watts",
    "available_load_budget_watts",
    "battery_voltage",
})


def _merge_runtime_booleans(
    runtime_state: dict[str, object],
    mode: RuntimeControlModeState | None,
) -> RuntimeControlModeState | None:
    """Merge runtime_state booleans into mode. Best-effort, non-failing."""
    base = _to_mode_snapshot(mode) if mode else None

    # If no mode provided, try building from runtime state
    if base is None:
        auton = _safe_bool(runtime_state.get("autonomous_enabled"))
        overrides = _safe_bool(runtime_state.get("operator_overrides_enabled"))
        ctrl = _safe_bool(runtime_state.get("controlled_execution_enabled"))
        dry = _safe_bool(runtime_state.get("dry_run_only"))
        base = ControlModeSnapshot(
            autonomous_enabled=auton,
            operator_overrides_enabled=overrides,
            controlled_execution_enabled=ctrl,
            dry_run_only=dry,
            execution_allowed_now=False,
            eligible_for_future_executor=False,
        )
        return None  # Return None so caller uses base

    return base


def _safe_bool(value: object | None) -> bool:
    """Safely interpret a value as bool. Non-failing."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower().strip() in ("true", "1", "yes")
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _safe_float(value: object | None) -> float | None:
    """Safely interpret a value as float. Non-failing."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (ValueError, TypeError):
            return None
    return None


# ===================================================================
# build_runtime_control_snapshot — pure adapter function
# ===================================================================


def build_runtime_control_snapshot(
    adapter_input: RuntimeControlSnapshotAdapterInput | None,
) -> RuntimeControlSnapshotAdapterResult:
    """Build a ControlStateSnapshot from caller-provided runtime-like data.

    Pure, deterministic, side-effect-free. Does NOT wire into runtime,
    import runtime modules, read live shared_state globals, or execute
    any commands.

    Args:
        adapter_input: RuntimeControlSnapshotAdapterInput with caller-provided
                       runtime state, loads, and pipeline objects.
                       (None treated as no input.)

    Returns:
        RuntimeControlSnapshotAdapterResult with status, snapshot_input,
        snapshot, warnings, and notes.
    """
    # ----- 1. no-input -----
    if adapter_input is None:
        sni = ControlStateSnapshotInput()
        snap = build_control_state_snapshot(sni)
        return RuntimeControlSnapshotAdapterResult(
            status=RuntimeSnapshotAdapterStatus.UNKNOWN,
            snapshot_input=sni,
            snapshot=snap,
            warnings=("no-input", "read-only-adapter", "no-execution", "no-runtime-wiring"),
            notes=(
                "read-only-adapter",
                "read-only-snapshot",
                "no-execution",
                "no-runtime-wiring",
                "caller-provided-state",
                "future-web-ui-read-model",
            ),
        )

    # Collect warnings and notes
    warnings: list[str] = []
    base_notes: list[str] = [
        "read-only-adapter",
        "read-only-snapshot",
        "no-execution",
        "no-runtime-wiring",
        "caller-provided-state",
        "future-web-ui-read-model",
    ]

    rt = adapter_input.runtime_state
    explicit_loads = adapter_input.loads
    explicit_mode = adapter_input.mode
    explicit_budget = adapter_input.energy_budget
    explicit_battery = adapter_input.battery_window

    # ----- runtime_state checks -----
    has_rt = rt is not None and len(rt) > 0
    has_loads = len(explicit_loads) > 0

    if not has_rt:
        warnings.append("missing-runtime-state")
    else:
        # Check for partial state (some expected keys missing)
        found_bools = sum(1 for k in _RUNTIME_BOOL_KEYS if k in rt)
        found_floats = sum(1 for k in _RUNTIME_FLOAT_KEYS if k in rt)
        if found_bools < 1 and found_floats < 1:
            warnings.append("partial-runtime-state")

    if not has_loads:
        warnings.append("missing-loads")

    # ----- mode: merge runtime_state booleans with explicit mode -----
    mode_snapshot: ControlModeSnapshot | None = None
    if explicit_mode is not None:
        mode_snapshot = _to_mode_snapshot(explicit_mode)
    elif has_rt:
        # Build mode from runtime_state booleans
        auton = _safe_bool(rt.get("autonomous_enabled"))
        overrides = _safe_bool(rt.get("operator_overrides_enabled"))
        ctrl = _safe_bool(rt.get("controlled_execution_enabled"))
        dry = _safe_bool(rt.get("dry_run_only"))
        mode_snapshot = ControlModeSnapshot(
            autonomous_enabled=auton,
            operator_overrides_enabled=overrides,
            controlled_execution_enabled=ctrl,
            dry_run_only=dry,
            execution_allowed_now=False,
            eligible_for_future_executor=False,
        )

    # ----- energy/battery: use explicit if provided, else try runtime_state -----
    energy: EnergyBudget | None = explicit_budget
    battery: BatteryOperatingWindow | None = explicit_battery

    if energy is None and has_rt:
        max_w = _safe_float(rt.get("max_total_load_watts"))
        cur_w = _safe_float(rt.get("current_total_load_watts"))
        avail_w = _safe_float(rt.get("available_load_budget_watts"))
        if any(v is not None for v in (max_w, cur_w, avail_w)):
            energy = EnergyBudget(
                max_total_load_watts=max_w if max_w is not None else 2500.0,
                current_total_load_watts=cur_w if cur_w is not None else 0.0,
                available_load_budget_watts=avail_w if avail_w is not None else 0.0,
            )

    if battery is None and has_rt:
        bv = _safe_float(rt.get("battery_voltage"))
        if bv is not None:
            battery = BatteryOperatingWindow()

    # ----- convert loads -----
    load_candidates = tuple(_to_load_candidate(rl) for rl in explicit_loads)

    # ----- build snapshot input and snapshot -----
    sni = ControlStateSnapshotInput(
        snapshot_id=adapter_input.snapshot_id,
        created_at=adapter_input.created_at,
        loads=load_candidates,
        policy_decision=adapter_input.policy_decision,
        command_proposal=adapter_input.command_proposal,
        safety_gate_result=adapter_input.safety_gate_result,
        execution_eligibility=adapter_input.execution_eligibility,
        manual_queue_snapshot=adapter_input.manual_queue_snapshot,
        energy_budget=energy,
        battery_window=battery,
        mode=mode_snapshot,
        notes=adapter_input.notes,
    )

    snap = build_control_state_snapshot(sni)

    # ----- determine status -----
    if snap.status.name == "UNKNOWN" and not has_loads and not has_rt:
        status = RuntimeSnapshotAdapterStatus.UNKNOWN
    elif snap.status.name in ("DEGRADED", "UNKNOWN"):
        status = RuntimeSnapshotAdapterStatus.DEGRADED
    else:
        status = RuntimeSnapshotAdapterStatus.OK

    all_notes = list(base_notes) + list(adapter_input.notes)

    return RuntimeControlSnapshotAdapterResult(
        status=status,
        snapshot_input=sni,
        snapshot=snap,
        warnings=tuple(warnings),
        notes=tuple(all_notes),
    )
