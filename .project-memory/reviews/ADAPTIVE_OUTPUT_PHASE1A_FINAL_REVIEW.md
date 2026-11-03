# Independent Final Review — Adaptive Output Control, Phase 1A

## 1. Review metadata

| | |
|---|---|
| **Date** | 2026-11-02 |
| **Reviewer role** | Independent reviewer; did not implement the code or its corrections |
| **Branch** | `feat/adaptive-output-control` |
| **HEAD** | `b19cd7a1c5cd71d8c56676f5ed2aeba286c13791`, plus untracked working-tree files |

**Read from disk in this review:**

- `app/control/adaptive_output_policy.py` (978 lines, untracked)
- `tests/test_adaptive_output_policy.py` (361 lines, untracked)
- `app/service/overload_protector.py:1-22` (evidence for N-01 only)

**Reference material:** `.project-memory/ADAPTIVE_OUTPUT_CONTROL_DESIGN.md`, `.project-memory/ADAPTIVE_OUTPUT_CONTROL_IMPLEMENTATION_PLAN.md`, `.project-memory/reviews/ADAPTIVE_OUTPUT_PHASE1A_REVIEW.md` and `.project-memory/reviews/ADAPTIVE_OUTPUT_PHASE1A_REREVIEW.md`. These were read or written earlier in this session rather than re-read here. `git diff HEAD` shows both architecture documents unchanged, and the first review's SHA-256 matches its earlier value.

The architect's settled rules in the final-review request are applied as binding.

## 2. Overall verdict: `CHANGES_REQUIRED`

| | Count | Findings |
|---|---:|---|
| Original: FIXED | 11 | F-01, F-03, F-04, F-05, F-08, F-14, F-15, F-17, F-18, F-19, F-20 |
| Original: PARTIALLY_FIXED | 9 | F-02, F-06, F-07, F-09, F-10, F-11, F-12, F-13, F-16 |
| Regression: FIXED | 3 | R-03, R-04, R-05 |
| Regression: PARTIALLY_FIXED | 2 | R-01, R-02 |
| NOT_FIXED / NOT_VERIFIABLE | 0 | |
| New findings | 4 | N-01, N-02 BLOCKER; N-03, N-04 MAJOR |

**Unresolved BLOCKER:** N-01, N-02. **Unresolved MAJOR:** N-03, N-04, N-05 (R-02 remainder), N-06 (F-10 remainder), F-13.

N-05 and N-06 are given IDs for traceability, and are remainders of R-02 and F-10, not new defects. The "new findings" count above therefore excludes them.

## 3. Executive assessment

This is a large improvement. The literal scenarios from the previous reviews now pass with **supplied full history and low discharge current**:

- The 10:01/10:03/10:05 collapse locks out until 11:05, blocks at 10:54, and recovers at 11:05.
- Failures ending 08:05 and 08:35 hold to 10:35.
- Churn holds to 10:16.
- Stable 260 W PV is not productive.
- Line Mode drift doesn't raise sag.
- `CONSIDER` remains reachable, and evaluation remains deterministic and passive.

Approval isn't possible, because lockout and short-trial protection are still unsafe under conditions close to real use:

1. **N-01 (BLOCKER).** Any sample at or above an invented 50 A threshold is treated as an *invalid measurement* and dropped from sag analysis. The same 10:01–10:05 collapse with 85 A of discharge gets **no `FAST_VOLTAGE_SAG`, no lockout and no escalation**. The test scenario pairs 2,000 W with 2 A, which hides this. The repository's own overload protector treats 50 A-range current as a real operating condition.
2. **N-02 (BLOCKER).** The evaluator tells callers it needs 3,600 s of history, but escalated and churn lockouts need more. With 3,600 s of accepted history, **09:36 returns `CONSIDER`**, inside the 10:35 lockout. The final-review request requires that historical cutoff not discard active restrictions.
3. **N-03 to N-06 (MAJOR).**
   - A collapse lasting just over 10 minutes gets no lockout.
   - `DEMAND_EXCEEDS_PV` remains a hard block, contrary to the architect's ruling.
   - Improving sub-productive PV is labelled `PRODUCTIVE_SOLAR`.
   - Unknown modes can hide churn.

