# Adaptive Output Control, Phase 1A: Targeted Safety Acceptance Review

## 1. Review details

| Item | Value |
|---|---|
| Review date | 2026-11-02, 18:51 GMT |
| Branch | `feat/adaptive-output-control` |
| HEAD | `b19cd7a1c5cd71d8c56676f5ed2aeba286c13791` |
| Local `origin/feat/adaptive-output-control` | `1aacb3d`; contains no copy of the policy module (no fetch performed) |
| Python | 3.14.7 (system `python3`) |

**Reviewed files, read from the working tree:**

| File | Lines | Bytes | Modified | SHA-256 |
|---|---|---|---|---|
| `app/control/adaptive_output_policy.py` (untracked) | 978 | 47256 | 14:37:30 | `7d9e89acafb6c5f0cd81f70b4ac9607919a30d9e9a4c7642e4a5475d28cf4dfb` |
| `tests/test_adaptive_output_policy.py` (untracked) | 361 | 20795 | 14:38:04 | `45a0d4b1c63d62f50f4d817c9f3b6a6951ae89b34eaab21243fdd46bec10eb9d` |

Also consulted: `.project-memory/reviews/ADAPTIVE_OUTPUT_PHASE1A_FINAL_REVIEW.md` (modified 14:50, scenario definitions). The approved design was not needed, because no requirement here turned on an ambiguity.

## 2. Overall verdict

**CHANGES_REQUIRED.** All seven findings are **NOT_FIXED**.

**The reviewed code predates the findings.** Both files were last modified at 14:37-14:38, before the final review was written at 14:50. The line counts match those in that review (978 and 361), the defective lines are still at the cited locations, and the test count is still 27. No newer copy exists locally: there is no stash, one worktree, no later commit, and no other file with these names outside `.git` and `.venv`. The earlier review recorded no hash, so this rests on timestamps, line counts and code content rather than a hash comparison.

Whatever the Coder changed did not reach this working tree. This review cannot say whether changes exist elsewhere, because no network fetch was allowed.

## 3. Finding matrix

| Finding | Severity | Status | Result |
|---|---|---|---|
| N-01 Unverified current limits | BLOCKER | **NOT_FIXED** | Sag detection and lockout disappear at 50 A; limits default to 100/50 A; `None` raises `TypeError` |
| N-02 Lockout history coverage | BLOCKER | **NOT_FIXED** | Truncated 3600 s history returns CONSIDER at 09:36 inside the 10:35 lockout |
| N-03 Collapse after short-trial window | MAJOR | **NOT_FIXED** | 12-minute collapse gets no restriction; CONSIDER by 10:59 |
| N-04 Demand exceeds PV | MAJOR | **NOT_FIXED** | 1000 W/700 W is BLOCK with `DEMAND_EXCEEDS_PV` as the only adverse reason |
| N-05 Productive PV threshold | MAJOR | **NOT_FIXED** | 460 W improving reports PRODUCTIVE_SOLAR and CONSIDER |
| N-06 Unknown working modes | MAJOR | **NOT_FIXED** | CONSIDER at 10:12 and 10:23; lockout to 10:24 is missing |
| F-13 Regression test integrity | MAJOR | **NOT_FIXED** | No regression test exists for N-01 to N-06, and two tests still enforce defects |

## 4. Code references

