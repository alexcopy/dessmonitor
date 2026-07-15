# Command Safety Gates

This document records the architecture, principles, and invariants for command
safety gates in the dessmonitor control system. Safety gates are the final
authority before any command proposal is executed.

## 1. CommandProposal Is Not Execution

A `CommandProposal` (from PR 0020) is an advisory intent — it represents what
the system wants to do. It does not execute hardware. Before any command can
be dispatched to Tuya or any hardware adapter, it must pass through safety gates.

```
Policy Decision → Arbitration → CommandProposal → Safety Gate → (future) Execution
```

## 2. Safety Gate Approval Is Required Before Any Future Execution

Every `CommandProposal` must be evaluated by `evaluate_command_safety_gate()`
before execution. The safety gate check is not optional — it is a hard requirement.
A proposal that fails safety gates must not be executed.

## 3. Autonomous Execution Remains Future and Separate

Safety gates evaluate proposals but do not execute them. Autonomous execution
(e.g., a scheduler that periodically evaluates proposals and dispatches safe ones)
is a future milestone. This PR provides the safety check; controlled execution
is deferred.

## 4. Web UI/Operator Override Still Passes Through Safety Gates

Manual operator intents (from the manual control queue, PR 0019) are translated
into proposals by command arbitration (PR 0020). Those proposals must still pass
through safety gates before execution. The operator cannot bypass safety checks.

## 5. Safety Gates Have Final Authority

Safety gates are the last decision point before hardware execution. They can:
- Block execution (`BLOCKED`)
- Require operator review (`REVIEW_REQUIRED`)
- Allow execution (`PASSED`)

No downstream component may bypass or override a safety gate decision.

## 6. Kill Switch and Maintenance Mode Are Hard Blocks

Two global safety conditions always block execution:
- **Kill switch** (`kill_switch_active=true`): All execution is blocked. This
  is an emergency stop.
- **Maintenance mode** (`maintenance_mode=true`): Automatic and manual execution
  is blocked to allow safe system maintenance.

## 7. Battery Fallback and Inverter Cap Protect Hardware

The safety gate enforces two hardware-level protection rules for ON proposals:
- **Battery fallback block**: If battery voltage is at or below the grid fallback
  threshold, ON proposals for discretionary loads are blocked. This prevents
  grid consumption and protects battery health.
- **Inverter load cap block**: If the projected total load (current + new) would
  exceed the inverter's rated capacity, the proposal is blocked. This protects
  the inverter from overload.

Life-support loads (pond aeration) receive special treatment: at low battery,
they are not hard-blocked but instead require operator review (`REVIEW_REQUIRED`).

## 8. Readiness/Health/Cooldown Protect Devices

Three device-level checks protect individual loads:
- **Readiness**: Ensures the load is allowed to switch at the current time
  (time windows, voltage thresholds, weather conditions).
- **Health**: Ensures the device's observed state is healthy (not stale,
  mismatched, or unreachable).
- **Cooldown**: Ensures sufficient time has elapsed since the last switch
  operation to prevent rapid cycling.

Any of these failing results in a hard `BLOCKED` decision.

## 9. Pond/Aeration/Life-Support Remains Special Priority

Pond aeration is life-support, not a discretionary convenience. The safety gate
reflects this: during battery fallback, aeration loads are flagged for operator
review rather than hard-blocked. The operator can choose to activate aeration
even in low-battery conditions if fish life is at risk.

This special treatment is limited to loads explicitly identified in the
`life_support_load_ids` tuple.

## 10. ML Control Remains Deferred

Machine learning models must not directly control devices without separate
safety-reviewed approval per ADR-0003. ML control is a distinct milestone from
ML advisory. Before any ML model can directly influence relay states:
- Independent safety review must approve
- Shadow/advisory mode validation must complete
- Controlled rollout with kill-switch capability must be in place
- Operator opt-in must be confirmed

ML control is deferred and is NOT in scope for any current or near-term PR.
