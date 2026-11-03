# Independent final acceptance — Phase 1A C-01 and M-01

## Review metadata and source identity

- Review date: 2026-11-03; repository: `/Users/alexred/Projects/Python/dessmonitor`.
- Branch: `feat/adaptive-output-control`; HEAD: `b19cd7a1c5cd71d8c56676f5ed2aeba286c13791` (one commit ahead of its upstream).
- Evaluator SHA-256: `e7902b1c620b6ec9657db6e49567a76dabd4d27dadd4e4c2307ec18d9e10c347` — matches requested hash.
- Test SHA-256: `d1f18f7084e111873b02b731580eb136f3b60117c5042f61d3756991d5cdad57` — matches requested hash.
- Both files were untracked at review time, so HEAD alone does not identify their contents. The previous `ADAPTIVE_OUTPUT_PHASE1A_ACCEPTANCE_RECHECK.md` was read. No implementation or test changes were made.

## Verdict: CHANGES_REQUIRED

C-01 passes the focused checks and no material evaluator safety regression was found. M-01 remains incomplete: the public decision constructor accepts mutable objects in fields other than the two normalized collections, including inside a nested assessment. This contradicts the requested deep immutability guarantee, even though evaluator-generated instances use normal immutable values. Approval is not recommended until that gap is resolved and covered through public constructors.

## C-01 independent reproduction

All results below are from public evaluator calls with fresh, continuous, otherwise adequate history unless stated. Fixture values are hypothetical, not approved hardware ratings. `FRESH` describes timestamp quality; it does not by itself assert that every current field is present or safe. Current readings were included in output evidence; confirmed limits and sag observations were processed as safety evidence, while absent or contradictory current safety was explicitly unverified. No lockout was present in the baseline grid.

| Case | Decision / battery | Relevant reasons and evidence |
|---|---|---|
| Line and Invert, discharge 0, discharge limit None | BLOCK / UNKNOWN in each | `UNKNOWN_BATTERY_CURRENT_SAFETY`, evidence `discharge_limit_unapproved`; no healthy Invert reason |
| Line and Invert, discharge missing | BLOCK / UNKNOWN in each | `UNKNOWN_BATTERY_CURRENT_SAFETY`, `UNKNOWN_TELEMETRY`, `discharge_reading_missing`; displayed current `UNKNOWN` |
| Line and Invert, charge missing, charge limit 65 A | BLOCK / UNKNOWN in each | `UNKNOWN_BATTERY_CURRENT_SAFETY`, `charge_reading_missing`; displayed charge `UNKNOWN` |
| Charge 0.2 A, charge limit None, zero discharge | BLOCK / UNKNOWN | `UNKNOWN_BATTERY_CURRENT_SAFETY`, `charge_limit_unapproved` |
| Charge 0.3 A and discharge 0.2 A simultaneously, both limits approved, Line and Invert | BLOCK / UNKNOWN in each | `INCONSISTENT_BATTERY_CURRENT`, `UNKNOWN_BATTERY_CURRENT_SAFETY`, `conflicting_charge_and_discharge`; no `INVALID_MEASUREMENT` |
| Discharge 69.9 and 70.0 A, limit 70 A, Invert | HOLD / STABLE | `INVERT_MODE_OBSERVED`, no `HIGH_BATTERY_DISCHARGE` |
| Discharge 70.5 and 71.2 A, limit 70 A | BLOCK / INELIGIBLE | `HIGH_BATTERY_DISCHARGE`, no `INVALID_MEASUREMENT` |
| Charge 59.9 and 60.0 A, limit 60 A, Line | CONSIDER / ELIGIBLE | `PRODUCTIVE_SOLAR`, no `HIGH_BATTERY_CHARGE` |
| Charge 60.5 and 61.2 A, limit 60 A | BLOCK / INELIGIBLE | `HIGH_BATTERY_CHARGE`, no `INVALID_MEASUREMENT` |
| Fully supported Line, zero charge and discharge, approved discharge limit 75 A | CONSIDER / ELIGIBLE | `PRODUCTIVE_SOLAR`, no unknown-current reason; shadow only |

Positive historic charge 0.2 A at 479 and exactly 480 minutes with a current zero reading and no charge limit produced BLOCK / UNKNOWN and `charge_limit_unapproved`; at 481 minutes it produced CONSIDER / ELIGIBLE without that reason. The default window is eight hours and inclusive at its start (`_points_in_history_window`, lines 675–681); its duration is baseline-derived, not a universal fixed eight hours. This is deliberately conservative: any strictly positive historical charge or simultaneous currents in the window has the stated effect, without a noise tolerance. Actual sensor semantics remain unverified.