## 4. Status matrix (F-01–F-20, R-01–R-05)

Lines refer to `app/control/adaptive_output_policy.py`, and test lines to `tests/test_adaptive_output_policy.py`.

| ID | Status | Location | Evidence | Remaining |
|---|---|---|---|---|
| F-01 | **FIXED** | `:319-324, :502-512, :648` | 400-day data `STALE`/`BLOCK`; future +1 s `UNKNOWN`; age 180 s `FRESH`, 181 s `STALE`; `received_at` after `evaluated_at` now checked (`:509`); naive and missing `evaluated_at` rejected | |
| F-02 | **PARTIALLY_FIXED** | `:781-797, :842-857` | With sufficient history and current below 50 A, blocks at 10:06, 10:07, 10:54, 11:04; `CONSIDER` at 11:05 | N-01, N-02 |
| F-03 | **FIXED** | `:373-376, :947` | 5000 W/600 W `BLOCK`, `HIGH_MEASURED_LOAD`; 2000 W/100 W uses 2000 W evidence; Tuya isn't an input | See N-04 for the separate warning issue |
| F-04 | **FIXED** | `:662-668, :624-625, :881, :898` | 1 s apart, empty, 59-min coverage: `BLOCK` | Requirement length: N-02 |
| F-05 | **FIXED** | `:884, :935-943` | Below exit: `BLOCK`, `EXIT_BATTERY_GUARD`; missing voltage and stale data don't `HOLD` | |
| F-06 | **PARTIALLY_FIXED** | `:720-761, :787-796, :800-825` | Late, growing-load and short collapses detected below 50 A | N-01, N-03 |
| F-07 | **PARTIALLY_FIXED** | `:733-734, :755-756, :777` | Pair load check added: ramp no longer creates sag. One-sample 0.4 V no lockout | Minor: two samples 26.0→25.6 V inside one minute cause a 60-min lockout (`:800-825`), inconsistent with the 0.6 V one-sample rule |
| F-08 | **FIXED** | `:671-689, :692-706` | 15-min and 6-min gaps don't produce dwell | Hidden-churn side effect: N-06 |
| F-09 | **PARTIALLY_FIXED** | `:190-192, :523-554, :557-565` | 99 A, 0 W/90 A, 99 V, invalid history all block; invalid history excluded from sag | N-01. Also 0 W with 40 A returns `CONSIDER` |
| F-10 | **PARTIALLY_FIXED** | `:842-870` | Lockouts no longer shorten: 10:35 and 10:16 hold; 60/120/180-min escalation capped at 180; expiry at correct UTC instant | N-02, N-06 |
| F-11 | **PARTIALLY_FIXED** | `:165, :645-655` | 180-min default; 180 `FRESH`, 181 `STALE`; past, empty and future-issued rejected; default tested at `:225-231` | Minor: `valid_at` a year ahead is still `FRESH` |
| F-12 | **PARTIALLY_FIXED** | `:15-20, :364-371, :910` | 20,000-case fuzz: no `BLOCK` without a specific reason; `FAST_VOLTAGE_SAG` now tied to failure lockout; decisions consistent with architect semantics; `HOLD` hides nothing tested | Minor: `HOLD`/`BLOCK` meanings still not stated in code; bad sun times also report `INSUFFICIENT_HISTORY`; real currents report `INVALID_MEASUREMENT` (N-01) |
| F-13 | **PARTIALLY_FIXED** | test file | 27 tests pass; many are real regressions | **MAJOR**, see section 5 |
| F-14 | **FIXED** | `:112-113, :152-153, :458-459` | DST fold gap correct; expiry 01:30 UTC from 00:30 UTC failure | No test |
| F-15 | **FIXED** | `:568-577, :608` | Wrong-day and over-24 h intervals give `UNKNOWN` | No test |
| F-16 | **PARTIALLY_FIXED** | `:208-249` | Negatives, NaN and non-int counts rejected | Minor: `productive_pv_w < weak_pv_w`, `failed_trial_lockout_seconds=0` and `voltage_sag_min_drop_v=0` accepted. Zero lockout disables protection |
| F-17 | **FIXED** | `:42, :634-635, :640` | `EARLY_MORNING`, `DECLINING` added; 830→700 W at 16:00 `BLOCK`, `DECLINING_SOLAR` | No test (`DECLINING` absent from tests). A 95 W decline remains `STABLE` by parameter design |
| F-18 | **FIXED** | `:482-484, :500` | Snapshot-timestamp duplicate `UNKNOWN`, `BLOCK` | Minor: conflicting duplicates within history aren't flagged; they showed `chg=2`, then blocked by dwell. Passing the snapshot in history always gives `UNKNOWN`; caller contract is undocumented |
| F-19 | **FIXED** | `:880` | Only populated from failed events | Minor: retains long-expired failures |
| F-20 | **FIXED** | `:298-303` | `command_executed=True` raises `ValueError`, including via `dataclasses.replace` | No test. Plan's `output_priority` input and bounded `effective_*` remain absent; treated as future scope |
| R-01 | **PARTIALLY_FIXED** | `:787-791, :800-825, :729, :809` | All tested cadences detected below 50 A: no-fabrication check holds, one-sample minor drop isn't failed | N-01 |
| R-02 | **PARTIALLY_FIXED** | `:637` (used), `:632-633, :369` | Stable 260 and 499 W: `WEAK`, `PV_DEFICIT`; 500 W: `STABLE`; forecast can't override | N-05 |
| R-03 | **FIXED** | `:877, :910` | Line 26.0→25.6 V: no `FAST_VOLTAGE_SAG`, `CONSIDER`; rebound still `BATTERY_REBOUND_NOT_RECHARGE`, not recharge | |
| R-04 | **FIXED** | `:568-577` | London, Los Angeles, Tokyo, Sydney, New York and Auckland all `CONSIDER`; autumn and spring DST-day remaining hours correct | No test |
| R-05 | **FIXED** | `:788` | Long healthy and early-sag sessions don't become failed trials | Consequence: N-03 |

