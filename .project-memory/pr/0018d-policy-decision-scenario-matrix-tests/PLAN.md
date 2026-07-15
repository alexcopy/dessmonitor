# PR 0018D — Policy Decision Scenario Matrix Tests

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `b9671e00a381e0522fec2bce566c8bb1b0e326ad` |
| Branch | `git branch --show-current` | `0018d-policy-decision-scenario-matrix-tests` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0018d-policy-decision-scenario-matrix-tests` and working tree is clean.

## 2. Purpose

PR 0018D adds **scenario matrix documentation and validation tests** for the pure
deterministic policy decision engine (`evaluate_policy_decision`). It documents 16
operating scenarios and provides a standalone Python test script that asserts the
engine's reason strings and decision behavior for each scenario.

This PR locks real operating behavior. It does NOT add runtime wiring, command proposal,
command queue, execution, automation, ML control, weather fetch, or deployment changes.

## 3. Product Context

1. PR 0018A documented policy engine operating boundaries.
2. PR 0018B added passive policy engine model types.
3. PR 0018C implemented `evaluate_policy_decision()` — the pure deterministic brain.
4. PR 0018D **tests** the brain against documented scenarios to ensure the:
   - battery fallback protection
   - inverter max load cap protection
   - pond/fish aeration life-support invariant
   - forecast-aware morning minimum strategy
   - bad all-day forecast conservation
   - high-voltage daytime spending
   - weather adjustment conservation/spending
   - neutral default behavior
5. The test script is standalone, uses only local Python, and requires no network/secrets/config.
6. Manual relay/switch ON/OFF remains available and unchanged.
7. Pump automation remains obsolete and disabled by default (PR 0008).
8. ML advisory may be used later; ML control remains disabled.

## 4. Current Repository State

| Capability | File | Status |
|---|---|---|
| Readiness evaluator (pure function) | `app/control/readiness.py` | Implemented (PR 0014) |
| Health evaluator (pure function) | `app/control/health.py` | Implemented (PR 0015) |
| Schedule profile model | `app/control/schedule_profile.py` | Implemented (PR 0016) |
| Weather adjustment evaluator | `app/control/weather_adjustment.py` | Implemented (PR 0017) |
| Policy engine operating boundaries | `.project-memory/POLICY_DECISION_ENGINE.md` | Documented (PR 0018A) |
| Passive policy engine models (7 types) | `app/control/policy_models.py` | Implemented (PR 0018B) |
| Policy decision engine (pure function) | `app/control/policy_decision.py` | Implemented (PR 0018C) |
| **Scenario matrix documentation and tests** | `.project-memory/POLICY_DECISION_SCENARIOS.md` + `scripts/check-policy-decision-scenarios.sh` | **This PR** |

### 4.1 Grep Evidence Summary

**`evaluate_policy_decision`** exists in `app/control/policy_decision.py`, exported from
`app/control/__init__.py`, and passes all validation checks (PR 0018C).

**`CommandProposal` and `CommandQueue`** do not exist in `app/control/` — good (deferred).

**All existing validation scripts pass** (14 scripts, including the PR 0018C decision engine check).

## 5. Required Scenario Matrix Document

### File: `.project-memory/POLICY_DECISION_SCENARIOS.md`

The document must describe these 16 scenarios with inputs, expected reason, and
expected decision behavior:

| # | Scenario | Key Input Condition | Expected Reason |
|---|---|---|---|
| 1 | No loads | Empty loads tuple | `no-loads` |
| 2 | Battery near 24.0–24.5V fallback threshold | Voltage <= grid_fallback_voltage, discretionary ON load available | `battery-fallback-protection` |
| 3 | Inverter current load at/above 2500W | current_total_load_watts >= max_total_load_watts, discretionary ON load available | `inverter-load-cap-protection` |
| 4 | Cloudy morning, sunny forecast in 2 hours | morning_strategy_active=true, forecast_improves_later_today=true, voltage <= morning_minimum_voltage | `morning-minimum-hold-for-sun` |
| 5 | Cloudy morning, bad all-day forecast | bad_forecast_all_day=true, discretionary ON load available | `bad-forecast-conserve` |
| 6 | Sunny day, battery at/above 28.5V | voltage >= high_voltage_spend_threshold, healthy/ready OFF load available | `high-voltage-spend` |
| 7 | Battery near full at 29.5V, optional loads available | voltage >= high_voltage_spend_threshold, multiple OFF candidates exist | `high-voltage-spend` |
| 8 | Candidate load would exceed max_total_load_watts | current + candidate watts > max_w | `high-voltage-spend` or `neutral-no-action` (no candidate fits) |
| 9 | Pond temperature 26°C, aeration OFF, budget available | life_support_active=true, healthy aeration OFF load fits budget | `pond-life-support-aeration` |
| 10 | Pond temperature 26°C, aeration OFF, budget full | life_support_active=true, aeration candidate exists but budget full | `shed-discretionary-for-aeration` |
| 11 | Pond temperature high, discretionary ON, aeration needs budget | life_support_active=true, discretionary ON load can be shed for aeration | `shed-discretionary-for-aeration` |
| 12 | Pond aeration device unhealthy/stale/unreachable | life_support_active=true, aeration load has health=STALE or MISMATCH or UNREACHABLE | `pond-life-support-aeration` (no candidate) or `neutral-no-action` |
| 13 | Weather conserve adjustment | weather_adjustment.decision = PREFER_OFF or FORCE_OFF, shed target exists | `weather-conserve` |
| 14 | Weather spend adjustment | weather_adjustment.decision = ALLOW_ON, healthy/ready OFF load fits budget | `weather-spend` |
| 15 | Weather unknown / neutral behavior | weather_adjustment=None or decision=NO_ACTION, no other rule triggers | `neutral-no-action` |
| 16 | Life-support load must not be first shed target | Battery fallback or inverter cap active, life-support ON load exists alongside discretionary ON | `battery-fallback-protection` or `inverter-load-cap-protection` selecting non-life-support |

## 6. Required Validation Test Script

### File: `scripts/check-policy-decision-scenarios.sh`

This script must be a standalone Python/bash test that:

1. Is executable locally with only Python standard library + app.control.
2. Imports `evaluate_policy_decision` and all passive model types from `app.control`.
3. Creates simple local context classes (e.g. `SimpleVoltageSnapshot`) as needed.
4. Does **not** require pytest.
5. Does **not** require network, secrets, or config files.
6. Does **not** write runtime files.
7. Prints clear scenario names and PASS/FAIL status for each.
8. Exits 0 only if all scenario assertions pass.
9. Exits 1 if any scenario assertion fails.

### Required Assertions

| # | Assertion |
|---|---|
| 1 | Empty/tiny loads → reason `"no-loads"` |
| 2 | Battery at/below fallback voltage with discretionary ON load → reason `"battery-fallback-protection"` |
| 3 | Battery fallback selects non-life-support shed target (life-support ON load remains untouched) |
| 4 | Current load at/above max_total_load_watts with discretionary ON → reason `"inverter-load-cap-protection"` |
| 5 | Current load at/above max, no shed target → safe NO_ACTION |
| 6 | Cloudy morning + later sun with voltage at morning minimum → reason `"morning-minimum-hold-for-sun"` |
| 7 | Bad all-day forecast with discretionary ON → reason `"bad-forecast-conserve"` |
| 8 | High voltage with healthy/ready OFF candidate fitting budget → reason `"high-voltage-spend"` with ALLOW_ON |
| 9 | ALLOW_ON projected_total_load_watts never exceeds max_total_load_watts |
| 10 | Hot pond with aeration OFF fitting budget → reason `"pond-life-support-aeration"` |
| 11 | Hot pond with no budget sheds discretionary before aeration → reason `"shed-discretionary-for-aeration"` |
| 12 | Unhealthy aeration is not selected for ON (no `pond-life-support-aeration` with a target) |
| 13 | Life-support currently-on load is not selected as first shed target |
| 14 | Weather conserve adjustment → reason `"weather-conserve"` or safe NO_ACTION |
| 15 | Weather spend adjustment with candidate → reason `"weather-spend"` |
| 16 | Neutral/default with no triggers → reason `"neutral-no-action"` |

## 7. Concurrent Contract-Preserving Fixes

The plan allows the coder to make **minimal contract-preserving fixes** to
`app/control/policy_decision.py` only if the scenario test reveals a mismatch between
the already-documented 0018C behavior and the actual implementation. Such fixes must:

1. Be the smallest possible change (e.g. adjust a comparison operator, add a missing check).
2. Not change the public function signature.
3. Not change module dependencies.
4. Not add new evaluation rules beyond what PR 0018C already planned.
5. Be documented in CODER_REPORT.txt with the specific reason.

## 8. Static Validation Script for Document Integrity

The `scripts/check-policy-decision-scenarios.sh` must also act as a **static check** when
run without the scenario test flag. In static mode, it must verify:

1. `.project-memory/POLICY_DECISION_SCENARIOS.md` exists.
2. Required scenario names/reason strings are documented in the scenarios doc.
3. The test script contains all critical assertions (at least 12 of 16 from Section 6).
4. No `CommandProposal` or `CommandQueue` exists in the module.
5. No forbidden runtime imports exist.
6. No hardware calls exist.
7. No weather fetch/file/env/network/ML/logging/current-time calls exist.
8. Runtime files not modified (except optional `policy_decision.py` minimal fix if needed).
9. Print clear per-check output.
10. Exit 0 only when all checks pass.

## 9. Allowed Implementation Files

| File | Action |
|---|---|
| `.project-memory/POLICY_DECISION_SCENARIOS.md` | **Create** — scenario matrix documentation (16 scenarios) |
| `scripts/check-policy-decision-scenarios.sh` | **Create** — standalone Python test script + static validation |
| `.github/workflows/validate.yml` | **Edit** — add one validation step |
| `.project-memory/CURRENT_STATE.md` | **Edit** — add PR 0018D section |
| `.project-memory/ROADMAP.md` | **Edit** — mark PR 0018D in roadmap |
| `.project-memory/pr/0018d-policy-decision-scenario-matrix-tests/CODER_REPORT.txt` | **Create** — coder report |
| `app/control/policy_decision.py` | **Edit** only if minimal contract-preserving fix is needed (see Section 7) |

## 10. Forbidden Implementation Files

The coder must **not** edit:
- `run.py`
- `app/service/**`
- `app/devices/**`
- `app/tuya/**`
- `app/monitoring/**`
- `app/ml/**`
- `app/weather/**`
- `app/control/domain.py` (frozen from PR 0009)
- `app/control/relay_mapping.py` (frozen from PR 0010)
- `app/control/energy_policy.py` (frozen from PR 0012)
- `app/control/readiness.py` (frozen from PR 0014)
- `app/control/health.py` (frozen from PR 0015)
- `app/control/schedule_profile.py` (frozen from PR 0016)
- `app/control/weather_adjustment.py` (frozen from PR 0017)
- `app/control/policy_models.py` (frozen from PR 0018B)
- `examples/energy_policy.example.yaml`
- `service/**`
- `shared_state/**`
- Config files, data files
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- `.github/workflows/build-and-deploy.yml`
- Existing validation scripts (other than adding the new step to `validate.yml`)

## 11. CURRENT_STATE.md Update

```
## PR 0018D — Policy Decision Scenario Matrix Tests
PR 0018D documents 16 operating scenarios in `.project-memory/POLICY_DECISION_SCENARIOS.md`
and provides a standalone Python validation script (`scripts/check-policy-decision-scenarios.sh`)
that asserts evaluate_policy_decision reason strings and decision behavior for each scenario.
No runtime wiring added. No command proposal. No execution. No automation.
```

## 12. ROADMAP.md Update

```
- [x] PR 0018D — Scenario matrix and regression tests
```

## 13. Future PR Boundary

PR 0018D explicitly defers:

| Deferred Work | Target PR |
|---|---|
| Command proposal model | 0020 |
| Command queue / manual control API | 0019 |
| Wire policy decisions to command proposal | 0020 |
| Controlled execution with safety gates | 0021+ |
| ML advisory integration | Later |
| ML control (safety gates per ADR-0003) | Much later |
| Runtime wiring | Deferred |

## 14. Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes this plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are LOCKED |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation |

## 15. Boundary Confirmations

- **Scenario matrix documentation and tests only**: No new evaluation logic
- **No runtime wiring**: Not connected to any runtime component
- **No command proposal**: Deferred to 0020
- **No hardware execution**: Does not call any switch/device/Tuya method
- **No config loading**: Does not read YAML, env vars, or files
- **No system clock**: Does not call `time.time` or `datetime.now`
- **No automation enabled**: Pump automation disabled per PR 0008
- **No ML control**: ML advisory is advice-only; ML control deferred per ADR-0003
- **Manual switch control preserved**: All switch methods unchanged
- **Pump automation remains disabled**: Per PR 0008
- **Docker/GitOps**: `build-and-deploy.yml` untouched; external GitOps boundary respected
- **All existing evaluators, models, and engine unchanged** (except optional minimal fix)
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval
