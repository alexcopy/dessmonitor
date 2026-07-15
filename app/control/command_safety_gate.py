"""Command safety gate model for dessmonitor.

Provides a pure deterministic function `evaluate_command_safety_gate()` that
decides whether a CommandProposal (from PR 0020) is eligible for future
execution. Safety gates are the final authority before any hardware action.

This module does NOT execute commands, wire into runtime, add API endpoints,
or call Tuya/hardware. Execution remains deferred to future controlled-execution PRs.

This module uses only:
  - Python standard library (dataclasses, enum, field)
  - app.control.command_arbitration types: CommandProposal, CommandIntent, CommandIntentSource
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.control.command_arbitration import (
    CommandIntent,
    CommandIntentSource,
    CommandProposal,
)


# ---------------------------------------------------------------------------
# SafetyGateStatus — overall safety evaluation result
# ---------------------------------------------------------------------------


class SafetyGateStatus(Enum):
    """Overall result of a command safety gate evaluation.

    PASSED: all checks passed, execution is allowed.
    BLOCKED: one or more hard checks failed, execution blocked.
    REVIEW_REQUIRED: execution requires operator review first.
    NO_PROPOSAL: no proposal was provided for evaluation.
    """
    PASSED = "passed"
    BLOCKED = "blocked"
    REVIEW_REQUIRED = "review_required"
    NO_PROPOSAL = "no_proposal"


# ---------------------------------------------------------------------------
# SafetyGateCheck — individual check result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SafetyGateCheck:
    """Result of a single safety gate check.

    Pure data — no hardware calls, no side effects.
    """
    name: str = ""
    passed: bool = False
    reason: str = ""
    severity: str = "info"


# ---------------------------------------------------------------------------
# CommandSafetyContext — runtime safety input
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandSafetyContext:
    """Runtime safety context for evaluating a command proposal.

    Pure data — no hardware calls, no side effects.
    All values are caller-provided (from telemetry, state, or config).
    """
    battery_voltage: float | None = None
    battery_grid_fallback_voltage: float = 24.5
    max_total_load_watts: float = 2500.0
    projected_total_load_watts: float | None = None
    readiness_passed: bool = True
    health_passed: bool = True
    cooldown_passed: bool = True
    operator_override_allowed: bool = True
    life_support_load_ids: tuple[str, ...] = field(default_factory=tuple)
    manual_review_required: bool = False
    kill_switch_active: bool = False
    maintenance_mode: bool = False


# ---------------------------------------------------------------------------
# CommandSafetyGateInput — input aggregator
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandSafetyGateInput:
    """Input to the command safety gate evaluation.

    Pure data — no side effects.
    """
    proposal: CommandProposal | None = None
    context: CommandSafetyContext = field(default_factory=CommandSafetyContext)


# ---------------------------------------------------------------------------
# CommandSafetyGateResult — evaluation output
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandSafetyGateResult:
    """Result of a command safety gate evaluation.

    status: overall safety gate status.
    execution_allowed: whether execution is allowed.
    requires_operator_review: whether operator review is needed before execution.
    reason: human-readable reason string.
    blocked_by: blocking condition identifiers.
    checks: detailed list of individual safety check results.
    safety_notes: advisory notes for operator visibility.
    """
    status: SafetyGateStatus = SafetyGateStatus.NO_PROPOSAL
    execution_allowed: bool = False
    requires_operator_review: bool = False
    reason: str = ""
    blocked_by: tuple[str, ...] = field(default_factory=tuple)
    checks: tuple[SafetyGateCheck, ...] = field(default_factory=tuple)
    safety_notes: str = ""


# ---------------------------------------------------------------------------
# Helper: check if proposal desires ON
# ---------------------------------------------------------------------------

def _desires_on(proposal: CommandProposal) -> bool:
    """Return True if the proposal's intent desires 'on'."""
    if proposal.intent is None:
        return False
    return proposal.intent.desired_state.lower().strip() == "on"


# ---------------------------------------------------------------------------
# Helper: build blocked result
# ---------------------------------------------------------------------------

def _blocked(reason: str, checks: tuple[SafetyGateCheck, ...]) -> CommandSafetyGateResult:
    """Build a BLOCKED result with checks and safety notes."""
    return CommandSafetyGateResult(
        status=SafetyGateStatus.BLOCKED,
        execution_allowed=False,
        requires_operator_review=False,
        reason=reason,
        blocked_by=(reason,),
        checks=checks,
        safety_notes="command execution not attempted by this module",
    )


def _review_required(
    reason: str, checks: tuple[SafetyGateCheck, ...], extra_note: str = ""
) -> CommandSafetyGateResult:
    """Build a REVIEW_REQUIRED result with checks, safety notes, and operator flag."""
    notes = "command execution not attempted by this module"
    if extra_note:
        notes = f"{extra_note}; {notes}"
    return CommandSafetyGateResult(
        status=SafetyGateStatus.REVIEW_REQUIRED,
        execution_allowed=False,
        requires_operator_review=True,
        reason=reason,
        blocked_by=(reason,),
        checks=checks,
        safety_notes=notes,
    )


# ===================================================================
# evaluate_command_safety_gate — pure deterministic safety gate
# ===================================================================


