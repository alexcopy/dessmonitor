# Independent Phase 1A Acceptance Recheck — Safety Invariants

## 1. Review metadata and source identity

| Item | Result |
|---|---|
| Review date | 2026-11-03 |
| Repository root | `/Users/alexred/Projects/Python/dessmonitor` |
| Branch | `feat/adaptive-output-control` |
| HEAD | `b19cd7a1c5cd71d8c56676f5ed2aeba286c13791` |
| Python | 3.14.7 |
| Expected evaluator SHA-256 | `55aba07464ce444c565fcd86ce97e3554ee88edc3826990b615fa50b67990349` |
| Actual evaluator SHA-256 | `55aba07464ce444c565fcd86ce97e3554ee88edc3826990b615fa50b67990349` |
| Expected test SHA-256 | `23909cfd05fb65b76cf77401ec7237998ec01c405b4913664eb1ec4e16817909` |
| Actual test SHA-256 | `23909cfd05fb65b76cf77401ec7237998ec01c405b4913664eb1ec4e16817909` |

Both expected hashes match the files read directly from the working tree. The source and test files are untracked, so this review inspected their contents and did not rely on `git diff HEAD`. The complete latest safety re-review, approved design, and implementation plan were read. No implementation or test file was modified.

## 2. Verdict

**CHANGES_REQUIRED**

The corrections to the previously reported discharge-current, escalation-history, terminal-sag, PV-threshold, and unknown-mode findings are present and pass the independent scenarios below. A remaining current-safety defect still permits `CONSIDER` while an applicable current safety bound is unknown or cannot be checked. That contradicts the requested invariant that unknown current safety cannot authorize battery operation. An additional minor public-model immutability gap is recorded in section 8.

The evaluator remains shadow-only and has no runtime wiring. Thus the findings do not constitute evidence of a live hardware action; they do prevent acceptance of its current candidate/eligibility semantics.

## 3. Current measurement validity versus current safety

### Verified behavior

- `OutputPolicyBaseline` leaves charge and discharge limits as `None` by default; it does not invent a hardware current rating.
- A finite high discharge measurement is not labeled sensor-invalid merely because it is high. With an approved limit, a violation is reported as `HIGH_BATTERY_DISCHARGE` and the classification is `INELIGIBLE`.
- A configured discharge violation remains available to sag analysis. Independent collapse probes at discharge readings of 0, 49, 50, 85, 100, 300, and 500 A all retained `FAST_VOLTAGE_SAG` and the same 11:05 expiry in the short-trial scenario. With no approved discharge limit, positive readings are marked `UNKNOWN_BATTERY_CURRENT_SAFETY`; with an approved 100 A limit, values through 100 A were accepted at the boundary and 300/500 A were blocked as high discharge.
- Negative current values are rejected as invalid measurements. The evaluator does not derive battery current, nominal battery voltage, efficiency, or battery watts from PV/output power. `DEMAND_EXCEEDS_PV` is a context reason only.

### BLOCKER C-01: unknown current safety can still return `CONSIDER`

`_current_safety` (`app/control/adaptive_output_policy.py:592-607`) sets `current_limit_unknown` only when the discharge limit is absent **and a positive discharge sample exists**. `_assess_battery` (`:1022-1032`) and `_line_can_be_considered` (`:1099-1101`) then accept the all-zero case as eligible. Zero observed discharge in Line Mode does not establish an approved safe discharge limit for the proposed battery-mode operation.

Minimal reproducer, using the checked-in test fixtures with adequate stable history and otherwise valid solar/voltage evidence:

```python
r = evaluate(
    sample(discharge=0.0),
    baseline(max_battery_discharge_a=None, max_battery_voltage_v=30.0),
    established_line_history(),
)
```

Observed result: `CONSIDER`, `BatteryEligibility.ELIGIBLE`, with no unknown-current-safety reason. This is a candidate despite the missing approved discharge limit. The boundary grid independently returned `CONSIDER` at 0 A with a `None` discharge limit.

The safety state must remain unknown/blocking when the discharge safety limit needed to evaluate a battery candidate is unapproved, even if the present Line-mode reading happens to be zero. A zero sample is not a hardware limit.

