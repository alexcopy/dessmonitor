# Adaptive Output Control, Phase 1A: Independent Safety Re-Review

## 1. Review details and verdict

| Item | Value |
|---|---|
| Review date | 2026-11-02 |
| Branch | `feat/adaptive-output-control` |
| HEAD | `b19cd7a1c5cd71d8c56676f5ed2aeba286c13791` |
| Python | 3.14.7 (system `python3`) |
| Evaluator | `app/control/adaptive_output_policy.py`, 993 lines, untracked, modified 23:06:59 |
| Tests | `tests/test_adaptive_output_policy.py`, 427 lines, untracked, modified 23:13:05 |

**Source hashes verified: yes.** Both match the expected values.

| File | Actual SHA-256 |
|---|---|
| Evaluator | `ff13e66200ee95cdf70a7973d9b9328b90eb685f05dfc528b3d9a58a9070e16d` |
| Tests | `8cb50ce1a85a9789151849918ad3437ec9897233f71e509c8a572fe1dcc37e0b` |

## VERDICT: CHANGES_REQUIRED

The fixes are now in the tree, and every literal scenario from the last review passes on its original values. Different valid inputs still break six of the seven fixes. Two of the remaining defects return `CONSIDER` when they must not, so they are blockers. All 34 tests pass on this code, so the suite does not detect any of these defects.

| Finding | Status | Result |
|---|---|---|
| N-01 Current and sag | **PARTIALLY_FIXED** | Sag parity is fixed. Unknown limits now act as proof of safety (BLOCKER), and configured limits still mark a reading invalid |
| N-02 History coverage | **PARTIALLY_FIXED** | 09:36 and 10:05 are correct. The 5 h horizon omits the escalation lookback, giving CONSIDER inside a lockout (BLOCKER) |
| N-03 Terminal collapse | **PARTIALLY_FIXED** | 12- and 30-minute collapses are restricted. Healthy long sessions are falsely locked out |
| N-04 Demand versus PV | **FIXED** | Advisory only; every tested violation still blocks |
| N-05 Productive threshold | **PARTIALLY_FIXED** | Plain ramps are correct. The gate uses the window median, so measured 460 W can still return PRODUCTIVE_SOLAR and CONSIDER |
| N-06 Unknown modes | **PARTIALLY_FIXED** | Confirmed churn survives and nothing is fabricated. Uncertain history is still treated as stable (CONSIDER at 10:12); the mode-change code wasn't changed |
| F-13 Test integrity | **PARTIALLY_FIXED** | New tests exist, but several are tailored or pass because of an unrelated blocker; 8 of 16 mutations survive |

| Area | Result |
|---|---|
| Physical safety invariants | **FAIL** |
| Test integrity | **FAIL** |
| Independent boundary probes | **FAIL** |
| Positive CONSIDER path | **PASS** |
| Determinism | **PASS** |
| No-I/O safety | **PASS** |

## 2. Method and limits

**Validation run (actual results):**

| Command | Result |
|---|---|
| `py_compile` on both files, with `PYTHONDONTWRITEBYTECODE=1` | exit 0 |
| `unittest discover -s tests -p 'test_*.py' -v` | 34 run, 34 passed, 0 failed |
| `git diff --check` | clean |
| `git status --short` | three untracked paths, listed in section 12 |

**Probe conditions:** UTC, 2 November 2026, sunrise 06:00, sunset 20:00, exit 24.5 V, enter 25.5 V, hysteresis 0.5 V, forecast fresh, 2-minute cadence unless stated. Line samples are 26.0 V, 500 W, 800 W PV and 0 A unless stated. All other limits use defaults. History reaches back at least 5 h. "Lk" is `lockout_expires_at`.

**Limits of this evidence:**

- Voltages and currents are illustrative values for a 24 V system, not measured hardware behaviour.
- Probes are aligned to a 2-minute grid, so some truncated results are affected by where the cut-off falls.
- History PV stays at 800 W before sunrise. Only the last 15 minutes of PV affects classification.
- Earlier code is compared from this review series, not a stored snapshot; the previous version isn't in git.
- The physical plausibility of a discharge reading in Line Mode depends on the installation, so correction C1 needs an architect decision.