## 5. Detailed unresolved findings

### N-01 — BLOCKER — Discharge at or above 50 A is "invalid" and is removed from sag analysis

- **File/lines:** `app/control/adaptive_output_policy.py:192` (default), `:543-544`, `:729`, `:809` (exclusion), `:342-345`, `:881`.
- **Violates:** architect rules ("Do not invent safe voltage or current thresholds"; "Validate measurements and use only approved baseline settings"); plan acceptance scenario 2.

**Reproducible inputs:** Line 08:50–10:00; Invert at 10:01 25.5 V, 10:03 25.0 V, 10:05 24.4 V, all at 2,000 W and 85 A; Line afterwards. Default algorithm parameters, exit 24.5, enter 25.5.

**Actual:**

```text
10:06 BLOCK lockout=None reasons=[INVALID_MEASUREMENT, ..., BATTERY_REBOUND_NOT_RECHARGE, ...]
10:54 BLOCK lockout=None reasons=[INVALID_MEASUREMENT, ..., MAINS_DWELL_UNPROVEN, ...]
evidence: history.significant_battery_discharge

Threshold, same trial, 10:54:
  49 A → lockout 11:05, FAST_VOLTAGE_SAG, COOLDOWN
  50 A → lockout None, no FAST_VOLTAGE_SAG

Two 85 A failures, 09:36, full history   → BLOCK, INVALID_MEASUREMENT, lockout None
Two 85 A failures, 09:36, 3600 s history → CONSIDER
Same data at 16:00, full history         → BLOCK (blocks while any such sample is supplied)
Healthy Invert, 60 A, 1500 W             → BLOCK (HOLD unreachable); 40 A at 1000 W → HOLD
Line, 0 W, 40 A                          → CONSIDER
```

**Expected:** a plausible 85 A reading is not invalid. It remains usable sag evidence, and the trial produces `FAST_VOLTAGE_SAG` with a lockout to 11:05. The reason states the true cause. Current thresholds aren't invented.