### Charge-current checks are also incomplete

`_current_safety` checks charge current only when `max_battery_charge_a` is set **and** the measured charge value is non-`None` (`:599-602`). It has no unknown-charge-limit result. `_critical_missing` (`:641-657`) requires `battery_current_dis` but not `battery_current_chg`; `_measurement_issues` (`:575-589`) rejects negative readings but does not detect simultaneous positive charge and discharge channels.

Independent probes with valid stable history produced:

| Input | Result |
|---|---|
| `battery_current_chg=500 A`, `battery_current_dis=0 A`, `max_battery_charge_a=None`, approved discharge limit | `CONSIDER`, `ELIGIBLE`, no current-safety reason |
| `battery_current_chg=None`, `max_battery_charge_a=50 A`, `battery_current_dis=0 A` | `CONSIDER`, `ELIGIBLE`, no current-safety reason |
| Charge and discharge both 100 A, both configured limits 500 A | `CONSIDER`, `ELIGIBLE`, no current or invalid-measurement reason |

The first two results do not establish whether an applicable charge-current bound is respected. The third accepts mutually opposing directional measurements without an inconsistency reason; callers need a documented telemetry contract if those fields are not mutually exclusive. These conditions reinforce C-01 and need explicit unknown/inconsistent handling before a candidate can be accepted.

### Current-test integrity

The 58-test suite covers discharge-current uncertainty, configured discharge violations, and sag independence well (`test_counterexample_unknown_current_limit_never_authorizes_positive_discharge`, `test_counterexample_configured_current_violation_is_not_invalid_measurement`, and `test_configured_current_limit_violation_remains_sag_evidence`). It does not exercise charge-current uncertainty, missing charge current under a configured limit, or contradictory charge/discharge channels. The test helper at `tests/test_adaptive_output_policy.py:36-61` defaults `charge=0.0`; no test varies charge current. The zero-discharge/no-approved-limit case is also absent from the tests and is why C-01 escaped the passing suite.

## 4. Escalation and history coverage

The required history calculation at `app/control/adaptive_output_policy.py:728-744` now includes the maximum capped restriction, escalation lookback, and the greatest relevant detection interval. Under the default baseline it returns 28,800 seconds (8 hours), not the earlier five-hour horizon. `_history_coverage` (`:747-756`) requires a sample at or before the horizon and continuous coverage within the configured continuity gap.

An independent reconstruction of failed-trial events ending at 09:15, 11:10, and 13:10 produced:

| History supplied to 16:00 evaluation | Decision | Expiry | `INSUFFICIENT_HISTORY` |
|---|---|---|---|
| Exactly 480 minutes, complete 2-minute cadence | BLOCK | 16:10 | no |
| 479 minutes | BLOCK | 16:10 | yes |
| 300 minutes | BLOCK | none reconstructed | yes |

The exact-horizon case stays blocked inside the 16:10 escalated lockout. Truncated data cannot establish that the restriction has expired. `_lockout_expiry` (`:961-976`) computes expiries from the historical event timestamps and takes the latest still-active expiry, rather than shortening it based on later healthy voltage or PV. Existing boundary tests also check exact configured lockout expiry and custom horizon inputs (`test_counterexample_escalated_lockout_needs_its_full_lookback`, `test_history_coverage_exact_truncated_and_truncated_old_data`, and `test_n02_history_coverage_includes_escalated_restriction_duration`).

No fixed five-hour cutoff remains in the evaluator. The history horizon is parameter-derived. Invalid measurements and duplicate timestamps outside the required window are excluded; duplicates inside it are unknown. A future-dated history observation remains conservatively unknown. There is no unresolved history/lockout blocker in the inspected defaults or probes.

## 5. Voltage sag and recovery

`_failed_trial_events` (`:883-902`) distinguishes short failed trials from longer inverter sessions. `_trial_sag` (`:905-934`) evaluates short trial evidence. `_segment_sag` (`:820-845`) evaluates the settled terminal window of longer sessions, requiring at least three contiguous valid observations and a final pair within the configured sag window. `_sag_pair` (`:847-863`) preserves evidence when load falls and rejects a sufficiently rising, confounding load. `FAST_VOLTAGE_SAG`, voltage drop/slope evidence, and the reconstructed expiry are reported rather than relying on `BLOCK` alone.