| Finding | References | Cause |
|---|---|---|
| N-01 | `app/control/adaptive_output_policy.py:190-192`, `:234-242`, `:537-544`, `:729`, `:809`, `:346-349` | Non-optional 100/100/50 A defaults; `None` fails the `< 0` check at `:241`; at or above 50 A a reading counts as a measurement issue and is excluded from both sag functions |
| N-02 | `:658-659`, `:662-668`, `:842-870` | Required history is the largest of 3600, 2700, 3600 and 900 s, so it ignores the 3 h escalated lockout, escalation lookback, and churn lockout |
| N-03 | `:787-796` | Failed-trial detection only runs when duration is at most `failed_trial_max_seconds` (600 s); no other path restricts after a terminal collapse |
| N-04 | `:377-378`, `:947` | Reason is appended, and every reason outside a three-item allowlist blocks CONSIDER |
| N-05 | `:632-633`, `:640`, `:368-369`, `:948` | IMPROVING has no productive-threshold check; the `else` branch labels it PRODUCTIVE_SOLAR |
| N-06 | `:696-705` | Unknown samples are skipped without updating `previous_at`, so two consecutive ones exceed the 300 s continuity limit and reset `previous_mode` |
| F-13 | `tests/test_adaptive_output_policy.py:42`, `:99-102`, `:149-155`, `:204-208`, `:261-275` | See section 6 |

## 5. Reproduced inputs and actual outputs

**Common inputs:** UTC, 2 November 2026, sunrise 06:00, sunset 20:00. Baseline is exit 24.5 V, enter 25.5 V, hysteresis 0.5 V, forecast fresh, 2-minute cadence, 800 W PV, 26.0 V Line samples at 0 A unless stated. All other limits use defaults. "Truncated" means only samples within 3600 s of the snapshot were supplied.

### N-01

Defaults: `max_battery_discharge_a=100.0`, `max_battery_charge_a=100.0`, `significant_battery_discharge_a=50.0`. Passing `None` for either discharge field raises `TypeError: '<' not supported between instances of 'NoneType' and 'int'`, so the required default of unknown (`None`) is impossible.

Same collapse in all rows: Invert at 2000 W, 10:01 25.5 V, 10:02 25.3 V, 10:03 25.0 V, 10:04 24.7 V, 10:05 24.4 V, then Line at 25.8 V. Only current differs.

| Current | Invert 10:05 | Line 10:06 | Line 10:54 | Line 11:05 | Line 11:07 |
|---|---|---|---|---|---|
| 49 A | BLOCK, `FAST_VOLTAGE_SAG` | BLOCK, sag, lockout 11:05 | BLOCK, sag, lockout 11:05 | CONSIDER (lockout expired) | CONSIDER |
| 50 A | BLOCK, **no sag**, `INVALID_MEASUREMENT` | BLOCK, **no sag, no lockout** | same | BLOCK, `INVALID_MEASUREMENT` | same |
| 85 A | as 50 A | as 50 A | as 50 A | as 50 A | as 50 A |
| 100 A | as 50 A | as 50 A | as 50 A | as 50 A | as 50 A |

- The 50, 85 and 100 A rows stay BLOCK only because the sample is called invalid. The decision is safe by accident, the reason is wrong, and no `lockout_expires_at` is produced.
- The invalid block is also unbounded: at 11:07 it still applies for as long as the sample is in the supplied history.
- Current is not kept as usable evidence: `battery_eligibility` becomes UNKNOWN.
- Healthy 90-minute Invert at 1500 W and flat 26.0 V: 49 A returns HOLD; 50 A and 60 A return BLOCK with `INVALID_MEASUREMENT`.
- 0 W output with 40 A discharge returns CONSIDER; the conflict check also starts at 50 A.
- Two failed trials at 85 A, truncated at 09:36: **CONSIDER**, lockout `None`.

### N-02

Reported requirement: `_required_history_seconds = 3600`. Failed trials end at 08:05 and 08:35, with an effective expiry of 10:35.

| Evaluated at | Complete history | Truncated 3600 s |
|---|---|---|
| 09:36 | BLOCK, lockout 10:35, `FAST_VOLTAGE_SAG`, `COOLDOWN` | **CONSIDER**, lockout `None` |
| 10:00 | BLOCK, lockout 10:35 | **CONSIDER**, lockout `None` |
| 10:34 | BLOCK, lockout 10:35 | **CONSIDER**, lockout `None` |
| 10:35 | CONSIDER (expired correctly) | BLOCK, `INSUFFICIENT_HISTORY` (sampling artefact) |