**Why it matters:** the evaluator's sag protection fails when the load is highest, which is when sag is most likely. `app/service/overload_protector.py:7-8, 22` monitors battery discharge around 40–55 A as a real alert condition, so such readings are clearly expected on this system. Roughly, 2,000 W at 26 V implies current far above 50 A (illustrative arithmetic only, not a measured electrical model). The tests never show this, since `sample()` defaults discharge to 2 A, even at 2,000 W.

**Required correction:**

1. Remove `significant_battery_discharge` from `_measurement_issues` (`:543-544`). Treat only impossible values as invalid, so that plausible samples stay in `_segment_sag` and `_trial_sag`.
2. Make `significant_battery_discharge_a`, `max_battery_discharge_a` and `max_battery_charge_a` either operator-approved values or `None`, not silent defaults. Defaults for these need architect or operator approval, since no battery hardware data is verified.
3. Report a separate reason for a current conflict. `INCONSISTENT_BATTERY_CURRENT` exists at `:78` and is never emitted. If the threshold isn't configured, conflict can't be proven safe, so a Line sample with near-zero output and non-zero discharge must not reach `CONSIDER`.
4. Old high-current samples shouldn't block indefinitely; limit validity checks to the evidence horizon.
5. Add tests of R-01, F-10 and long healthy sessions at 49, 50 and 85 A.

### N-02 — BLOCKER — The declared history requirement can't reconstruct active lockouts

- **File/lines:** `:658-659`, `:662-668`, `:846-857`, `:860-870`.
- **Violates:** "Historical cutoff does not discard active restrictions" and "At 09:36, CONSIDER must be impossible" (final-review request); design §4 and §6.

**Reproducible inputs:** the F-10 scenarios at 2-min cadence, with history trimmed to the requirement the evaluator itself reports (`history_requirement_seconds=3600`, `:411`).

**Actual:**

```text
Failures end 08:05, 08:35 (expiry 10:35)
  Full history:    09:36 BLOCK, lockout 10:35; 10:35 CONSIDER   (correct)
  3600 s history:  09:36 CONSIDER, coverage accepted
                   10:00 CONSIDER

Transitions 09:04–09:16 (expiry 10:16)
  Full history:    10:10 BLOCK, lockout 10:16
  3600 s history:  10:10 CONSIDER
```

The single 60-minute trial is safe under the same trim when samples are dense, because 3,600 s just covers it. Only events older than the declared horizon are lost.

**Expected:** with history short of what's needed to prove no restriction applies, the result is `BLOCK`, `INSUFFICIENT_HISTORY`.

**Why it matters:** a pure evaluator has only its input. A caller that supplies exactly what the function asks for, as replay or a restart-recovery loader naturally would, loses an active lockout.

**Required correction:** extend `_required_history_seconds` to cover the longest reconstructable restriction: the escalation lookback plus the maximum escalated lockout (with defaults, `max_churn_lockout_seconds` 240 min + capped lockout 180 min = 420 min), and `mode_change_window_seconds + churn_lockout_seconds` for churn. Test at the exact horizon boundary. The longer requirement makes OR-2 in section 13 more significant. The design's durable runtime-state journal (§4) is the eventual alternative; it isn't needed for Phase 1A.

### N-03 — MAJOR — Collapse longer than `failed_trial_max_seconds` gets no lockout

- **File/lines:** `:787-796`.
- **Violates:** design §6 ("When a trial shows a fast sag, record `FAST_VOLTAGE_SAG`, prevent immediate retry"), design §9 scenario 3. Reopens the first review's F-06 case (d). The original R-05 correction asked to classify such sags separately, not to ignore them.

**Reproducible inputs and actual:**

```text
Invert 10:02–10:14 (12 min) falling 25.8→24.6 V at constant load, then Line
10:16 BLOCK, lockout=None, only BATTERY_REBOUND_NOT_RECHARGE
11:01 CONSIDER (mains dwell only; the same collapse at 10 min locks to 11:12)

2 h healthy Invert, then 1.5 V collapse in the final 5 min, then Line
At last Invert sample: BLOCK, FAST_VOLTAGE_SAG
09:06 BLOCK, lockout=None
09:51 CONSIDER
```

**Expected:** a collapse near the end of a completed session restricts retries. It doesn't need to be labelled a *short failed trial*. Case H shows that a healthy long session must not be restricted, and it isn't.