**Probe scripts:** in `/private/tmp/claude-501/-Users-alexred-Projects-Python-dessmonitor/89f11ab5-3de5-4bf6-8c85-4f884d2e0cde/scratchpad/`: `rereview.py`, `extra.py`, `io.py`, `mutate.py`. Outputs are `rereview.out`, `extra.out` and `mutate.out`. Mutations were applied in memory only.

## 3. N-01: Battery current and voltage sag: PARTIALLY_FIXED

**Invariant:** a real collapse is detected at any plausible current, and an unknown limit is never evidence of safety.

**Fixed:** all three limits default to `None`. `_voltage_load_measurement_issues` (`:833-840`) keeps high-current samples in sag analysis.

Same collapse at 2000 W: 10:01 25.5 V, 10:02 25.3 V, 10:03 25.0 V, 10:04 24.7 V, 10:05 24.4 V, then Line at 25.8 V.

| Current, limits `None` | Invert 10:05 | Line 10:06 | Line 11:04 | Line 11:05 |
|---|---|---|---|---|
| 49, 50, 85, 100 A (identical) | BLOCK, `FAST_VOLTAGE_SAG`, INELIGIBLE, valid | BLOCK, sag, Lk 11:05 | BLOCK, sag, Lk 11:05 | CONSIDER after expiry |

A different case gives the same kind of result: 180 A at 4000 W, 27.0 to 25.0 V in 3 minutes, Lk 11:04.

### Defect N-01-A (BLOCKER): unknown limit treated as proof of safety

| Input, limits `None`, otherwise healthy | Actual | Expected |
|---|---|---|
| Line, 0 W, 40 A | **CONSIDER** | Not CONSIDER |
| Line, 0 W, 300 A | **CONSIDER** | Not CONSIDER |
| Line, 500 W, 85 A | **CONSIDER** | Not CONSIDER |
| Line, 500 W, 5000 A | **CONSIDER** | Not CONSIDER |
| Same currents only in a history sample | **CONSIDER** | Not CONSIDER |
| Invert, 500 W, 300 A, 5 h | **HOLD**, no warning | Not presented as healthy |

- **Location:** `:542`, `:544`, `:546-553`. Every current check, including the current/output conflict check, is skipped when the limit is `None`. Only a negative reading is still rejected.
- **`INCONSISTENT_BATTERY_CURRENT`** is declared at `:78` and never emitted.
- **Safety impact:** an impossible or dangerous reading now produces a candidate. Before this change, 0 W with 90 or 300 A returned BLOCK.
- **Test impact:** the cases `{"discharge": 500.0}` and `{"output": 0.0, "discharge": 300.0}` were removed from `test_invalid_current_and_power_values_never_consider` (`:384-390`). The remaining 500 A case supplies a limit and doesn't assert the decision.

### Defect N-01-B (MAJOR): configured limit still means "sensor invalid"

| Input | Result |
|---|---|
| `significant=50`, 85 A, Invert 10:05 | `FAST_VOLTAGE_SAG` and `HIGH_BATTERY_DISCHARGE` reported together (good), plus `INVALID_MEASUREMENT` and UNKNOWN eligibility |
| `max=80`, 85 A, Line 10:06 | Lk 11:05 correct, plus `invalid=history.battery_current_dis` |
| Same, Line 11:05 | BLOCK, `INVALID_MEASUREMENT`, for as long as the sample is supplied |
| Healthy Invert, 60 A, `significant=50` | BLOCK, `INVALID_MEASUREMENT` |

- **Location:** `:542-545`.
- **Expected:** a limit violation reports `HIGH_BATTERY_DISCHARGE` and blocks for that reason; it isn't sensor-invalid. A warning threshold shouldn't invalidate the sample either.

## 4. N-02: History coverage: PARTIALLY_FIXED

**Invariant:** when the coverage check passes, the supplied history can rebuild every active restriction.

`_required_history_seconds` (`:660-663`) now returns 18000 s (4 h restriction + 1 h detection).

