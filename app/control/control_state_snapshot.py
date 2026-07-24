"""Read-only control state snapshot model for dessmonitor.

Packages already-computed control-layer state (load candidates, policy decision,
manual queue, command proposal, safety gate result, execution eligibility result,
battery/load budget summary, autonomous/operator mode flags) into a stable passive
view suitable for future web UI, observability, and debugging.

This module does NOT execute commands, add API endpoints, wire into runtime,
call Tuya/hardware, read devices directly, fetch weather, or call ML.
It only packages what has already been computed by the control layer.

This module uses only:
  - Python standard library (dataclasses, enum, field)
  - app.control.policy_models types: LoadCandidate, EnergyBudget, BatteryOperatingWindow
  - app.control.command_arbitration types: CommandProposal, CommandProposalStatus
  - app.control.command_safety_gate types: CommandSafetyGateResult, SafetyGateStatus
  - app.control.execution_eligibility types: ExecutionEligibilityResult, ExecutionEligibilityStatus, ExecutionEligibilityMode
  - app.control.manual_control_queue types: ManualControlQueueSnapshot
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.control.command_arbitration import (
    CommandProposal,
    CommandProposalStatus,
)
from app.control.command_safety_gate import (
    CommandSafetyGateResult,
    SafetyGateStatus,
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
# ControlStateSnapshotStatus — overall snapshot health
# ---------------------------------------------------------------------------


class ControlStateSnapshotStatus(Enum):
    """Overall status of a control state snapshot.

    OK: all pipeline stages present and not blocked.
    DEGRADED: some pipeline pieces missing or review required.
    BLOCKED: safety or execution eligibility is blocked.
    UNKNOWN: no meaningful input provided.
    """
    OK = "ok"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


# ===================================================================
# Type 1: LoadControlSnapshot
# ===================================================================


@dataclass(frozen=True)
class LoadControlSnapshot:
    """A single load's control state summary for display and observability.

    Pure data — no hardware calls, no side effects.
    As of PR 0034a, currently_on is nullable: True=ON, False=OFF, None=UNKNOWN.
    """
    load_id: str = ""
    display_name: str = ""
    description: str = ""
    device_type: str = ""
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
    mapping_status: str | None = None
    startup_reset_result: str | None = None
    enabled: bool = True
    communication_status: str | None = None


# ===================================================================
# Type 2: ControlPipelineSnapshot
# ===================================================================


@dataclass(frozen=True)
class ControlPipelineSnapshot:
    """Snapshot of the decision pipeline state for a target load.

    Pure data — no recomputation, no side effects.
    """
    policy_decision: PolicyDecisionResult | None = None
    command_proposal: CommandProposal | None = None
    safety_gate_result: CommandSafetyGateResult | None = None
    execution_eligibility: ExecutionEligibilityResult | None = None
    manual_queue_snapshot: ManualControlQueueSnapshot | None = None


# ===================================================================
# Type 3: ControlModeSnapshot
# ===================================================================


@dataclass(frozen=True)
class ControlModeSnapshot:
    """Snapshot of execution mode flags.

    Pure data — no side effects.
    execution_allowed_now and eligible_for_future_executor are copied
    from execution eligibility if present; this module does not set them.
    """
    autonomous_enabled: bool = True
    operator_overrides_enabled: bool = True
    controlled_execution_enabled: bool = True
    dry_run_only: bool = False
    execution_allowed_now: bool = False
    eligible_for_future_executor: bool = False


# ===================================================================
# Type 6: SensorReadSnapshot
# ===================================================================


@dataclass(frozen=True)
class SensorReadSnapshot:
    """A single sensor's telemetry snapshot for the web UI.

    Pure data — no hardware calls, no side effects.
    All fields are web-safe strings or scalars.
    observed_at is an ISO string or None at this boundary.
    """
    sensor_id: str = ""
    display_name: str = ""
    description: str = ""
    device_type: str = ""
    metric: str = ""
    value: float | None = None
    unit: str = "celsius"
    observed_at: str | None = None
    source: str = ""
    freshness: str = ""
    status: str = ""
    communication_status: str = ""


# ===================================================================
# Type 7: ControlStateSnapshotInput (extended)
# ===================================================================


@dataclass(frozen=True)
class ControlStateSnapshotInput:
    """All input data needed to build a control state snapshot.

    Pure data — no side effects.
    snapshot_id and created_at are caller-provided.
    This function does not generate UUIDs or read system time.
    """
    snapshot_id: str = ""
    created_at: str = ""
    loads: tuple[LoadCandidate, ...] = field(default_factory=tuple)
    sensors: tuple[SensorReadSnapshot, ...] = field(default_factory=tuple)
    load_metadata: tuple[RuntimeLoadState, ...] = field(default_factory=tuple)
    policy_decision: PolicyDecisionResult | None = None
    command_proposal: CommandProposal | None = None
    safety_gate_result: CommandSafetyGateResult | None = None
    execution_eligibility: ExecutionEligibilityResult | None = None
    manual_queue_snapshot: ManualControlQueueSnapshot | None = None
    energy_budget: EnergyBudget | None = None
    battery_window: BatteryOperatingWindow | None = None
    mode: ControlModeSnapshot | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


# ===================================================================
# Type 5: ControlStateSnapshot
# ===================================================================


@dataclass(frozen=True)
class ControlStateSnapshot:
    """Complete read-only control state snapshot.

    Pure data — no side effects, no execution, no recomputation.
    """
    snapshot_id: str = ""
    created_at: str = ""
    status: ControlStateSnapshotStatus = ControlStateSnapshotStatus.UNKNOWN
    loads: tuple[LoadControlSnapshot, ...] = field(default_factory=tuple)
    sensors: tuple[SensorReadSnapshot, ...] = field(default_factory=tuple)
    pipeline: ControlPipelineSnapshot | None = None
    mode: ControlModeSnapshot | None = None
    energy_budget: EnergyBudget | None = None
    battery_window: BatteryOperatingWindow | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Helper: convert a LoadCandidate to LoadControlSnapshot
# ---------------------------------------------------------------------------

def _convert_load(load: LoadCandidate, load_meta: dict[str, Any] | None = None) -> LoadControlSnapshot:
    """Convert a LoadCandidate into a read-only LoadControlSnapshot.

    Args:
        load: The LoadCandidate from the control layer.
        load_meta: Optional dict with display-only metadata (description,
            device_type, mapping_status, startup_reset_result, enabled,
            communication_status) keyed by load_id.
    """
    # Determine status string
    if load.currently_on is True:
        load_status = "active"
    elif load.currently_on is False:
        if load.controllable:
            load_status = "idle"
        else:
            load_status = "unavailable"
    else:
        load_status = "unknown"

    meta = (load_meta or {}).get(load.load_id, {}) if load_meta else {}

    return LoadControlSnapshot(
        load_id=load.load_id,
        display_name=load.display_name,
        description=meta.get("description", ""),
        device_type=meta.get("device_type", ""),
        configured_load_watts=load.configured_load_watts,
        currently_on=load.currently_on,
        controllable=load.controllable,
        is_life_support=load.is_life_support,
        roles=load.roles,
        status=load_status,
        notes="",
        observed_state=load.observed_state,
        observed_at=load.observed_at,
        observation_source=load.observation_source,
        freshness=load.freshness,
        mapping_status=meta.get("mapping_status"),
        startup_reset_result=meta.get("startup_reset_result"),
        enabled=meta.get("enabled", True),
        communication_status=meta.get("communication_status"),
    )


# ===================================================================
# build_control_state_snapshot — pure read-only packaging
# ===================================================================


def build_control_state_snapshot(
    snapshot_input: ControlStateSnapshotInput | None,
) -> ControlStateSnapshot:
    """Build a read-only control state snapshot from already-computed state.

    Pure, deterministic, side-effect-free. Does NOT recompute policy decisions,
    arbitrate commands, evaluate safety gates, or evaluate execution eligibility.
    Only packages what is already provided.

    Args:
        snapshot_input: ControlStateSnapshotInput with pre-computed state
                        (None treated as unknown/no-input).

    Returns:
        ControlStateSnapshot with status, pipeline, loads, mode, and warnings.
    """
    # ----- 1. no-input -----
    if snapshot_input is None:
        return ControlStateSnapshot(
            status=ControlStateSnapshotStatus.UNKNOWN,
            warnings=("no-input", "read-only-snapshot", "no-execution"),
            notes=("read-only-snapshot", "no-execution"),
        )

    # Gather warnings and notes
    warnings: list[str] = []
    base_notes: list[str] = ["read-only-snapshot", "no-execution"]

    policy = snapshot_input.policy_decision
    proposal = snapshot_input.command_proposal
    safety = snapshot_input.safety_gate_result
    eligibility = snapshot_input.execution_eligibility

    # Track missing pipeline pieces
    has_policy = policy is not None
    has_proposal = proposal is not None
    has_safety = safety is not None
    has_eligibility = eligibility is not None

    if not has_policy:
        warnings.append("missing-policy-decision")
    if not has_proposal:
        warnings.append("missing-command-proposal")
    if not has_safety:
        warnings.append("missing-safety-gate-result")
    if not has_eligibility:
        warnings.append("missing-execution-eligibility")

    # Determine status from pipeline state
    status = ControlStateSnapshotStatus.UNKNOWN

    # Check for blocked state (safety or eligibility)
    safety_blocked = has_safety and safety.status == SafetyGateStatus.BLOCKED
    eligibility_blocked = has_eligibility and eligibility.status == ExecutionEligibilityStatus.BLOCKED

    if safety_blocked or eligibility_blocked:
        status = ControlStateSnapshotStatus.BLOCKED
        if safety_blocked:
            warnings.append("safety-gate-blocked")
        if eligibility_blocked:
            warnings.append("execution-eligibility-blocked")
    elif has_safety and safety.status == SafetyGateStatus.REVIEW_REQUIRED:
        status = ControlStateSnapshotStatus.DEGRADED
        warnings.append("review-required")
    elif has_eligibility and eligibility.status == ExecutionEligibilityStatus.REVIEW_REQUIRED:
        status = ControlStateSnapshotStatus.DEGRADED
        warnings.append("review-required")
    elif not all([has_policy, has_proposal, has_safety, has_eligibility]):
        # Some pieces are missing
        has_any_input = any([
            has_policy, has_proposal, has_safety, has_eligibility,
            snapshot_input.loads, snapshot_input.sensors,
        ])
        if has_any_input:
            status = ControlStateSnapshotStatus.DEGRADED
        else:
            status = ControlStateSnapshotStatus.UNKNOWN
    else:
        # All pieces present and not blocked/review
        status = ControlStateSnapshotStatus.OK

    # Build load metadata lookup from RuntimeLoadState objects
    load_meta: dict[str, dict[str, Any]] = {}
    for rls in snapshot_input.load_metadata:
        load_meta[rls.load_id] = {
            "description": rls.description,
            "device_type": rls.device_type,
            "mapping_status": rls.mapping_status,
            "startup_reset_result": rls.startup_reset_result,
            "enabled": rls.enabled,
            "communication_status": rls.communication_status,
        }

    # Convert loads with metadata
    load_snapshots = tuple(
        _convert_load(load, load_meta) for load in snapshot_input.loads
    )

    # Build pipeline snapshot (unchanged — just package)
    pipeline = ControlPipelineSnapshot(
        policy_decision=policy,
        command_proposal=proposal,
        safety_gate_result=safety,
        execution_eligibility=eligibility,
        manual_queue_snapshot=snapshot_input.manual_queue_snapshot,
    )

    # Build mode snapshot
    if snapshot_input.mode is not None:
        mode = snapshot_input.mode
        # Update execution flags from eligibility if present
        if has_eligibility and eligibility is not None:
            mode = ControlModeSnapshot(
                autonomous_enabled=mode.autonomous_enabled,
                operator_overrides_enabled=mode.operator_overrides_enabled,
                controlled_execution_enabled=mode.controlled_execution_enabled,
                dry_run_only=mode.dry_run_only,
                execution_allowed_now=eligibility.execution_allowed_now,
                eligible_for_future_executor=eligibility.eligible_for_future_executor,
            )
    else:
        mode = ControlModeSnapshot(
            execution_allowed_now=eligibility.execution_allowed_now if has_eligibility and eligibility else False,
            eligible_for_future_executor=eligibility.eligible_for_future_executor if has_eligibility and eligibility else False,
        )

    # Include caller-provided notes
    all_notes = list(snapshot_input.notes) + base_notes

    return ControlStateSnapshot(
        snapshot_id=snapshot_input.snapshot_id,
        created_at=snapshot_input.created_at,
        status=status,
        loads=load_snapshots,
        sensors=snapshot_input.sensors,
        pipeline=pipeline,
        mode=mode,
        energy_budget=snapshot_input.energy_budget,
        battery_window=snapshot_input.battery_window,
        notes=tuple(all_notes),
        warnings=tuple(warnings),
    )