**Required correction:** after a completed Invert segment, evaluate its final `voltage_sag_window_seconds` for a terminal fast sag regardless of total duration. Create a restriction with its own expiry, and reason if needed. Don't restrict for an old sag from earlier in a session that recovered. Test 10 min / 10 min + 1 sample / 2 h.

### N-04 — MAJOR — `DEMAND_EXCEEDS_PV` is a hard block

- **File/lines:** `:377-378`, `:947`; test `:99-102` encodes the behaviour.
- **Violates:** the architect's ruling: "`DEMAND_EXCEEDS_PV` is a contextual warning, not a universally sufficient reason to block."

**Reproducible inputs and actual:**

```text
1000 W / 700 W stable, 70 min history, all else valid  → BLOCK, only DEMAND_EXCEEDS_PV
1400 W / 1000 W, same                                  → BLOCK, only DEMAND_EXCEEDS_PV
```

**Expected:** the warning is shown but doesn't alone determine `BLOCK`. `HIGH_MEASURED_LOAD`, `PV_DEFICIT`, weak or declining solar, and battery-evidence blockers still stop high demand with weak solar: 5000 W/600 W and 2000 W/100 W must stay blocked by `HIGH_MEASURED_LOAD`.

**Required correction:** add `DEMAND_EXCEEDS_PV` to the non-blocking set at `:947`. Rewrite test `:99-102` to assert both a `CONSIDER` with this warning and the high-demand blocks.

### N-05 — MAJOR (R-02 remainder) — Improving sub-productive PV is reported as productive

- **File/lines:** `:632-633`, `:368-369`, `:640`, `:948`.
- **Violates:** final-review request ("Distinguish PV merely present / stable / genuinely productive"); design §9 ("strong sustained PV").

**Reproducible inputs and actual:**

```text
PV 300 W for 70 min, then 330 W, then 460 W; output 500 W → CONSIDER solar=IMPROVING reasons=[PRODUCTIVE_SOLAR]
PV 260→430 W; output 500 W                                → CONSIDER solar=IMPROVING reasons=[PRODUCTIVE_SOLAR]
```

A single sample's jump can qualify; the median was 300 W, below `productive_pv_w=500`.

**Expected:** no `PRODUCTIVE_SOLAR` below the threshold, and no `CONSIDER` on one sample's rise.

**Required correction:** let the `else` branch at `:368-369` produce `PRODUCTIVE_SOLAR` only for productive values. In `IMPROVING`, require current PV at or above `productive_pv_w`, with several samples of support. Any exception allowing a morning candidate below that threshold needs an explicit architectural decision. Add boundary tests.

### N-06 — MAJOR (F-10 remainder) — Unknown modes can hide churn

- **File/lines:** `:696-705`. `previous_at` isn't updated on unknown samples, so a transition across more than 300 s of unknowns is discarded.
- **Violates:** "Unknown modes cannot conceal known safety events."

**Reproducible inputs and actual:** 2-min cadence, `Line, Fault, Fault, Invert, Fault, Fault, Line, Fault, Fault, Invert, Fault, Fault, Line` (09:00–09:24), then Line. Four real known-mode changes; expected churn expiry 10:24.

```text
10:12 → CONSIDER chg=0 lockout=None
5-min cadence with one unknown between: 10:26 → CONSIDER chg=0
```

Mapped real DESS mode names such as `Battery Mode`, `PV Mode`, `Standby Mode` and `Bypass Mode` (`app/api.py:58-65`) also count as unknown here.

**Expected:** unknown samples are uncertainty, not a gap in continuity, when the telemetry itself is continuous. Known-mode changes across them count toward churn. No transition is invented across genuine telemetry gaps.

**Required correction:** track continuity using all samples' timestamps (including unknown-mode ones) and known modes separately. Test with one and two unknowns in between, and a real gap.

### F-13 — MAJOR, PARTIALLY_FIXED — Tests still omit or falsely pass material cases

- **Location:** `tests/test_adaptive_output_policy.py:149-161`, `:39-42`, `:99-102`, `:163-169`.

**Actual:**