| Failed trials 08:05, 08:35 (expiry 10:35) | 09:36 | 10:34 | 10:35 |
|---|---|---|---|
| Complete | BLOCK, Lk 10:35 | BLOCK, Lk 10:35 | CONSIDER |
| Truncated 3600 s | BLOCK, `INSUFFICIENT_HISTORY` | same | same |
| Just below (17999 s) | BLOCK, Lk 10:35, `INSUFFICIENT_HISTORY` | same | BLOCK, `INSUFFICIENT_HISTORY` |
| Exact (18000 s) | BLOCK, Lk 10:35 | BLOCK, Lk 10:35 | BLOCK, `INSUFFICIENT_HISTORY` (grid effect) |
| Longer (21600 s) | BLOCK, Lk 10:35 | BLOCK, Lk 10:35 | CONSIDER |

| Churn 09:04-09:16 (expiry 10:16) | 10:05 | 10:15 | 10:16 |
|---|---|---|---|
| Complete | BLOCK, Lk 10:16 | BLOCK, Lk 10:16 | CONSIDER |
| Truncated 3600 s | BLOCK, `INSUFFICIENT_HISTORY` | same | same |
| Just below, exact | BLOCK, Lk 10:16 | BLOCK, Lk 10:16 | CONSIDER or `INSUFFICIENT_HISTORY` |
| Longer | BLOCK, Lk 10:16 | BLOCK, Lk 10:16 | CONSIDER |

Later healthy data doesn't shorten a restriction: 27.5 V and 3000 W PV at 10:34 still returns Lk 10:35. Two samples of history returns BLOCK, `INSUFFICIENT_HISTORY`.

### Defect N-02-A (BLOCKER): horizon omits the escalation lookback

A third failure's lockout depends on earlier failures up to 4 h before it (`:861`, `:866`), and `:661-663` doesn't count that lookback.

Failures end at 09:15, 11:10 and 13:10, so the true lockout is 13:10 + 3 h = 16:10.

| Evaluated at 16:00 | Decision | Lk |
|---|---|---|
| Complete history | BLOCK, sag, cooldown | 16:10 |
| **Exact 18000 s, coverage passes** | **CONSIDER** | **None** |
| 25200 s | BLOCK, sag, cooldown | 16:10 |

- **Safety impact:** a candidate is produced 10 minutes inside an active lockout, with the evaluator reporting that history is sufficient. The 09:15 failure is outside the cut-off, so the multiplier falls from 3 to 2.
- **Expected:** restriction + escalation lookback + detection. On defaults that is about 3 h + 4 h + detection, not 5 h.

### Defect N-02-B (MINOR): data older than the horizon still decides the result

| History | Result |
|---|---|
| 30-minute gap 7.5 h ago, last 5 h clean | BLOCK, `STALE_TELEMETRY`, `INSUFFICIENT_HISTORY` |
| Same history trimmed to 5 h | CONSIDER |
| Invalid voltage sample 9 h ago | BLOCK, `INVALID_MEASUREMENT` |

Conservative, but "history longer than required" can change the decision, and a caller that doesn't trim is blocked permanently (`:518-520`, `:565-566`, `:672`).

## 5. N-03: Terminal collapse: PARTIALLY_FIXED

**Invariant:** confirmed collapse restricts at any session length, and a healthy long session is never classified as failed.

| Case | Input | Decision | Lk | Result |
|---|---|---|---|---|
| A | 5-minute failed trial | BLOCK to 11:04; CONSIDER 11:05 | 11:05 | PASS |
| B | 12 minutes, 25.9 to 24.4 V in the final 3 minutes | BLOCK to 11:11; CONSIDER 11:12 | 11:12 | PASS |
| C | 30 minutes, 25.8 to 24.6 V in the final 4 minutes | BLOCK to 11:29; CONSIDER 11:30 | 11:30 | PASS |
| D | 3 h flat 26.0 V | CONSIDER at 10:46 | None | PASS |
| D4 | 3 h slow decline, about 1 V | CONSIDER at 10:46 | None | PASS |
| **D2** | 3 h healthy, 26.0 to 25.6 V at start, then flat | **BLOCK, sag, cooldown** | **10:58** | **FAIL** |
| **D3** | 3 h, dip to 25.6 V recovered 2 h before the end | **BLOCK, sag, cooldown** | **10:58** | **FAIL** |
| E1 | Terminal drop explained by a 800 to 2500 W load step | CONSIDER at 10:46 | None | PASS |
| E3 | Repeated large load cycles | CONSIDER at 10:46 | None | PASS |
| F1 | 8-minute gap in a healthy session | BLOCK, `INSUFFICIENT_HISTORY` | None | PASS |
| F2 | Collapse across an 8-minute gap | BLOCK, sag | 11:00 | PASS |
| Line | Line drift 26.3 to 25.6 V | CONSIDER, no sag | None | PASS |
| Rebound | Line +0.5 V after healthy Invert | BLOCK, rebound, no sag | None | PASS |

