# Controlled Execution Eligibility

This document records the architecture, principles, and invariants for controlled
execution eligibility in the dessmonitor control system. Execution eligibility is
the final gate before any command proposal is dispatched to a future hardware executor.

## 1. Execution Eligibility Is Not Hardware Execution

An `ExecutionEligibilityResult` indicates whether a proposal is eligible for execution.
It does NOT execute anything. The `execution_allowed_now` field is always `false` in
this PR. Actual hardware execution requires a separate, safety-reviewed execution PR.

## 2. CommandProposal Must Pass Safety Gates Before Eligibility

The pipeline is:

```
CommandProposal → Safety Gate → Execution Eligibility → (future) Executor
```

A proposal must pass safety gates (`PASSED`) before it can be evaluated for execution
eligibility. If the safety gate status is `BLOCKED` or `REVIEW_REQUIRED`, eligibility
returns a corresponding status. No proposal can bypass safety gates.

## 3. eligible_for_future_executor Means a Future Executor May Consider It

The field `eligible_for_future_executor` is `true` only when:
- The safety gate passed
- Controlled execution is enabled
- The target load is not in the disabled list
- The relevant execution mode (autonomous/manual) is enabled
- No operator review is required for that mode
- Dry-run mode is acceptable (future executor considers but does not execute)

This flag tells a future executor "this proposal is safe to consider."

## 4. execution_allowed_now Remains False

`execution_allowed_now` is always `false` in this PR. It will only become `true` in a
future PR that implements the actual controlled executor with hardware dispatch. This
is a hard invariant — the validation script verifies it.

## 5. Autonomous Operation Remains the Default Future Path

Proposals originating from the autonomous policy decision engine (source `AUTO_POLICY`)
are evaluated for eligibility with `mode=AUTONOMOUS`. When autonomous execution is
enabled and no review is required, these proposals become `eligible_for_future_executor=true`.

## 6. Operator/Web UI Remains Override/Correction

Operator-initiated proposals (source `MANUAL_OPERATOR`) are evaluated for eligibility
with `mode=MANUAL_OPERATOR`. Operators can override or correct autonomous decisions but
do not have mandatory approval authority over every autonomous command.

## 7. Disabled Loads and Safety Gates Block Eligibility

Two mechanisms block eligibility:
- **Safety gates**: Battery fallback, inverter cap, readiness, health, cooldown, kill
  switch, and maintenance mode (evaluated by `evaluate_command_safety_gate`).
- **Disabled loads**: Specific load IDs in `disabled_load_ids` are blocked from execution
  regardless of safety gate results.

## 8. Dry-Run Mode Never Executes

When `dry_run_only` is `true`, the system evaluates eligibility as if it would execute
but never actually dispatches hardware commands. Proposals can be `ELIGIBLE` in dry-run
mode, but `execution_allowed_now` remains `false`. This mode is for testing and validation.

## 9. Operator Approval Is Not Mandatory for Every Autonomous Command

By default, `require_operator_review_for_autonomous` is `false`. Autonomous proposals
that pass safety gates can be eligible without operator intervention. Operators can
be required for autonomous proposals by setting this flag to `true`, which changes
the eligibility result to `REVIEW_REQUIRED`.

## 10. ML Control Remains Deferred

Machine learning models must not directly control devices without separate safety-reviewed
approval per ADR-0003. ML control is a distinct milestone from ML advisory. Before any ML
model can directly influence relay states:
- Independent safety review must approve
- Shadow/advisory mode validation must complete
- Controlled rollout with kill-switch capability must be in place
- Operator opt-in must be confirmed

ML control is deferred and is NOT in scope for any current or near-term PR.