- `:149-156`: `continuous_line` contains samples later than each snapshot, or at the same timestamp. Probed results at +6, +54 and +64 min are `dq=UNKNOWN`, so `BLOCK` would be returned regardless of the lockout. The `COOLDOWN` and sag assertions are genuine; the `BLOCK` assertion proves nothing. `:159-161` never asserts recovery, although it does return `CONSIDER`.
- `sample()` uses 2 A at any load, masking N-01.
- `:99-102` encodes N-04.
- Exact F-10 scenarios (08:05/08:35 at 09:36; churn at 10:05) are absent. `:210-214` checks a value once.
- No tests mention `DECLINING`, `ZoneInfo`, `INVALID_SUN_TIMES`, `command_executed`, `shadow_only`, `failed_trial_max_seconds` or `INVALID_BASELINE`.

**Expected and required correction:** add the regression tests in section 14.

## 6. Independently reproduced critical scenarios

Default algorithm parameters; exit 24.5 V, enter 25.5 V, hysteresis 0.5 V (test assumptions, not hardware facts).

**R-01 short collapse** (10:01 25.5 V, 10:03 25.0 V, 10:05 24.4 V, discharge below 50 A):

| Time | Decision | Lockout | Key reasons |
|---|---|---|---|
| 10:06 | BLOCK | 11:05 | `FAST_VOLTAGE_SAG`, `BATTERY_REBOUND_NOT_RECHARGE`, `COOLDOWN` |
| 10:54 | BLOCK | 11:05 | `FAST_VOLTAGE_SAG`, `COOLDOWN` |
| 11:04 | BLOCK | 11:05 | `FAST_VOLTAGE_SAG`, `COOLDOWN` |
| 11:05 | CONSIDER | None | Genuine expiry |
| 11:30 | CONSIDER | None | |

Cadences 0; 0,2; 0,4; 0,2,4; 0,1,2,3; 0,3,6; 0,2,4,6: all detected for a 26→24 V collapse. **At ≥50 A: not detected (N-01). Result: FAIL overall.**

**F-10 with full history:**

| Scenario | 09:36 / 10:05 | Expiry | Boundary |
|---|---|---|---|
| Failures 08:05, 08:35 | BLOCK | 10:35 | 10:34 BLOCK, 10:35 CONSIDER |
| Churn 09:04–09:16 | BLOCK | 10:16 | 10:15 BLOCK, 10:16 CONSIDER |

Later healthy samples don't erase restrictions; repeated evaluation returns identical expiry; escalation 60/120/180, then capped at 180. **Fails with accepted 3,600 s history (N-02) and hidden transitions (N-06). Result: FAIL overall.**

**R-02:** stable 260 W and 499 W `WEAK`; stable 500 W `STABLE`; a perfect forecast doesn't change stable 260 W. **Fails for `IMPROVING` (N-05). Result: FAIL overall.**

**R-03:** Line drift 26.0→25.6 V gives no sag, `CONSIDER`. Rebound is not recharge. **Result: PASS.**

**Required scenario matrix:**

| | Scenario | Result | Evidence |
|---|---|---|---|
| A | Positive path | **PASS** | Section 7 |
| B | 5,000 W / 600 W | **PASS** | `BLOCK`, `HIGH_MEASURED_LOAD` |
| C | 2,000 W / 100 W with irrelevant Tuya | **PASS** | `BLOCK`; evidence `measured_output_power_w=2000.0`; "Tuya totals not used" |
| D | 0 W with significant discharge | **PARTIAL** | 60 A blocks; 40 A `CONSIDER`; threshold invented (N-01) |
| E | Line samples 1 s apart | **PASS** | `BLOCK`, `INSUFFICIENT_HISTORY` |
| F | 400 days old | **PASS** | `STALE`, `BLOCK` |
| G | Collapse during settling | **PARTIAL** | Detected below 50 A only (N-01) |
| H | Long healthy session | **PASS at low current** | Line 09:50 `CONSIDER`, no lockout; at ≥50 A blocked (N-01) |
| I | Ordinary Line voltage movement | **PASS** | No `FAST_VOLTAGE_SAG` |
| J | Repeated failures | **PARTIAL** | Escalates correctly; N-01, N-02 |
| K | Gaps and unknown modes | **PARTIAL** | No fabricated dwell or switching; hidden transitions (N-06) |
| L | DST and site-local dates | **PASS** | Six timezones and both DST days; not tested in repo |
| M | Duplicates, malformed, future | **PASS** | All safe and deterministic; minor duplicate note in F-18 |