### Defect N-03-A (MAJOR): "terminal" collapse is not terminal, and settling counts

- **Location:** `:793-796`, `:805-830`. Long sessions reuse `_trial_sag`, which takes the largest drop anywhere in the session and includes settling samples. It never checks that the drop is at the end or that voltage stayed low.
- **Threshold behaviour:** a single settling step of 0.29 V gives no lockout; 0.30 V gives Lk 10:58. Voltage was steady for the following 3 h in both.
- **False escalation:** two healthy 1-hour sessions with a settling step return Lk 11:02, a 2 h escalated lockout counted from 09:02.
- **Safety impact:** blocks valid candidates and, worse, overstates the failure history used for later lockouts. Replay results would be unreliable.
- **Correction:** for sessions over `failed_trial_max_seconds`, require settled samples, comparable load, and a drop that reaches the end of the session.
- **Note:** `:793` and `:795` are the same condition split by duration, and `_FailedTrial` and `failed_trial_duration_seconds` still label long sessions as trials.

## 6. N-04: Demand versus PV: FIXED

**Invariant:** demand above PV is context, never a sufficient blocker, and never a calculated battery power.

`DEMAND_EXCEEDS_PV` was added to the allowlist at `:962`. Nothing in the evaluator computes battery power from PV minus output.

| Input | Decision | Reasons (excluding `SHADOW_ONLY`) |
|---|---|---|
| **1000 W / 700 W, all prerequisites met** | **CONSIDER** | `PRODUCTIVE_SOLAR`, `DEMAND_EXCEEDS_PV` |
| + voltage 25.4 V | BLOCK | adds `BATTERY_GUARD` |
| + telemetry 200 s old | BLOCK | adds `STALE_TELEMETRY` |
| + forecast missing | BLOCK | adds `FORECAST_UNAVAILABLE` |
| + discharge unknown | BLOCK | adds `UNKNOWN_TELEMETRY` |
| + `max=50`, 60 A | BLOCK | adds `HIGH_BATTERY_DISCHARGE`, `INVALID_MEASUREMENT` (N-01-B) |
| + thresholds unknown | BLOCK | adds `UNKNOWN_OUTPUT_CAPABILITY` |
| + invalid baseline | BLOCK | adds `INVALID_BASELINE` |
| + short dwell | BLOCK | adds `MAINS_DWELL_UNPROVEN` |
| + lockout active | BLOCK, Lk 10:05 | adds `FAST_VOLTAGE_SAG`, `COOLDOWN` |
| + churn active | BLOCK, Lk 10:16 | adds `CHURN_LOCKOUT`, `COOLDOWN` (this fixture also changes PV) |
| + 3600 s history | BLOCK | adds `INSUFFICIENT_HISTORY` |
| 5000 W / 100 W, 0 A or discharge unknown | BLOCK | `PV_DEFICIT`, `HIGH_MEASURED_LOAD`, `DEMAND_EXCEEDS_PV` |
| 5000 W / 6000 W | BLOCK | `HIGH_MEASURED_LOAD` |
| 1400 W / 510 W, discharge unknown | BLOCK | `UNKNOWN_TELEMETRY` |

**For the architect, not counted as a defect:** 1499 W with 500 W PV returns CONSIDER, because the load is 1 W under `high_measured_output_w` and PV is exactly productive. Line Mode provides no evidence of battery support at that load. That matches the rules as written.