A confirmed short-trial collapse was independently reproduced at 0, 49, 50, 85, 100 and 300 A, with both unknown discharge limit and limit 70 A. All twelve results were BLOCK / INELIGIBLE, with `FAST_VOLTAGE_SAG`, `COOLDOWN`, and identical 11:05 UTC expiry; none marked the readings invalid. Unknown limit added `UNKNOWN_BATTERY_CURRENT_SAFETY`; 85/100/300 A with the approved 70 A limit additionally gave `HIGH_BATTERY_DISCHARGE`. Voltage samples continued to establish sag despite current uncertainty or violation. Separate active-lockout probes with missing discharge, missing charge, unknown limit, simultaneous currents and over-limit charge each preserved BLOCK / INELIGIBLE, sag and cooldown with the same expiry and the applicable current-specific reason. Thus the protective lockout was independently confirmed effective when current evidence is incomplete or unsafe.

Production baseline current limits default to `None` (lines 200–202), with no invented fallback. Exact values 90 A, 70 A, 60 A, 75 A and 65 A here are fixture/probe values only. A missing charge reading produces `UNKNOWN_BATTERY_CURRENT_SAFETY` even though timestamp quality can remain FRESH; it is not represented as verified current safety.

## M-01 public constructors

- Passing caller lists for `reason_codes` and `supporting_evidence` copies them to tuples; later appends/clears do not alter the result. Invalid reason values are rejected; non-`Evidence` items are rejected; `command_executed=True` raises `ValueError`. These were exercised using the public `OutputPolicyDecision` constructor.
- **Unresolved defect, M-01:** `OutputPolicyDecision.__post_init__` validates the two collections but does not validate or freeze the other fields (`app/control/adaptive_output_policy.py:293–325`). Its nested assessment classes likewise accept arbitrary mutable values (`:273–289`). A public constructor reproduction using otherwise valid evaluator output:

  ```python
  values = []
  assessment = SolarOpportunityAssessment(classification=SolarOpportunity.STABLE, pv_trend_w=values)
  decision = OutputPolicyDecision(**{**valid_result.__dict__, "solar_opportunity": assessment})
  values.append(42)
  assert decision.solar_opportunity.pv_trend_w == [42]
  ```

  Likewise, a list directly supplied as `battery_eligibility`, `solar_opportunity`, or `data_age_seconds` remains shared and mutable. Type annotations are not runtime enforcement. This is a material gap in the explicitly requested deep-immutability public-model contract, not evidence that normal evaluator results mutate. Validate nested assessment types and their fields, and decision scalar/assessment fields, or otherwise ensure no caller-owned mutable data can enter; verify with public-constructor tests. No global mutable state was identified in the evaluator.

## Previously accepted safety behavior

The full suite passed targeted smoke tests for short-trial collapse, long-session terminal collapse, recovered historical dips, failed-trial escalation and exact expiry, falling-load sag, current PV below productive threshold, advisory-only `DEMAND_EXCEEDS_PV`, recent unknown modes, and stale telemetry. The independent sag grid and active-lockout cases above also directly exercised current/sag interaction. Tests assert specific reasons, battery class and/or expiry for these scenarios, rather than treating BLOCK alone as proof. The prior review contains additional earlier independent history/escalation probes; they were not rerun wholesale in this focused pass.

Repeated identical evaluator calls compared equal. Imports and inspected evaluation paths show no external I/O or clock read; separate BLOCK, HOLD and CONSIDER calls completed with `builtins.open`, `socket.socket`, `subprocess.Popen`, and `time.time` patched to raise. This is targeted evidence, not a proof against every possible side effect. The public constructor rejected command execution and evaluator outputs carried `shadow_only=True`, `command_executed=False`.

## Test integrity and execution

- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`: **66 total, 66 passed, 0 failed**; no test suite edits.
- Inspected seven `test_c01_...` tests and `test_m01_decision_constructor_normalizes_collections` (lines 114–255). They call the public evaluator or public constructor; relevant cases check specific reasons and classifications, and the positive path proves `CONSIDER` is reachable. The unknown-limit cases explicitly supply `None`. The `90.0` discharge value appears in the test baseline helper and is explicitly labeled a fixture; the production baseline defaults to `None`.
- The current tests isolate the intended current condition with adequate otherwise healthy evidence; the sag test also checks independent sag and cooldown. Additional independent probes used different current values, 300 A sag, and the history expiry boundary. The eight tests do not cover mutable values inside nested assessment or other decision fields; their collection-specific assertions pass but are insufficient to establish full deep immutability. This gap is the M-01 finding above.
- The independent probe commands used the public evaluator and constructors and did not change the suite. No production hardware or network was contacted.

## Operational prerequisites

Obtain hardware/BMS-approved current and voltage bounds before operational use; fixture numbers are not approvals. Confirm whether the directional sensors can both legitimately report positive values and characterize their measurement semantics before changing the conservative uncertainty rule. Unknown hardware bounds are supported safely in this shadow-only phase and are not themselves a failure. Hardware validation and runtime integration are outside this focused offline review.

## Final recommendation

**CHANGES_REQUIRED.** Preserve the passing C-01, regression, deterministic and shadow-only behavior while closing the public-model deep immutability gap and adding constructor-level coverage. Do not infer deployment readiness from this offline acceptance review.