## 7. Positive CONSIDER-path evidence — PASS

```text
Time 11:15 UTC; evaluated_at 30 s later; sunrise 06:00, sunset 20:00
History: 80 min continuous Line Mode at 2-min cadence
Output 700–760 W; PV 950–990 W; battery 26.4 V; discharge 0 A; charge 3 A
SOC alternating 70/100%; forecast 9 min old, valid 45 min ahead
Default algorithm parameters including 180-min forecast limit

→ CONSIDER dq=FRESH solar=STABLE fc=FRESH batt=ELIGIBLE lockout=None chg=0 age=30.0
  reasons=[SHADOW_ONLY, PRODUCTIVE_SOLAR]; shadow_only=True; command_executed=False
```

Not a trivial always-`BLOCK`: a low-variation fuzz (20,000 cases, seed 11) returned `BLOCK` 14,259, `CONSIDER` 4,608, `HOLD` 1,133.

## 8. Time and determinism verification — PASS

- `evaluated_at` is a required keyword argument, timezone-aware (`:319, :323`).
- No `.now(`, `.utcnow(`, `.today(`, `time.time` or `import time` in the source. Evaluation succeeded with `time.time`, `open` and `socket.socket` patched to raise.
- Repeat decisions equal across a 20,000-case fuzz run, each case evaluated twice. Repeated lockout scenarios returned identical expiry.
- The module-level assignment is only `__all__`, with no hidden state.
- All datetimes normalise to UTC. DST fold gap and expiry instants are correct.

## 9. Battery and inverter safety assessment — FAIL

**Pass:** exit guard in Invert Mode; no protective boundary lowered in any path; `HOLD` needs fresh evidence and doesn't hide breaches; SOC unused for eligibility; rebound never treated as recharge; lockouts monotonic with full history; missing evidence blocks.

**Fail:** N-01 (no sag detection at high current), N-02 (lockout lost at accepted history), N-03 (no restriction after longer collapse).

**Anti-flapping — FAIL:** timed churn is correct with full history, but N-02 and N-06 remain. Transitions are observed only and never claimed as commands of this application.

## 10. Solar and load model assessment

**Load — PASS except N-04.** Inverter output is authoritative; Tuya isn't an input; `None` isn't zero; PV minus output isn't presented as battery power. High demand with weak solar can't reach `CONSIDER`.

**Solar — PARTIAL.** Actual sun times, phases, decline and forecast freshness work. Forecast doesn't override weak measured PV. Remaining: N-05, and `valid_at` horizon (F-11, minor).

## 11. Test commands and actual results

| Command | Result |
|---|---|
| `python3 -m py_compile app/control/adaptive_output_policy.py tests/test_adaptive_output_policy.py` (with `PYTHONPYCACHEPREFIX` pointing outside the repository) | Exit 0 |
| `python3 -m unittest discover -s tests -p 'test_*.py'` | **Ran 27 tests, OK**; developer's count independently confirmed |
| `git diff --check` | Exit 0, no output |
| `git status --short` | See section 12 |
| Offline probes outside the repository (`finalprobe.py`, `final2.py` in the session scratchpad) | Quoted in sections 5–8 |
| Fuzz, seed 11, 2 runs of 20,000 | Both: 0 `BLOCK` lacking a specific reason; 0 non-`FRESH` `CONSIDER`. Run 1 also checked repeat decisions equal and `command_executed` false; its mix included 60 A history and returned `BLOCK` for all 20,000, itself a symptom of N-01. Run 2 (no discharge) gave the decision mix in section 7 |