## 7. N-05: Productive PV threshold: PARTIALLY_FIXED

**Invariant:** a measured value below `productive_pv_w` never qualifies as productive.

| Measured PV (`productive_pv_w = 500`) | Classification | Decision | Result |
|---|---|---|---|
| 280 to 450 W, improving | WEAK | BLOCK, `PV_DEFICIT` | PASS |
| 300 to 460 W, improving | WEAK | BLOCK, `PV_DEFICIT` | PASS |
| 300 to 499 W, improving | WEAK | BLOCK, `PV_DEFICIT` | PASS |
| 300 to 500 W, improving | WEAK | BLOCK, `PV_DEFICIT` | Conservative; median 400 W |
| 550 W, stable | STABLE | CONSIDER | PASS |
| 700 to 550 W, declining | DECLINING | BLOCK | PASS |
| 06:40, 300 to 460 W | WEAK | BLOCK | PASS; no morning exception |
| 500 W with lockout | STABLE | BLOCK, Lk 10:05 | PASS; 500 W doesn't imply eligibility |

### Defect N-05-A (MAJOR): gate uses the window median, not the measurement

| 15-minute window, then snapshot | Classification | Decision | Reason |
|---|---|---|---|
| **300, 800 x6, then 460 W** | **IMPROVING** | **CONSIDER** | **`PRODUCTIVE_SOLAR`** |
| **550 x7, then 499 W** | **STABLE** | **CONSIDER** | **`PRODUCTIVE_SOLAR`** |

- **Location:** `:635`, `:639`. `:620` compares the current value only with `weak_pv_w`.
- **Safety impact:** a falling, cloud-affected reading below the threshold qualifies because of older samples. The trend also uses only the two endpoints, so the first case counts as "improving" even though PV just fell from 800 to 460 W.
- **Correction:** also require `snapshot.pv_power_w >= productive_pv_w`, and don't derive IMPROVING from endpoints alone.

## 8. N-06: Unknown modes: PARTIALLY_FIXED

**Invariant:** confirmed restrictions survive unknown data, nothing is fabricated, and uncertainty is not evidence of stability.

**Confirmed churn: PASS.** Direct transitions 09:00-09:06, then 20 minutes of `Fault`, then Line: BLOCK with Lk 10:06 at 09:20 (`Fault` snapshot), 09:40 and 10:05. At 10:06 it is correctly released from churn and held by dwell.

**No fabrication: PASS.** `Line, F, F, Line, F, F, Line` counts 0 changes. No lockout time is invented across unknowns.

**Normalisation: PASS.** `LINE MODE` and `  line   mode ` match. `Line`, `Battery Mode`, an empty string and `None` are unknown and block.

**Sample gaps: PASS.** An 8-minute gap, with or without a mode change, blocks on `INSUFFICIENT_HISTORY`.

### Defect N-06-A (MAJOR): uncertain history treated as stable

`Line, F, F, Invert, F, F, Line, F, F, Invert, F, F, Line` from 09:00 to 09:24, then continuous Line.

| Evaluated at | Decision | Changes | Lk | Expected |
|---|---|---|---|---|
| 10:09 | **CONSIDER** | 0 | None | Conservative, no precise lockout |
| 10:12 | **CONSIDER** | 0 | None | Conservative, no precise lockout |
| 10:23 | **CONSIDER** | 0 | None | Conservative, no precise lockout |

| Other input | Decision |
|---|---|
| Same pattern, three `Fault` samples at 3-minute cadence, 10:12 | **CONSIDER** |
| 6 h of `Fault`, then 46 minutes of Line | **CONSIDER** |
| One `Fault` between each known mode, 09:50 | BLOCK, Lk 10:16, 4 changes |

- **Location:** `:696-710`. This code wasn't changed. Known samples more than 300 s apart reset `previous_mode`, and dwell (`:910-913`) sees only the latest Line segment.
- **Safety impact:** alternating Line and Invert observations an hour earlier give the same result as stable operation. One unknown sample counts as confirmed transitions, while two consecutive unknown samples count as none.
- **Correction:** keep the count honest and add an explicit uncertainty blocker when known modes differ across an unknown span inside the anti-flap window. No new expiry is needed.