Verified with the complete suite and independent current variations:

- Short transition-settling collapse and 12- and 30-minute terminal collapse are detected with `FAST_VOLTAGE_SAG` and an explicit cooldown expiry.
- A three-hour stable battery session and a settled initial voltage step do not create a failed-trial lockout.
- A recovered dip several hours before the end is outside the recent terminal window and is not reported as current sag.
- A collapse while output load decreases remains detected.
- A rising-load excursion inside the comparison window confounds the pair and does not create a false sag.
- Telemetry gaps do not connect noncontiguous voltage samples into a fabricated slope.
- Returning to Line Mode preserves a confirmed failed-trial lockout; an ordinary voltage rebound after a healthy battery session is classified as rebound rather than recharge/sag.

Relevant tests include `test_short_and_long_terminal_sags_have_evidence_but_recovered_dips_do_not`, `test_counterexample_old_recovered_dip_is_not_current_sag`, `test_counterexample_falling_load_does_not_erase_confirmed_voltage_collapse`, `test_temporary_rising_load_inside_terminal_window_confounds_sag`, `test_long_session_telemetry_gap_does_not_connect_voltage_samples`, and `test_rebound_after_invert_to_line_is_not_recharge`.

No unresolved sag defect was reproduced in this pass. The illustrative voltage/current values are algorithmic probes, not hardware measurements or approved device limits.

## 6. Solar productivity and measured load

`_assess_solar` (`:660-712`) classifies the current PV reading before trend classification and explicitly returns WEAK below `productive_pv_w` (`:679-680`, `:700-705`). The historical median cannot upgrade a current reading below the productive threshold. Fresh forecast quality is required by `_line_can_be_considered` (`:1099-1101`).

Independent PV probes returned:

| PV evidence | Classification/result |
|---|---|
| Historical 300 W followed by six 800 W samples, current 460 W, productive threshold 500 W | WEAK, BLOCK; no `PRODUCTIVE_SOLAR` |
| Stable 499 W | WEAK, BLOCK |
| Stable 500 W | STABLE, CONSIDER when all other prerequisites are met |
| Stable 501 W | STABLE, CONSIDER when all other prerequisites are met |
| Declining productive PV | DECLINING and blocked |

An otherwise safe 1000 W measured inverter load with 700 W PV remains `CONSIDER` and carries `DEMAND_EXCEEDS_PV`; the advisory reason alone does not veto. Measured output at and above the configured 1500 W high-load threshold returns BLOCK with `HIGH_MEASURED_LOAD`. The evaluator consumes `output_power_w`; no Tuya total is imported or used as a replacement. The 1499 W case remains below the configured 1500 W high-load threshold, consistent with the current rule.

Relevant tests: `test_historical_pv_median_cannot_override_weak_current_reading`, `test_stable_pv_requires_productive_boundary`, `test_n05_improving_pv_below_productive_threshold_cannot_consider`, `test_demand_exceeding_pv_is_advisory_when_other_evidence_is_safe`, `test_n04_demand_exceeds_pv_advisory_alone_does_not_veto`, and `test_measured_inverter_load_blocks_independently_of_pv`.

No unresolved PV or load defect was reproduced.

## 7. Unknown modes and observed transitions

`_mode_segments` (`:759-777`) terminates segments at unknown modes and gaps. `_mode_change_times` (`:780-796`) counts only confirmed adjacent known transitions. `_mode_history_uncertain` (`:799-806`) blocks when recent anti-flap history contains an unknown mode or fails to cover the configured window. Confirmed earlier churn still feeds `_churn_lockout_expiry` (`:979-990`); unknown observations do not invent a transition or expiry.

The current tests cover preserved churn through unknown samples, unknown intervals without fabricated expiry, Fault history followed by insufficient recovery, and known modes separated by unknown intervals. An independent probe replaced one observation seven hours old with `Fault` in otherwise complete stable Line history; it remained `CONSIDER` and did not return `MODE_HISTORY_UNCERTAIN`. A recent Fault interval still blocks.