Results at 0 A and 40 A are identical. Anti-flap with changes at 09:04, 09:08, 09:12 and 09:16, expiry 10:16:

| Evaluated at | Complete history | Truncated 3600 s |
|---|---|---|
| 10:03 | BLOCK, lockout 10:16 | BLOCK, lockout `None`, `INSUFFICIENT_HISTORY` |
| 10:05 | **BLOCK, lockout 10:16, `CHURN_LOCKOUT`** | BLOCK, lockout `None`, `INSUFFICIENT_HISTORY` |
| 10:10 | BLOCK, lockout 10:16 | **CONSIDER**, lockout `None` |
| 10:15 | BLOCK, lockout 10:16 | BLOCK, lockout `None`, `INSUFFICIENT_HISTORY` |
| 10:16 | CONSIDER | CONSIDER |

The truncated BLOCK rows do not show the lockout working. They occur only because the 2-minute grid leaves the first sample just inside the cut-off, and no lockout is reported.

### N-03

Invert 10:00-10:12 at 1000 W: 26.0 V until 10:06, 25.9 at 10:09, 25.4 at 10:10, 24.9 at 10:11, 24.4 at 10:12. Then Line at 25.8 V with complete history.

| Evaluated at | Decision | Lockout | Reasons (excluding `SHADOW_ONLY`) |
|---|---|---|---|
| 10:13 | BLOCK | None | `PRODUCTIVE_SOLAR`, `BATTERY_REBOUND_NOT_RECHARGE` |
| 10:30 | BLOCK | None | `PRODUCTIVE_SOLAR`, `MAINS_DWELL_UNPROVEN`, `UNKNOWN_BATTERY_RISK` |
| 10:59 | **CONSIDER** | None | `PRODUCTIVE_SOLAR` |
| 11:12 | **CONSIDER** | None | `PRODUCTIVE_SOLAR` |

Two-hour session with terminal collapse: BLOCK at 09:00, then **CONSIDER at 09:51** with no lockout.

Controls: healthy 12-minute and healthy 3-hour sessions both return CONSIDER afterwards, so healthy long sessions are not mislabelled. This is not evidence of a fix, because collapsed sessions get the same result.

### N-04

| Input | Decision | Reasons (excluding `SHADOW_ONLY`) | Expected | Result |
|---|---|---|---|---|
| 1000 W / 700 W, all prerequisites met, battery ELIGIBLE | **BLOCK** | `PRODUCTIVE_SOLAR`, `DEMAND_EXCEEDS_PV` | Advisory, not independently blocking | **FAIL** |
| 950 W / 700 W | CONSIDER | `PRODUCTIVE_SOLAR` | Boundary | Boundary confirmed |
| 951 W / 700 W | **BLOCK** | `PRODUCTIVE_SOLAR`, `DEMAND_EXCEEDS_PV` | Advisory | **FAIL** |
| 5000 W / 100 W, 0 A reported | BLOCK | `PV_DEFICIT`, `HIGH_MEASURED_LOAD`, `DEMAND_EXCEEDS_PV` | Blocked | PASS |
| 5000 W / 100 W, discharge unknown | BLOCK | as above, plus `UNKNOWN_TELEMETRY` | Blocked | PASS |

### N-05

`productive_pv_w = 500`; PV rises over the last 15 minutes.

| Input | Classification | Decision | Reason | Result |
|---|---|---|---|---|
| 300 to 460 W, midday | IMPROVING | **CONSIDER** | `PRODUCTIVE_SOLAR` | **FAIL** |
| 300 to 499 W, midday | IMPROVING | **CONSIDER** | `PRODUCTIVE_SOLAR` | **FAIL** |
| 300 to 460 W, 06:40 | EARLY_MORNING | **CONSIDER** | `SUNRISE_OPPORTUNITY` | **FAIL**, unless the operator approves a morning exception |
| 500 to 700 W, midday | IMPROVING | CONSIDER | `PRODUCTIVE_SOLAR` | Correct |
| 460 W stable (control) | WEAK | BLOCK | `PV_DEFICIT` | Correct |