## 9. F-13: Test integrity: PARTIALLY_FIXED

**Improved:** 7 tests were added (34 total), `sample()` no longer defaults to 2 A, and a positive path exists. The 5 h fixture change at `:75` is legitimate, since N-02 requires it.

### New regression tests

| Test | Assessment |
|---|---|
| `test_n01_...` (`:236-248`) | **Tailored.** Sets `significant=0.0`, so even 0 A counts as invalid. Currents are mixed in one trial rather than one collapse at each level. History has an 11-minute gap, so all 4 evaluations have `INSUFFICIENT_HISTORY`. `assertIsNone(config.max_battery_discharge_a)` only checks a `None` it passed in. No exact expiry |
| `test_n02_...` (`:250-251`) | **Weak.** Zero evaluations; checks only that a private function returns at least 3 h |
| `test_n03_...` (`:253-266`) | **Partial.** Exercises the branch but has no decision or expiry assertion, and the healthy session is perfectly flat, which hides N-03-A |
| `test_n04_...` (`:268-271`) | **Good.** Isolates the advisory case; no violation-by-violation coverage |
| `test_n05_...` (`:273-281`) | **Partial.** One literal ramp; misses N-05-A |
| `test_n06_...` (`:283-293`) | **Passes because of an unrelated blocker.** See below |
| `test_shadow_only_contract_is_enforced` (`:295-298`) | **Weak.** Reads default field values; never attempts a violation |

**`test_n06_...` in detail:** history stops at 09:58 and starts only 3 h back, so all 3 evaluations have `INSUFFICIENT_HISTORY`, and evaluating at 10:12 leaves a 14-minute gap. It asserts `lockout_expires_at` is `None`. With continuous history the same sequence returns CONSIDER at 10:12 (section 8), so the test hides N-06-A.

### Existing tests

| Test | Problem |
|---|---|
| `test_demand_exceeding_pv_is_a_separate_conservative_warning` (`:99-102`) | Passes only because the fixture makes PV DECLINING (800 to 600 W). With 600 W history the same input returns CONSIDER. The name now contradicts the architect's ruling |
| `test_original_five_minute_trial_...` (`:149-156`) | Still invalid: 4 of 5 evaluations have history at or after the snapshot and are non-FRESH, so BLOCK is guaranteed. Reported in the last two reviews |
| `test_failed_trial_lockout_persists_until_expiry` (`:124-139`) | 3 of 5 evaluations are non-FRESH with `INSUFFICIENT_HISTORY`. The expiry case asserts only that `COOLDOWN` is absent |
| `test_invalid_current_and_power_values_never_consider` (`:384-390`) | Original safety cases were removed rather than corrected (N-01-A) |
| `test_invert_exit_voltage_...`, `test_rebound_...` (`:308`, `:352`) | Decision is guaranteed by 60-minute history, though specific reasons are asserted |
| `sample()` (`:42`) | Still invents a measurement: 0 A at any load, including Invert at 2000 W |

### Mutation check (in memory, 16 mutations: 8 killed, 8 survived)

| Mutation | Tests |
|---|---|
| Sag excludes current at or above 50 A | Killed by `test_n01_...` |
| Required history back to 1 h | Killed by `test_n02_...` |
| Long-session branch removed | Killed by `test_n03_...` |
| Demand back to blocker | Killed by `test_n04_...` |
| Improving gate removed | Killed by `test_n05_...` |
| Unknown samples always reset mode tracking | Killed by `test_unknown_modes_...` |
| Churn lockout disabled | Killed by 2 tests |
| Failed-trial lockout 1 minute short | Killed by 3 or more tests |
| **Restore the 50 A default** | **Survived** |
| **Required history 3 h** | **Survived** |
| **Escalation multiplier removed** | **Survived** |
| **Shadow-only constructor check removed** | **Survived** |
| **HOLD never returned** | **Survived** |
| **Enter-voltage guard ignored in Line** | **Survived** |
| **Fresh forecast not required** | **Survived** |
| **Mains dwell not required** | **Survived** |

