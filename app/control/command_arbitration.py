"""Command intent and proposal arbitration for dessmonitor.

Connects autonomous policy decision results with manual control queue snapshots
into command intents and proposals. Autonomous operation is the default path.
Manual/operator input is an override and correction layer.

This module produces CommandProposal instances but does not execute them.
No API endpoints. No executor. No runtime wiring. No hardware calls.

This module uses only:
  - Python standard library (dataclasses, enum, field)
  - app.control.energy_policy types: EnergyPolicyDecision
  - app.control.policy_models types: PolicyDecisionResult
  - app.control.manual_control_queue types: ManualControlQueueSnapshot, ManualControlStatus
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.control.energy_policy import EnergyPolicyDecision
from app.control.manual_control_queue import (
    ManualControlQueueSnapshot,
    ManualControlStatus,
)
from app.control.policy_models import PolicyDecisionResult

# ---------------------------------------------------------------------------
# CommandIntentSource — where the intent originates
# ---------------------------------------------------------------------------


class CommandIntentSource(Enum):
    """Origin of a command intent.

    AUTO_POLICY: derived from the autonomous policy decision engine.
    MANUAL_OPERATOR: from a human operator via the manual control queue.
    SCHEDULE: from a time-based schedule (future).
    SAFETY_SYSTEM: from a safety override (future).
    MAINTENANCE: from a maintenance mode (future).
    """
    AUTO_POLICY = "auto_policy"
    MANUAL_OPERATOR = "manual_operator"
    SCHEDULE = "schedule"
    SAFETY_SYSTEM = "safety_system"
    MAINTENANCE = "maintenance"


# ---------------------------------------------------------------------------
# CommandProposalStatus — proposal lifecycle status
# ---------------------------------------------------------------------------


class CommandProposalStatus(Enum):
    """Status of a command proposal.

    PROPOSED: proposal is ready for future execution (subject to safety gates).
    BLOCKED: proposal is blocked by safety or operator conditions.
    NO_ACTION: no actionable intent was found.
    """
    PROPOSED = "proposed"
    BLOCKED = "blocked"
    NO_ACTION = "no_action"


# ===================================================================
# Type 1: CommandIntent
# ===================================================================


@dataclass(frozen=True)
class CommandIntent:
    """A command intent from a specific source for a specific load.

    Pure data — no hardware execution, no side effects.
    created_at is a caller-provided string.
    """
    intent_id: str
    load_id: str
    desired_state: str
    source: CommandIntentSource
    reason: str = ""
    priority: int = 0
    created_at: str = ""
    ttl_seconds: int | None = None


# ===================================================================
# Type 2: CommandProposal
# ===================================================================


@dataclass(frozen=True)
class CommandProposal:
    """A proposal to execute a command intent.

    This is the output of arbitration — it represents what should be done
    but does not execute it. Execution is deferred to future safety-gated
    controlled execution.

    Pure data — no hardware execution, no side effects.
    """
    proposal_id: str
    intent: CommandIntent | None
    status: CommandProposalStatus = CommandProposalStatus.NO_ACTION
    execution_eligible: bool = False
    requires_operator_review: bool = False
    blocked_by: tuple[str, ...] = field(default_factory=tuple)
    safety_notes: tuple[str, ...] = field(default_factory=tuple)
    created_at: str = ""


# ===================================================================
# Type 3: CommandArbitrationInput
# ===================================================================


@dataclass(frozen=True)
class CommandArbitrationInput:
    """Input for command intent arbitration.

    Combines an autonomous policy decision, an optional manual queue snapshot,
    and configuration flags for autonomous/operator override behavior.

    Pure data — no side effects.
    """
    policy_decision: PolicyDecisionResult | None = None
    manual_queue_snapshot: ManualControlQueueSnapshot | None = None
    autonomous_enabled: bool = True
    operator_overrides_enabled: bool = True
    safety_blocked_by: tuple[str, ...] = field(default_factory=tuple)


# ===================================================================
# Type 4: CommandArbitrationResult
# ===================================================================


@dataclass(frozen=True)
class CommandArbitrationResult:
    """Result of command intent arbitration.

    accepted: whether an actionable intent was found.
    proposal: the CommandProposal (if any).
    reason: human-readable reason string.
    blocked_by: blocking condition identifiers (if rejected).
    """
    accepted: bool
    proposal: CommandProposal | None = None
    reason: str = ""
    blocked_by: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Actionable decision set for mapping
# ---------------------------------------------------------------------------

_ACTIONABLE_DECISIONS: dict[str, str] = {
    "ALLOW_ON": "on",
    "FORCE_OFF": "off",
    "PREFER_OFF": "off",
}


def _decision_name(decision: EnergyPolicyDecision) -> str:
    """Get the name of an EnergyPolicyDecision value."""
    return getattr(decision, "name", str(decision))


def _desired_state_string(value) -> str:
    """Extract desired state as a string from a DesiredState enum or raw string."""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


# ---------------------------------------------------------------------------
# Helper: generate deterministic IDs from source + load_id
# ---------------------------------------------------------------------------

def _make_intent_id(source: CommandIntentSource, load_id: str) -> str:
    """Generate a deterministic intent ID from source and load."""
    return f"intent-{source.value}-{load_id}"


def _make_proposal_id(intent_id: str) -> str:
    """Generate a deterministic proposal ID from intent ID."""
    return f"proposal-{intent_id}"


# ===================================================================
# arbitrate_command_intent — pure command intent arbitration
# ===================================================================


def arbitrate_command_intent(
    arbitration_input: CommandArbitrationInput,
) -> CommandArbitrationResult:
    """Arbitrate between autonomous policy and manual operator intents.

    Pure, deterministic, side-effect-free. Priority order:
      1. safety-blocked — non-empty safety_blocked_by blocks everything
      2. manual-operator-override — queued manual command takes priority
      3. autonomous-disabled — autonomous is off, no auto intents
      4. auto-policy-intent — autonomous policy with actionable decision
      5. no-target-load — actionable but missing target
      6. no-actionable-intent — default, nothing to do

    Args:
        arbitration_input: CommandArbitrationInput with policy decision,
                           manual queue snapshot, and flags.

    Returns:
        CommandArbitrationResult with accepted flag, proposal, and reason.
    """
    policy = arbitration_input.policy_decision
    manual_snapshot = arbitration_input.manual_queue_snapshot

    # ----- 1. safety-blocked -----
    if arbitration_input.safety_blocked_by:
        proposal = CommandProposal(
            proposal_id="proposal-safety-blocked",
            intent=None,
            status=CommandProposalStatus.BLOCKED,
            execution_eligible=False,
            requires_operator_review=False,
            blocked_by=arbitration_input.safety_blocked_by,
            safety_notes=("safety condition active; all proposals blocked",),
        )
        return CommandArbitrationResult(
            accepted=False,
            proposal=proposal,
            reason="safety-blocked",
            blocked_by=arbitration_input.safety_blocked_by,
        )

    # ----- 2. manual-operator-override -----
    if (
        arbitration_input.operator_overrides_enabled
        and manual_snapshot is not None
        and manual_snapshot.items
    ):
        # Find first queued item by snapshot order
        first_queued = None
        for item in manual_snapshot.items:
            if item.status == ManualControlStatus.QUEUED:
                first_queued = item
                break

        if first_queued is not None:
            cmd = first_queued.command
            reason_text = cmd.reason if cmd.reason else "manual-operator-override"
            ds = _desired_state_string(cmd.desired_state)

            intent = CommandIntent(
                intent_id=_make_intent_id(CommandIntentSource.MANUAL_OPERATOR, cmd.load_id),
                load_id=cmd.load_id,
                desired_state=ds,
                source=CommandIntentSource.MANUAL_OPERATOR,
                reason=reason_text,
                created_at=cmd.created_at,
            )
            proposal = CommandProposal(
                proposal_id=_make_proposal_id(intent.intent_id),
                intent=intent,
                status=CommandProposalStatus.PROPOSED,
                execution_eligible=True,
                requires_operator_review=False,
                safety_notes=(
                    "operator override intent; safety gates still required before execution",
                ),
                created_at=cmd.created_at,
            )
            return CommandArbitrationResult(
                accepted=True,
                proposal=proposal,
                reason="manual-operator-override",
            )

    # ----- 3. autonomous-disabled -----
    if not arbitration_input.autonomous_enabled:
        proposal = CommandProposal(
            proposal_id="proposal-autonomous-disabled",
            intent=None,
            status=CommandProposalStatus.NO_ACTION,
            execution_eligible=False,
            requires_operator_review=False,
        )
        return CommandArbitrationResult(
            accepted=False,
            proposal=proposal,
            reason="autonomous-disabled",
        )

    # ----- 4. auto-policy-intent -----
    if policy is not None:
        dname = _decision_name(policy.decision)
        mapped_state = _ACTIONABLE_DECISIONS.get(dname)

        if mapped_state is not None:
            target_id = policy.target_load_id

            if target_id:
                reason_text = policy.reason if policy.reason else "auto-policy-intent"

                intent = CommandIntent(
                    intent_id=_make_intent_id(CommandIntentSource.AUTO_POLICY, target_id),
                    load_id=target_id,
                    desired_state=mapped_state,
                    source=CommandIntentSource.AUTO_POLICY,
                    reason=reason_text,
                )
                proposal = CommandProposal(
                    proposal_id=_make_proposal_id(intent.intent_id),
                    intent=intent,
                    status=CommandProposalStatus.PROPOSED,
                    execution_eligible=True,
                    requires_operator_review=False,
                    safety_notes=(
                        "autonomous default; safety gates still required before execution",
                    ),
                )
                return CommandArbitrationResult(
                    accepted=True,
                    proposal=proposal,
                    reason="auto-policy-intent",
                )

            # ----- 5. no-target-load -----
            proposal = CommandProposal(
                proposal_id="proposal-no-target-load",
                intent=None,
                status=CommandProposalStatus.NO_ACTION,
                execution_eligible=False,
                requires_operator_review=False,
            )
            return CommandArbitrationResult(
                accepted=False,
                proposal=proposal,
                reason="no-target-load",
            )

    # ----- 6. no-actionable-intent -----
    proposal = CommandProposal(
        proposal_id="proposal-no-actionable-intent",
        intent=None,
        status=CommandProposalStatus.NO_ACTION,
        execution_eligible=False,
        requires_operator_review=False,
    )
    return CommandArbitrationResult(
        accepted=False,
        proposal=proposal,
        reason="no-actionable-intent",
    )