### N-06

Previous scenario: 2-minute samples `Line, Fault, Fault, Invert, Fault, Fault, Line, Fault, Fault, Invert, Fault, Fault, Line` from 09:00 to 09:24, then Line. Expected lockout until 10:24.

| Evaluated at | Decision | Mode changes | Lockout | Result |
|---|---|---|---|---|
| 09:26 | BLOCK (`MAINS_DWELL_UNPROVEN`) | 0 | None | Wrong reason |
| 10:12 | **CONSIDER** | 0 | None | **FAIL** |
| 10:23 | **CONSIDER** | 0 | None | **FAIL** |
| 10:24 | CONSIDER | 0 | None | Correct only because expired |

These parts already behave correctly:

- One unknown sample between known modes keeps churn evidence: BLOCK, lockout 10:16, 4 changes.
- `Line, Fault, Fault, Line, Fault, Fault, Line` adds no transitions.
- A failed-trial lockout survives later Fault samples: BLOCK, lockout 10:05, both with a later Line snapshot and with a Fault snapshot.

## 6. Regression tests

**Run:** `python3 -m unittest discover -s tests -p 'test_*.py'`: 27 run, 0 failures, 0 errors. `git diff --check` is clean. The pass count says nothing about the seven findings, because each one was reproduced on this same code.

**Red/green evidence:** none is available. No test targets these findings, so there is no pre-fix failure to reproduce. The failures in section 5 come from my probes, not repository tests.

| Finding | Nearest existing test | Assessment | Missing |
|---|---|---|---|
| N-01 | `test_significant_discharge_is_not_ignored` (`:204`), `test_invalid_current_and_power_values_never_consider` (`:320`) | **Enforce the defect**: 99 A and 90 A are expected to BLOCK, with no collapse scenario | 49/50/85/100 A sag parity; `None` default |
| N-02 | `test_missing_history_and_gaps_are_unknown_not_eligible` (`:326`) | Covers gaps only | Two-failure escalation with truncation; required-history value |
| N-03 | None | Absent | 12-minute and long-session terminal collapse |
| N-04 | `test_demand_exceeding_pv_is_a_separate_conservative_warning` (`:99`) | **Enforces the defect**: asserts BLOCK at 1000 W/600 W | Advisory-only CONSIDER case; retained 5000 W block |
| N-05 | `test_stable_pv_requires_productive_boundary` (`:163`) | Covers stable PV only | Improving below threshold |
| N-06 | `test_unknown_modes_do_not_hide_known_flapping` (`:261`) | Covers single unknown samples and asserts no decision or expiry | Consecutive unknown samples; 10:12 and 10:24 expiry |

| F-13 check | Result | Evidence |
|---|---|---|
| Histories exclude samples after the snapshot | **FAIL** | `test_original_five_minute_trial_is_detected_through_settling_window` (`:141`): 4 of 5 evaluations receive history at or after the snapshot, and those 4 are non-FRESH, so its BLOCK assertion passes regardless (`:149-156`) |
| Helper doesn't assign 2 A to every scenario | **FAIL** | `sample()` defaults to `discharge=2.0` (`:42`). Runtime instrumentation shows only 2 of 27 tests use any other value, and neither is a load scenario |
| Specific reasons and expiry behaviour | **PARTIAL** | Reasons are often asserted. Exact expiries appear only at `:214` and `:223`. `:136` and `:259` assert only "not None"; `:139` and `:161` only that `COOLDOWN` is absent |
| Test data quality | **PARTIAL** | `test_failed_trial_lockout_persists_until_expiry` (`:124`): 3 of 5 evaluations are non-FRESH because the history has gaps |
| Legitimate CONSIDER path | **PASS** | `test_productive_day_requires_history_and_then_is_considered` (`:104`) |
| Determinism test | **THIN** | One equality assertion at `:232` |
| No-I/O test | **THIN** | `:233-234` patches `builtins.open` and `socket.socket` only |
| Shadow-only enforcement test | **FAIL** | No test mentions `shadow_only` or `command_executed`. The code enforces it (section 7), but nothing tests it |