Environment: system Python 3.14.7 (`.venv/bin/python` doesn't resolve). Fuzz checks invariants, not safety correctness. It did not find N-01 to N-06.

## 12. Side-effect safety verification — PASS

| Requirement | Result |
|---|---|
| Imports | Only `__future__`, `dataclasses`, `datetime`, `enum`, `math` |
| No DESS or Tuya commands | None present; no such modules loaded |
| No DB, filesystem or network | None; `requests`, `aiohttp`, Tuya, `psycopg2`, `asyncpg`, `yaml`, `sqlite3`, `httpx` not loaded |
| No runtime integration | Module referenced only by its test (checked in the earlier review; files unchanged at HEAD) |
| No config or schema changes | `git diff --stat HEAD` empty; `devices.yaml` is git-ignored, unchanged by this review |
| Immutability | Frozen dataclasses; `object.__setattr__` only for UTC normalisation in `__post_init__` |
| Shadow-only | `shadow_only=False` and `command_executed=True` rejected at construction |

```text
?? .project-memory/reviews/
?? app/control/adaptive_output_policy.py
?? tests/
```

## 13. Remaining operational limitations

These are not Phase 1A code defects, but they affect what Phase 2 can show.

- **OR-1 (unverified):** `app/ml/ml_data_collector.py:585` says `output_power` is `None` in Line Mode. If production telemetry confirms it, `CONSIDER` is unreachable in practice. Verify with a read-only sample before Phase 2.
- **OR-2:** the plan documents about 1800 s grid-mode cadence, while coverage requires gaps of 300 s or less. Replay of such periods can't reach `CONSIDER`. N-02's longer horizon makes this stricter.
- **Battery facts:** chemistry, BMS limits, real inverter thresholds and site timezone remain unverified. All voltage values in this review are test assumptions.
- **Numeric parameters** remain illustrative (design §6), and need simulation or replay tuning.
- **Plan Gate 1 "restart-safe read of baseline YAML"** isn't implemented; treated as out of Phase 1A scope, as in prior reviews.

## 14. Required corrections

**Before approval:**

1. **N-01:** stop treating plausible current as invalid; keep samples in sag analysis; approved-or-`None` current thresholds; emit `INCONSISTENT_BATTERY_CURRENT`.
2. **N-02:** extend the required history to reconstruct all active restrictions.
3. **N-03:** restrict after a terminal collapse beyond `failed_trial_max_seconds`, without mislabelling healthy sessions.
4. **N-04:** make `DEMAND_EXCEEDS_PV` non-blocking on its own.
5. **N-05:** apply the productive threshold to `IMPROVING` and to `PRODUCTIVE_SOLAR`, or obtain an explicit decision for a morning exception.
6. **N-06:** keep unknown samples from hiding known-mode churn.
7. **F-13, regression tests:**
   - Fix `:149-156` so history contains nothing at or after the snapshot, and assert recovery to `CONSIDER`.
   - R-01, F-10 and long-session cases at 49, 50, 85 A.
   - Exact 09:36 and 10:05 cases; history at and just below the new horizon.
   - 12-minute and 2-hour terminal collapse.
   - Demand warning with and without `CONSIDER`.
   - Improving PV at 460 W and 500 W.
   - Unknown-mode churn.
   - DST, non-UTC sun times, `DECLINING`, `command_executed` rejection.

**Decision needed from the architect or operator:** values for current thresholds (N-01), and whether a sub-productive morning candidate is allowed (N-05).

**MINOR, may be fixed in the same pass (don't block approval on their own):** F-07 two-sample step inconsistency; F-11 `valid_at` horizon; F-12 `HOLD`/`BLOCK` definitions and sun-time attribution; F-16 relational and zero-value validation; F-18 duplicates within history; duplicate `HIGH_BATTERY_DISCHARGE` checks at `:346-349`; unreachable check at `:246-247`.

**SUGGESTION:** document the caller contract: required history length, snapshot not in history, cadence.

## 15. Final approval recommendation

**Do not approve Phase 1A. Do not start Phase 2 replay or any wiring until N-01 and N-02 are fixed and independently verified.**

The core design is sound and should be kept: `evaluated_at`, history reconstruction of lockouts, UTC normalisation, Invert-only sag, shadow-only enforcement. The remaining corrections are small and targeted, and most are a few lines each. The review after them can focus on sections 5 and 14.

The module stays passive, unwired and uncommitted, so there is no current hardware risk.