The survivors show that 34 of 34 passing doesn't protect those behaviours: HOLD has no test at all, and the enter guard, forecast and dwell gates can be removed without a failure.

| Checklist item | Result |
|---|---|
| History doesn't pass the snapshot | FAIL (`test_original_...`) |
| Measurements aren't invented | FAIL (`sample()` 0 A) |
| Positive scenarios have sufficient history | PASS |
| Line and Invert sessions don't overlap | PASS, as the sort removes same-time cases; no overlapping sessions found |
| Current explicit where relevant | PARTIAL |
| PV trends reflect intent | PARTIAL (`:99-102`) |
| Short and long trials check evidence and expiry | FAIL for long sessions; PARTIAL for short |
| Negatives don't pass via unrelated blockers | FAIL (`test_n06_...`, `:99`, `:149`) |
| Positives don't disable protections | PASS |
| Determinism | THIN; one equality at `:232` |
| No-I/O | THIN; `:233-234` patches two functions |
| Shadow-only | THIN; no violation attempted |

## 10. New findings and cross-cutting safety

### NF-01 (MAJOR): collapse with falling load produces no sag

Invert at 2000 W, 26.0 V, then 09:50 25.5 V at 1500 W, 09:52 25.0 V at 1000 W, 09:54 24.6 V at 500 W. Line afterwards.

| Evaluated at | Decision | Lk |
|---|---|---|
| 09:55 | BLOCK, rebound only | None |
| 10:40 | **CONSIDER** | None |

- **Location:** `:745-746`, `:759-760`, `:822-823`. The load comparison uses an absolute difference.
- **Physical issue:** a voltage drop while load rises may be explained by load. A drop while load falls can't be, so this is stronger evidence, yet it is discarded. 24.6 V is also just above the exit guard, so nothing else trips.
- **Correction:** exclude a pair only when load rose by more than the tolerance. The comparable-load rule itself should stay.

### NF-02 (MAJOR, correctness): an old recovered dip blocks Invert for the rest of the session

Invert now flat at 26.0 V, dip to 25.6 V 1, 2 or 3 h earlier: all return BLOCK, `FAST_VOLTAGE_SAG`, INELIGIBLE. The same session with no dip returns HOLD.

- **Location:** `:752-765`. `_segment_sag` searches the whole segment, although `test_voltage_sag_uses_recent_comparable_window_and_settling` implies a recent window.
- **Impact:** errs towards blocking, but it reports a current sag that isn't there.

### NF-03 (open risk before Phase 2): replay cadence

`.project-memory/ADAPTIVE_OUTPUT_CONTROL_IMPLEMENTATION_PLAN.md:76-79` says the Timescale collector normally stores data every 1800 s in grid mode. The evaluator needs 5 h of history with no gap over 300 s (`:672`). Grid-mode replay from that data will therefore always return `INSUFFICIENT_HISTORY`, unless the history comes from a denser source. The plan's 60 s live poll would satisfy the rule, but no such history source is wired up yet.

### Cross-cutting invariants

| Invariant | Result | Evidence |
|---|---|---|
| Pure, deterministic | PASS | 6,000 of 6,000 identical on shuffled re-evaluation (BLOCK 4,263, CONSIDER 1,695, HOLD 42) |
| `evaluated_at` mandatory | PASS | Missing raises `TypeError`; naive raises `ValueError` |
| Timezone comparisons | PASS | Mixed New York and Tokyo inputs match the UTC result |
| Immutable models | PASS | Baseline and decision raise `FrozenInstanceError` |
| No fabricated hardware thresholds | **PARTIAL** | Current limits fixed. Voltage and load defaults remain, labelled generic; `:530`'s 100 V cap and `:535`'s 2.5x factor are unapproved constants |
| No SOC eligibility | PASS | SOC 1 and 75 give equal results |
| No hardware, wiring, config or DB writes | PASS | Imports are `__future__`, `dataclasses`, `datetime`, `enum`, `math`; only the test imports this module in `app` and `tests` |
| No hidden state | PASS | Only `__all__` at module level; no `global` or `nonlocal` |
| No stale-data optimism | PASS | Stale and future data block, including at 6000 W PV |
| No bypass through strong PV | PASS | Guard, stale and lockout all block at 6000 W |
| Every BLOCK has a blocker reason | PASS | 0 of 4,263 fuzz BLOCKs without one (`DEMAND_EXCEEDS_PV` not counted) |
| CONSIDER reachable | PASS | 800 W stable: CONSIDER, `shadow_only=True`, `command_executed=False` |
| No lowering of a protection boundary | PASS | `effective_*` are `None`; `_battery_guard_breached` reads only baseline values |
| Unknown limit isn't proof of safety | **FAIL** | N-01-A |
| Restrictions rebuild from sufficient history | **FAIL** | N-02-A |
| No-I/O at runtime | PASS | CONSIDER, BLOCK and HOLD paths ran with `open`, `socket.socket`, `subprocess.Popen` and `time.time` raising |
| Shadow-only | PASS | Violations raise `ValueError` at construction |