def evaluate_command_safety_gate(
    gate_input: CommandSafetyGateInput,
) -> CommandSafetyGateResult:
    """Evaluate whether a CommandProposal is safe for future execution.

    Pure, deterministic, side-effect-free. Checks in priority order:
      1. no-proposal
      2. proposal-not-executable
      3. kill-switch-active
      4. maintenance-mode
      5. battery-fallback-block (life-support → REVIEW_REQUIRED)
      6. inverter-load-cap-block
      7. readiness-block
      8. health-block
      9. cooldown-block
      10. manual-review-required
      11. manual-override-not-allowed
      12. passed

    Args:
        gate_input: CommandSafetyGateInput with proposal and context.

    Returns:
        CommandSafetyGateResult with status, execution_allowed, checks, and reason.
    """
    if gate_input is None:
        return CommandSafetyGateResult(
            status=SafetyGateStatus.NO_PROPOSAL,
            execution_allowed=False,
            reason="no-proposal",
            safety_notes="command execution not attempted by this module",
        )

    proposal = gate_input.proposal
    ctx = gate_input.context

    # ----- 1. no-proposal -----
    if proposal is None:
        return CommandSafetyGateResult(
            status=SafetyGateStatus.NO_PROPOSAL,
            execution_allowed=False,
            reason="no-proposal",
            safety_notes="command execution not attempted by this module",
        )

    # ----- 2. proposal-not-executable -----
    if not proposal.execution_eligible:
        check = SafetyGateCheck(
            name="proposal-executable",
            passed=False,
            reason="Proposal is not execution-eligible",
            severity="critical",
        )
        return _blocked("proposal-not-executable", (check,))

    # ----- 3. kill-switch-active -----
    if ctx.kill_switch_active:
        check = SafetyGateCheck(
            name="kill-switch",
            passed=False,
            reason="Kill switch is active — all execution blocked",
            severity="critical",
        )
        return _blocked("kill-switch-active", (check,))

    # ----- 4. maintenance-mode -----
    if ctx.maintenance_mode:
        check = SafetyGateCheck(
            name="maintenance-mode",
            passed=False,
            reason="Maintenance mode is active — automatic/manual execution blocked",
            severity="critical",
        )
        return _blocked("maintenance-mode", (check,))

    # ----- 5. battery-fallback-block -----
    if (
        _desires_on(proposal)
        and ctx.battery_voltage is not None
        and ctx.battery_voltage <= ctx.battery_grid_fallback_voltage
    ):
        check = SafetyGateCheck(
            name="battery-fallback",
            passed=False,
            reason=f"Battery voltage {ctx.battery_voltage}V at/below fallback {ctx.battery_grid_fallback_voltage}V",
            severity="critical",
        )
        # Life-support exception → REVIEW_REQUIRED instead of hard BLOCKED
        target_id = proposal.intent.load_id if proposal.intent else ""
        if target_id and target_id in ctx.life_support_load_ids:
            return _review_required(
                "battery-fallback-block",
                (check,),
                extra_note="life-support load at low battery — operator review required before execution",
            )
        return _blocked("battery-fallback-block", (check,))

    # ----- 6. inverter-load-cap-block -----
    if (
        _desires_on(proposal)
        and ctx.projected_total_load_watts is not None
        and ctx.projected_total_load_watts > ctx.max_total_load_watts
    ):
        check = SafetyGateCheck(
            name="inverter-load-cap",
            passed=False,
            reason=f"Projected load {ctx.projected_total_load_watts}W exceeds inverter cap {ctx.max_total_load_watts}W",
            severity="critical",
        )
        return _blocked("inverter-load-cap-block", (check,))

    # ----- 7. readiness-block -----
    if not ctx.readiness_passed:
        check = SafetyGateCheck(
            name="readiness",
            passed=False,
            reason="Readiness check failed",
            severity="warning",
        )
        return _blocked("readiness-block", (check,))

    # ----- 8. health-block -----
    if not ctx.health_passed:
        check = SafetyGateCheck(
            name="health",
            passed=False,
            reason="Health check failed",
            severity="warning",
        )
        return _blocked("health-block", (check,))

    # ----- 9. cooldown-block -----
    if not ctx.cooldown_passed:
        check = SafetyGateCheck(
            name="cooldown",
            passed=False,
            reason="Cooldown period not elapsed",
            severity="warning",
        )
        return _blocked("cooldown-block", (check,))

    # ----- 10. manual-review-required -----
    if proposal.requires_operator_review or ctx.manual_review_required:
        check = SafetyGateCheck(
            name="manual-review",
            passed=False,
            reason="Operator review is required before execution",
            severity="warning",
        )
        return _review_required(
            "manual-review-required",
            (check,),
        )

    # ----- 11. manual-override-not-allowed -----
    if (
        proposal.intent is not None
        and proposal.intent.source == CommandIntentSource.MANUAL_OPERATOR
        and not ctx.operator_override_allowed
    ):
        check = SafetyGateCheck(
            name="manual-override-allowed",
            passed=False,
            reason="Operator override is not allowed in current context",
            severity="critical",
        )
        return _blocked("manual-override-not-allowed", (check,))

    # ----- 12. passed -----
    passed_check = SafetyGateCheck(
        name="all-checks",
        passed=True,
        reason="All safety checks passed",
        severity="info",
    )
    return CommandSafetyGateResult(
        status=SafetyGateStatus.PASSED,
        execution_allowed=True,
        requires_operator_review=False,
        reason="passed",
        checks=(passed_check,),
        safety_notes="command execution not attempted by this module",
    )