## 7. Determinism and side-effect safety

| Check | Result | Evidence |
|---|---|---|
| Determinism | **PASS** | 5,000 cases: 5,000 identical on re-evaluation with shuffled history (BLOCK 3,250, CONSIDER 1,739, HOLD 11) |
| Determinism, high-current mix | **PASS, limited** | 3,000 of 3,000 identical, but all BLOCK, so this run is weak on its own |
| Imports | **PASS** | AST: only `__future__`, `dataclasses`, `datetime`, `enum`, `math` |
| Clock and I/O calls | **PASS** | AST: no `now`, `utcnow`, `today`, `time`, `open`, `print`, `eval` or `exec` calls |
| No I/O at runtime | **PASS** | CONSIDER path ran with `open`, `socket.socket`, `subprocess.Popen` and `time.time` patched to raise |
| Shadow-only | **PASS** | `shadow_only=False` and `command_executed=True` both raise `ValueError` at construction |
| Positive CONSIDER | **PASS** | 800 W stable, 500 W load, full history: CONSIDER, STABLE, ELIGIBLE, `shadow_only=True`, `command_executed=False` |

No DESS, Tuya, cloud, production database, hardware or network action was taken. Probes ran from the scratchpad with bytecode kept outside the repository: `/private/tmp/claude-501/-Users-alexred-Projects-Python-dessmonitor/89f11ab5-3de5-4bf6-8c85-4f884d2e0cde/scratchpad/accept.py` and `/private/tmp/claude-501/-Users-alexred-Projects-Python-dessmonitor/89f11ab5-3de5-4bf6-8c85-4f884d2e0cde/scratchpad/det.py`.

## 8. Remaining blocking issues

1. **Fixes are absent from the tree.** The corrected files must be present before any fix can be accepted.
2. **N-01 (BLOCKER):** make the current limits optional with a `None` default, stop treating plausible current as invalid, and keep those samples in sag analysis.
3. **N-02 (BLOCKER):** derive required history from the longest lockout plus escalation and detection windows, and return INSUFFICIENT_HISTORY rather than CONSIDER when it isn't met.
4. **N-03 (MAJOR):** restrict after a verified terminal collapse of any session length, using a classification distinct from a failed short trial.
5. **N-04 (MAJOR):** make `DEMAND_EXCEEDS_PV` non-blocking on its own.
6. **N-05 (MAJOR):** apply `productive_pv_w` to IMPROVING and to the `PRODUCTIVE_SOLAR` reason.
7. **N-06 (MAJOR):** stop consecutive unknown samples discarding known-mode evidence, without adding transitions across them.
8. **F-13 (MAJOR):** replace the two defect-enforcing tests, fix the later-history test, remove the 2 A default from load scenarios, and add the regression tests in section 6 plus a shadow-only test.

No new defect class was found. This review covered the seven findings and did not repeat the full audit.

**Decisions still needed from the operator or architect:**

- Approved current limits, if any. Until then they should be `None`.
- Whether a morning exception below `productive_pv_w` is allowed. The early-morning row in section 5 fails unless it is approved.

## 9. Final recommendation

Do not approve Phase 1A, and do not start Phase 2 replay or wiring. There is no current hardware risk, because the module is untracked, unwired and shadow-only.

The Coder should confirm where the fixes were saved and put them in this working tree, with a named regression test for each finding and a recorded failing run before the fix. Acceptance can then be re-run with `/private/tmp/claude-501/-Users-alexred-Projects-Python-dessmonitor/89f11ab5-3de5-4bf6-8c85-4f884d2e0cde/scratchpad/accept.py`, as long as the scratchpad is still available; otherwise the inputs in section 5 are enough to rebuild it.