## 11. Required corrections

| ID | Severity | Location | Correction |
|---|---|---|---|
| C1 | **BLOCKER** | `:542-553` | With limits `None`, an unexplained or implausible discharge can't reach CONSIDER or a healthy HOLD. Emit `INCONSISTENT_BATTERY_CURRENT` or another specific reason. The architect must define the test without an invented hardware limit |
| C2 | **BLOCKER** | `:660-663` | Add the escalation lookback to required history; test CONSIDER is impossible at the exact horizon |
| C3 | MAJOR | `:542-545` | Report configured-limit violations as `HIGH_BATTERY_DISCHARGE`, not `INVALID_MEASUREMENT` |
| C4 | MAJOR | `:793-796`, `:805-830` | Long sessions: settled samples, comparable load, drop reaches session end |
| C5 | MAJOR | `:635`, `:639` | Require measured PV at or above the productive threshold |
| C6 | MAJOR | `:696-710` | Conservative blocker for differing known modes across recent unknown data; no fabricated count or expiry |
| C7 | MAJOR | `:745-746`, `:759-760`, `:822-823` | Don't discard a voltage drop because load fell |
| C8 | MAJOR | `:752-765` | Limit current sag to a recent window, or report it as past evidence |
| C9 | MAJOR | Tests | Fix each test in section 9; restore the removed current cases; add HOLD, enter guard, forecast, dwell, escalation, exact-horizon and constructor-rejection tests |
| C10 | MINOR | `:518-520`, `:565-566`, `:672` | Evaluate gaps and invalid samples only within the required horizon |

## 12. Artifact verification

| Check | Result |
|---|---|
| Implementation and test hashes unchanged after probes | Yes, section 1 values |
| `ADAPTIVE_OUTPUT_PHASE1A_REVIEW.md` | `a86f25ae5cf4fde84dc3cbc51d0e5fd116b4847781d42dc9ab262b78bf128d29` |
| `ADAPTIVE_OUTPUT_PHASE1A_REREVIEW.md` | `ae7d033a3b4176a2bcf9c63664dc8d52cec07e089bffdaa10c035cee562b7b63` |
| `ADAPTIVE_OUTPUT_PHASE1A_FINAL_REVIEW.md` | `880b867bc290e779d8f3407967f1afca4a24fbf31c7f8325a8a1dea8dcb04020` |
| `ADAPTIVE_OUTPUT_PHASE1A_SAFETY_ACCEPTANCE.md` | `0f61ca5017d825492821821d417a2ad0d64fa0b84fc165935abf46f6a52f60f4` |

Git status: `?? .project-memory/reviews/`, `?? app/control/adaptive_output_policy.py`, `?? tests/`. No staging, commit or push.

No DESS, Tuya, cloud, production database, network or hardware action was taken.

## 13. Recommendation

Do not approve Phase 1A, and do not start Phase 2 replay or wiring until C1 and C2 are fixed. There is no hardware risk today, because the module is untracked, unwired and shadow-only. N-04 needs no further code change, though its old test at `:99-102` still does.

The next review should repeat the varied scenarios in this report and re-run the mutation check, rather than only the listed values.