Relevant tests: `test_counterexample_recent_unknown_mode_interval_blocks_without_fake_expiry`, `test_confirmed_churn_expiry_survives_unknown_modes_without_new_expiry`, `test_known_modes_separated_by_unknowns_do_not_fabricate_transitions`, `test_fault_history_and_short_known_recovery_do_not_prove_stability`, `test_n06_consecutive_unknown_modes_preserve_lockout`, and `test_unknown_modes_do_not_hide_known_flapping`.

No unresolved unknown-mode defect was reproduced.

## 8. Baseline and decision-model findings

- `max_battery_voltage_v` is optional and defaults to `None` (`OutputPolicyBaseline`, `:161-202`). It is reported as unknown and blocks battery eligibility/positive consideration when absent. An observed value above the configured upper bound blocks and is not misclassified as an invalid sensor reading.
- `_baseline_is_valid` (`:1078-1085`) rejects enter-at-or-below-exit and a configured maximum voltage below the enter threshold. The evaluator emits `INVALID_BASELINE`; contradictory baseline thresholds do not produce `CONSIDER`.
- Lower guards use the exit threshold in Invert Mode and enter threshold in Line Mode (`:1088-1092`). Equality is accepted by the `<` lower guard and `>` upper guard; configured current maxima similarly allow equality and reject strictly greater values.
- SOC is not used for eligibility. The immutable input/output models are frozen dataclasses. Evaluator-created results use tuples for reason codes/evidence, frozen nested assessments, `shadow_only=True`, and `command_executed=False`.
- `OutputPolicyDecision.__post_init__` (`:289-315`) rejects `shadow_only=False` and `command_executed=True`; the public constructor cannot represent an executed command.
- `CONSIDER` is reached in a legitimate fully supported scenario with fresh telemetry/forecast, stable productive PV, adequate history/dwell, an approved current limit, and valid voltage baselines. It remains a shadow recommendation.

### MINOR M-01: manually constructed result can contain mutable collections

The public frozen `OutputPolicyDecision` constructor does not validate or normalize `reason_codes` and `supporting_evidence` to tuples. A caller can pass a list, create the frozen object, then mutate the decision contents through that list. Minimal reproducer: copy a valid evaluator result's fields, replace `reason_codes` with a list, construct `OutputPolicyDecision`, append `COOLDOWN` to the list; the frozen decision then reflects the appended reason. This does **not** affect results returned by `evaluate_output_policy`, which constructs tuples, so it is minor and not the approval blocker. It does mean immutability is shallow for arbitrary public-constructor callers.

## 9. Test integrity and independently executed probes

The complete offline suite independently ran **58 tests: 58 passed, 0 failed**. Test inputs are chronological and helper histories are sorted by observation time; positive candidates use explicit `battery_current_dis=0.0` and sufficient history. Existing tests assert exact reasons, classifications, and expiries for the corrected safety scenarios rather than merely asserting BLOCK. The previous review's fixture defects around future/overlapping history and unrelated-blocker assertions have been corrected in the checked-in test file.

Coverage verified includes HOLD and CONSIDER reachability, exit and enter voltage guards, fresh forecast, full mains dwell, escalated and exact history boundaries, current violations remaining sag evidence, current PV boundaries, anti-flap lockouts, SOC irrelevance, immutable evaluator outputs, and shadow-only constructor rejection.

Independent probes executed in this review (not only the original test assertions):

1. Current grid: discharge 0/49/50/85/100/300/500 A with no approved limit and with a 100 A limit. This confirmed the zero-current unknown-bound defect C-01 as well as correct handling above an approved limit.
2. Same short voltage collapse at each of 0/49/50/85/100/300/500 A. Every case returned `FAST_VOLTAGE_SAG` and expiry 11:05, including current-limit violations/unknowns.
3. Escalation events ending 09:15, 11:10, 13:10 evaluated 16:00 with 480/479/300 minutes of history. Exact horizon rebuilt 16:10; one minute less marked insufficient; five hours could not reconstruct expiry.
4. Historical 300 W followed by six 800 W samples, current PV 460 W; 499/500/501 W productive boundaries; declining PV; 1000 W output/700 W PV advisory candidate; and 1499/1500/2000 W measured-load boundaries.
5. A seven-hour-old Fault observation in otherwise full Line history, which did not permanently block consideration.
6. Current data probes: no discharge limit with zero measured discharge; 500 A charge with no approved charge limit; missing charge measurement with a configured charge limit; and simultaneous 100 A charge/discharge within configured 500 A limits. These reproduce the current-safety finding in section 3.
7. BLOCK, HOLD, and CONSIDER evaluation paths while `open`, `socket.socket`, `subprocess.Popen`, and `time.time` were patched to raise. All three paths completed without invoking them.

