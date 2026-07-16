"""Controlled execution eligibility model for dessmonitor.

Determines whether a CommandProposal (from PR 0020) that has passed
safety gates (PR 0021) is eligible for a future executor.

Answers: CommandProposal + PASSED safety gate → eligible_for_future_executor.

This module does NOT execute commands, wire into runtime, add API endpoints,
or call Tuya/hardware. execution_allowed_now is always false.

This module uses only:
  - Python standard library (dataclasses, enum, field)
  - app.control.command_arbitration types: CommandProposal, CommandIntentSource
  - app.control.command_safety_gate types: CommandSafetyGateResult, SafetyGateStatus
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.control.command_arbitration import (
    CommandIntentSource,
    CommandProposal,
)
from app.control.command_safety_gate import (
    CommandSafetyGateResult,
    SafetyGateStatus,
)


# ---------------------------------------------------------------------------
# ExecutionEligibilityStatus — result of eligibility check
# ---------------------------------------------------------------------------


class ExecutionEligibilityStatus(Enum):
    """Overall execution eligibility status.

    ELIGIBLE: proposal is eligible for future execution (execution_allowed_now still false).
    BLOCKED: proposal is blocked from execution.
    REVIEW_REQUIRED: operator review needed before execution.
    NO_PROPOSAL: no proposal was provided.
    """
    ELIGIBLE = "eligible"
    BLOCKED = "blocked"
    REVIEW_REQUIRED = "review_required"
    NO_PROPOSAL = "no_proposal"


# ---------------------------------------------------------------------------
# ExecutionEligibilityMode — which execution mode applies
# ---------------------------------------------------------------------------


class ExecutionEligibilityMode(Enum):
    """Execution mode for the proposal.

    AUTONOMOUS: proposal originated from autonomous policy.
    MANUAL_OPERATOR: proposal originated from manual operator input.
    DISABLED: execution is disabled or source is unknown.
    """
    AUTONOMOUS = "autonomous"
    MANUAL_OPERATOR = "manual_operator"
    DISABLED = "disabled"


# ---------------------------------------------------------------------------
# ExecutionEligibilityContext — mode and policy flags
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionEligibilityContext:
    """Execution eligibility mode and policy flags.

    Pure data — no side effects.
    """
    controlled_execution_enabled: bool = True
    autonomous_execution_enabled: bool = True
    manual_operator_execution_enabled: bool = True
    require_operator_review_for_autonomous: bool = False
    require_operator_review_for_manual: bool = False
    disabled_load_ids: tuple[str, ...] = field(default_factory=tuple)
    dry_run_only: bool = False


# ---------------------------------------------------------------------------
# ExecutionEligibilityInput — input aggregator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionEligibilityInput:
    """Input to the execution eligibility evaluation.

    Pure data — no side effects.
    """
    proposal: CommandProposal | None = None
    safety_gate_result: CommandSafetyGateResult | None = None
    context: ExecutionEligibilityContext = field(
        default_factory=ExecutionEligibilityContext,
    )


# ---------------------------------------------------------------------------
# ExecutionEligibilityResult — evaluation output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionEligibilityResult:
    """Result of an execution eligibility evaluation.

    status: overall eligibility status.
    eligible_for_future_executor: whether a future executor may consider this.
    execution_allowed_now: always false in this PR.
    requires_operator_review: whether operator review is needed.
    mode: which execution mode applies.
    reason: human-readable reason string.
    blocked_by: blocking condition identifiers.
    safety_notes: advisory notes for operator visibility.
    """
    status: ExecutionEligibilityStatus = ExecutionEligibilityStatus.NO_PROPOSAL
    eligible_for_future_executor: bool = False
    execution_allowed_now: bool = False
    requires_operator_review: bool = False
    mode: ExecutionEligibilityMode = ExecutionEligibilityMode.DISABLED
    reason: str = ""
    blocked_by: tuple[str, ...] = field(default_factory=tuple)
    safety_notes: str = ""


# ---------------------------------------------------------------------------
# Helper: determine execution mode from intent source
# ---------------------------------------------------------------------------

def _get_mode(proposal: CommandProposal) -> ExecutionEligibilityMode:
    """Map proposal intent source to execution mode."""
    if proposal.intent is None:
        return ExecutionEligibilityMode.DISABLED
    source = proposal.intent.source
    if source == CommandIntentSource.AUTO_POLICY:
        return ExecutionEligibilityMode.AUTONOMOUS
    if source == CommandIntentSource.MANUAL_OPERATOR:
        return ExecutionEligibilityMode.MANUAL_OPERATOR
    return ExecutionEligibilityMode.DISABLED


# ---------------------------------------------------------------------------
# Helper: build result (execution_allowed_now always false)
# ---------------------------------------------------------------------------

_BASE_SAFETY_NOTES = "eligible for future executor only; this PR does not execute commands"
_DRY_RUN_NOTES = "dry-run only; no hardware execution"


def _result(
    status: ExecutionEligibilityStatus,
    eligible: bool,
    reason: str,
    mode: ExecutionEligibilityMode = ExecutionEligibilityMode.DISABLED,
    requires_review: bool = False,
    blocked_by: tuple[str, ...] = (),
    notes: str = "",
) -> ExecutionEligibilityResult:
    """Build an ExecutionEligibilityResult with execution_allowed_now always False."""
    return ExecutionEligibilityResult(
        status=status,
        eligible_for_future_executor=eligible,
        execution_allowed_now=False,
        requires_operator_review=requires_review,
        mode=mode,
        reason=reason,
        blocked_by=blocked_by if blocked_by else (reason,),
        safety_notes=notes,
    )


# ===================================================================
# evaluate_execution_eligibility — pure eligibility check
# ===================================================================


def evaluate_execution_eligibility(
    eligibility_input: ExecutionEligibilityInput | None,
) -> ExecutionEligibilityResult:
    """Evaluate whether a CommandProposal is eligible for a future executor.

    Pure, deterministic, side-effect-free. Checks in priority order:
      1. no-proposal
      2. no-safety-gate
      3. safety-gate-blocked
      4. safety-review-required
      5. controlled-execution-disabled
      6. disabled-load
      7. autonomous-disabled
      8. manual-operator-disabled
      9. autonomous-review-required
      10. manual-review-required
      11. dry-run-only
      12. eligible

    execution_allowed_now is always False in this PR.

    Args:
        eligibility_input: ExecutionEligibilityInput (None treated as no proposal).

    Returns:
        ExecutionEligibilityResult with status, eligible_for_future_executor,
        mode, reason, and safety notes.
    """
    # ----- 1. no-proposal -----
    if eligibility_input is None:
        return _result(
            ExecutionEligibilityStatus.NO_PROPOSAL,
            eligible=False,
            reason="no-proposal",
        )

    proposal = eligibility_input.proposal
    if proposal is None:
        return _result(
            ExecutionEligibilityStatus.NO_PROPOSAL,
            eligible=False,
            reason="no-proposal",
        )

    safety = eligibility_input.safety_gate_result
    ctx = eligibility_input.context

    # ----- 2. no-safety-gate -----
    if safety is None:
        return _result(
            ExecutionEligibilityStatus.BLOCKED,
            eligible=False,
            reason="no-safety-gate",
        )

    # ----- 3. safety-review-required -----
    if safety.requires_operator_review:
        return _result(
            ExecutionEligibilityStatus.REVIEW_REQUIRED,
            eligible=False,
            reason="safety-review-required",
            requires_review=True,
        )

    # ----- 4. safety-gate-blocked -----
    if not safety.execution_allowed:
        return _result(
            ExecutionEligibilityStatus.BLOCKED,
            eligible=False,
            reason="safety-gate-blocked",
        )

    # ----- 5. controlled-execution-disabled -----
    if not ctx.controlled_execution_enabled:
        return _result(
            ExecutionEligibilityStatus.BLOCKED,
            eligible=False,
            reason="controlled-execution-disabled",
        )

    # Determine intent source and load for subsequent checks
    intent = proposal.intent
    source = intent.source if intent else None
    load_id = intent.load_id if intent else ""

    # ----- 6. disabled-load -----
    if load_id and load_id in ctx.disabled_load_ids:
        return _result(
            ExecutionEligibilityStatus.BLOCKED,
            eligible=False,
            reason="disabled-load",
        )

    # ----- 7. autonomous-disabled -----
    if source == CommandIntentSource.AUTO_POLICY and not ctx.autonomous_execution_enabled:
        return _result(
            ExecutionEligibilityStatus.BLOCKED,
            eligible=False,
            reason="autonomous-disabled",
        )

    # ----- 8. manual-operator-disabled -----
    if source == CommandIntentSource.MANUAL_OPERATOR and not ctx.manual_operator_execution_enabled:
        return _result(
            ExecutionEligibilityStatus.BLOCKED,
            eligible=False,
            reason="manual-operator-disabled",
        )

    # ----- 9. autonomous-review-required -----
    if source == CommandIntentSource.AUTO_POLICY and ctx.require_operator_review_for_autonomous:
        return _result(
            ExecutionEligibilityStatus.REVIEW_REQUIRED,
            eligible=False,
            reason="autonomous-review-required",
            requires_review=True,
        )

    # ----- 10. manual-review-required -----
    if source == CommandIntentSource.MANUAL_OPERATOR and ctx.require_operator_review_for_manual:
        return _result(
            ExecutionEligibilityStatus.REVIEW_REQUIRED,
            eligible=False,
            reason="manual-review-required",
            requires_review=True,
        )

    # Determine mode
    mode = _get_mode(proposal)

    # ----- 11. dry-run-only -----
    if ctx.dry_run_only:
        return _result(
            ExecutionEligibilityStatus.ELIGIBLE,
            eligible=True,
            reason="dry-run-only",
            mode=mode,
            notes=_DRY_RUN_NOTES,
        )

    # ----- 12. eligible -----
    return _result(
        ExecutionEligibilityStatus.ELIGIBLE,
        eligible=True,
        reason="eligible",
        mode=mode,
        notes=_BASE_SAFETY_NOTES,
    )