The suite does not currently include a test for zero discharge with an unapproved discharge-current limit, positive charge with an unapproved charge limit, missing charge current under a configured limit, or contradictory charge/discharge values. The test helper hardcodes `charge=0.0`; current safety tests principally cover discharge. No mutation campaign was run during this independent recheck; the code review and independent parameter probes above are the mutation-independent evidence.

## 10. Side effects and determinism

The evaluator's imports are limited to `dataclasses`, `datetime`, `enum`, and `math`; it has no DESS, Tuya, network, database, YAML, subprocess, or filesystem integration. `evaluate_output_policy` requires caller-supplied `evaluated_at`; no system clock read is present. There is no module-level mutable policy state. Evaluation derives fresh local lists/tuples and returns frozen result models.

The no-I/O monkeypatch probe exercised BLOCK, HOLD, and CONSIDER paths successfully. Existing deterministic equality coverage passed; the function does not read time or external state, and duplicate timestamps are conservatively classified as unknown. No runtime wiring or YAML/database changes were present in Git status. No production service, hardware, or network was contacted.

## 11. Remaining BLOCKER/MAJOR findings

### BLOCKER C-01 — current-limit unknownness is not fail-closed

- **Exact source:** `app/control/adaptive_output_policy.py:592-607` (`_current_safety`); `:1022-1032` (`_assess_battery`); `:1099-1101` (`_line_can_be_considered`). Charge input gaps are at `:599-602`, `:641-657`, and contradictory-value validation at `:575-589`.
- **Minimal reproduction:** healthy Line snapshot/history, `battery_current_dis=0.0`, `max_battery_discharge_a=None`, valid approved voltage thresholds and upper voltage bound. Returns `CONSIDER` / `ELIGIBLE` without unknown-current reason.
- **Additional reproductions:** (a) 500 A charge, zero discharge, no approved charge limit; (b) charge measurement `None` with a configured charge limit; (c) both charge/discharge at 100 A with both limits at 500 A. Each returns `CONSIDER` / `ELIGIBLE` and no current-safety or invalid-measurement reason.
- **Required disposition:** keep missing/unapproved applicable current safety unknown and prevent `CONSIDER`/healthy `HOLD`; reject or explicitly classify contradictory directional readings unless the input telemetry contract establishes they can coexist. Do not fabricate replacement hardware ratings.

No other BLOCKER or MAJOR implementation finding was confirmed in this recheck.

## 12. Operational prerequisites (separate from code defects)

These remain intentionally outside Phase 1A and are not the basis for this verdict:

- Real inverter model/firmware, battery chemistry/BMS-approved current and voltage ranges, and output-source read/write capability remain unknown. No threshold should be inferred from test fixtures.
- `max_battery_voltage_v` and current limits must remain operator-approved inputs; the source defaults them to unknown. The upper-voltage input is an optional extension and needs architectural ownership before any operational use.
- Runtime wiring, TimescaleDB replay/cadence, minute-precision sunrise/sunset sourcing, and hardware validation are later-phase work. They are not required to approve the purity of an offline evaluator and were not performed here.

## 13. Final recommendation

Do not approve Phase 1A yet. Correct C-01 and add focused regressions for zero current with an unknown discharge bound, unapproved/missing charge-current safety where configured or applicable, and contradictory directional current observations. Preserve the independently verified sag behavior across current boundaries, the exact escalation history, advisory-only `DEMAND_EXCEEDS_PV`, current-PV productive gate, unknown-mode handling, and shadow-only boundary. Then rerun this acceptance recheck. M-01 may be addressed by rejecting or tuple-normalizing mutable collections in the public decision constructor; evaluator-returned results are already tuple-backed.
