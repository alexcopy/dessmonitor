# Independent Re-Review — Adaptive Output Control, Phase 1A

## 1. Review metadata

| | |
|---|---|
| **Date** | 2026-11-02 |
| **Reviewer role** | Independent reviewer; did not implement the code or the corrections |
| **Repository / branch** | `alexcopy/dessmonitor` / `feat/adaptive-output-control` |
| **HEAD** | `b19cd7a1c5cd71d8c56676f5ed2aeba286c13791`, plus untracked working-tree files |
| **Previous verdict** | `CHANGES_REQUIRED` (`ADAPTIVE_OUTPUT_PHASE1A_REVIEW.md`) |

**Read in full:**

- `app/control/adaptive_output_policy.py` (869 lines, untracked)
- `tests/test_adaptive_output_policy.py` (265 lines, untracked)
- `.project-memory/reviews/ADAPTIVE_OUTPUT_PHASE1A_REVIEW.md`

**Reference contract:** `.project-memory/ADAPTIVE_OUTPUT_CONTROL_DESIGN.md` and `.project-memory/ADAPTIVE_OUTPUT_CONTROL_IMPLEMENTATION_PLAN.md`, read in the earlier review this session. `git diff HEAD` shows neither has changed since.

**Scope:** F-01 to F-20, new regressions, the positive `CONSIDER` path, and passivity. The implementation is passive and no hardware was contacted.

## 2. Overall verdict: `CHANGES_REQUIRED`

| Result | Count | Findings |
|---|---:|---|
| FIXED | 8 | F-01, F-03, F-04, F-05, F-08, F-14, F-18, F-19 |
| PARTIALLY_FIXED | 12 | F-02, F-06, F-07, F-09, F-10, F-11, F-12, F-13, F-15, F-16, F-17, F-20 |
| NOT_FIXED | 0 | |
| NOT_VERIFIABLE | 0 | |
| New findings | 5 | R-01 BLOCKER; R-02, R-03 MAJOR; R-04, R-05 MINOR |

## 3. Executive technical assessment

The correction is substantial and much of it is sound. `evaluated_at` resolves the freshness defect, missing evidence no longer authorises anything, high load blocks on its own, exit voltage is evaluated, and a legitimate `CONSIDER` is reachable. Phase 1A remains passive and deterministic.

It can't be approved, for these reasons:

1. **R-01 (BLOCKER, regression):** the new settling filter removes the evidence needed to detect short collapses. The original five-minute collapse scenario now produces no sag, no lockout, and **`CONSIDER` at 10:54**. The previous implementation at least detected it on the first Line sample.
2. **F-10:** timed lockouts and escalation exist, but sliding windows end them before their own reported expiry. A reported 10:35 expiry disappears at 09:10 and the result is `CONSIDER` at 09:36.
3. **R-02:** `productive_pv_w` is no longer read. A stable 260 W PV supply is reported as `PRODUCTIVE_SOLAR` and returns `CONSIDER`.
4. **R-03:** Line Mode voltage drift raises `FAST_VOLTAGE_SAG`.

The 18 passing tests miss all of these. The failed-trial test uses sample timing that happens to leave two samples after the settling interval.

## 4. F-01 – F-20 verification matrix

Line numbers refer to `app/control/adaptive_output_policy.py`, and test lines to `tests/test_adaptive_output_policy.py`.

| ID | Original severity | Status | Current location | Evidence | Test coverage | Remaining problem |
|---|---|---|---|---|---|---|
| F-01 | BLOCKER | **FIXED** | `:299-309, :479-483, :587` | 400-day and 1-day data: `BLOCK`, `STALE`. +1 s future: `UNKNOWN`, `FUTURE_TELEMETRY`. Age 180 s `FRESH`; 181 s `STALE`. Missing `evaluated_at`: `TypeError`. Naive: `ValueError`. No clock tokens in source | `:198-212, :255-261` | Minor: `received_at` later than `evaluated_at` is not detected (returned `CONSIDER`). No test for "yesterday" specifically |
| F-02 | BLOCKER | **PARTIALLY_FIXED** | `:710-724, :741-752` | Persistence works: a detected trial blocks at 10:06, 10:07, 10:54, 11:04 and returns `CONSIDER` at 11:05. **The original scenario isn't detected: `CONSIDER` at 10:54** | `:123-138` | See R-01 and F-10 (window truncation). Test never proves recovery to `CONSIDER` |
| F-03 | BLOCKER | **FIXED** | `:352-357, :838` | 5000 W/600 W: `BLOCK`, `HIGH_MEASURED_LOAD`. 1500 W/3000 W: `BLOCK`. 1499 W/3000 W: `CONSIDER`. Tuya isn't an input | `:90-101` | Note: `DEMAND_EXCEEDS_PV` is a hard veto (1000 W/700 W: `BLOCK`), though design §5.2 calls it a rough warning. Conservative; operator should confirm. `:92` doesn't test 1499 W with strong PV |
| F-04 | BLOCKER | **FIXED** | `:597-607, :565, :772, :786-790` | 1 s apart, 2 min apart, empty: `BLOCK`, `INSUFFICIENT_HISTORY`, `UNKNOWN`. 59 min: `BLOCK`. 60 min: `CONSIDER` | `:103-113, :230-238` | None for this finding |
| F-05 | MAJOR | **FIXED** | `:826-830, :775, :833-834` | Invert 24.2 V (exit 24.5): `BLOCK`, `EXIT_BATTERY_GUARD`. `None` voltage: `BLOCK`. Stale: `BLOCK`. Healthy: `HOLD` | `:148-153` | `None`/stale still show `batt=STABLE` next to `BLOCK` (see F-12). No test for missing voltage |
| F-06 | MAJOR | **PARTIALLY_FIXED** | `:659-691, :698-707, :713-723` | Fixed: late collapse, growing load, one sample, 11-min collapse (now with lockout). **Not detected: 2–4 Invert samples inside or just after the first 180 s** | `:140-146, :181-190` | R-01. No test for growing load or the 11-min case |
| F-07 | MAJOR | **PARTIALLY_FIXED** | `:665-676, :698-707` | Settling step and middle spike no longer report sag. **Gradual load ramp is still sag; one-sample step creates a lockout** | Only incidentally in `:181-190`; no direct assertion | See section 5 |
| F-08 | MAJOR | **FIXED** | `:610-628, :631-645` | 15-min and 6-min gaps no longer give `STABLE`; 3 h gap `BLOCK` | `:230-238` (Line only) | No Invert-gap test. See section 13 for replay implications |
| F-09 | MAJOR | **PARTIALLY_FIXED** | `:331-332, :500-513` | Negative values, 9999 V, simultaneous currents, and over 100 A block. **99 A, 0 W with 90 A, and 99 V return `CONSIDER`; Invert 90 A/500 W returns `HOLD`** | `:224-228` | See section 5 |
| F-10 | MAJOR | **PARTIALLY_FIXED** | `:741-761, :631-645` | Lockout fields and unknown-mode handling at 2-min cadence work. **Lockouts end before reported expiry; escalation doesn't hold** | `:155-179` | See section 5 |
| F-11 | MAJOR | **PARTIALLY_FIXED** | `:161, :584-594` | Default 10800 s; 180 min `FRESH`, 181 min `STALE`; past, empty, no-`valid_at`, future-issued all rejected | `:214-222` | Minor: `valid_at` one year ahead is `FRESH`. Test named "default_age" never tests the default, since `:68` still overrides it to 600 s. Content-based blocking stays a recommendation (storm: `CONSIDER`) |
| F-12 | MAJOR | **PARTIALLY_FIXED** | `:15-20, :339-350, :392-402` | Fuzz: 19,943 `BLOCK` results, none lacking a specific reason. `NIGHT`, `MAINS_DWELL_UNPROVEN`, `BATTERY_TRIAL_IN_PROGRESS`, `PRODUCTIVE_SOLAR` added | No nonempty-blocker test | See section 5 |
| F-13 | MAJOR | **PARTIALLY_FIXED** | test file | 18 pass; many are real behaviour tests. They miss R-01 to R-03 and the F-10 expiry defects | | See section 5 |
| F-14 | MINOR | **FIXED** | `:108-109, :148-149, :435-436` | UTC normalised. DST fold gap computed correctly: `STALE` at 3000 s limit (real 6000 s). Later real sample ordered correctly | **None** | Add DST tests |
| F-15 | MINOR | **PARTIALLY_FIXED** | `:544-550` | Mismatched-day and over-24 h intervals give `UNKNOWN` | **None** | R-04 |
| F-16 | MINOR | **PARTIALLY_FIXED** | `:146-147, :201-232` | Non-string mode rejected. **Negative baseline values still accepted**, because `allow_zero=True` remains a no-op: `telemetry_max_age_seconds=-5`, `voltage_sag_min_drop_v=-1`, `high_measured_output_w=-1`, `max_battery_discharge_a=-1`, `min_pv_samples=True` | **None** | Reject negatives and non-int counts |
| F-17 | MINOR | **PARTIALLY_FIXED** | `:37, :163-164, :575-580` | `EARLY_MORNING` and minimum PV span added; 1-minute sunrise spike rejected | **None** for phases | 830→700 W decline at 16:00 is still `STABLE` with `CONSIDER`; no declining phase |
| F-18 | MINOR | **FIXED** | `:459-461, :477-478` | Snapshot-timestamp duplicate gives `UNKNOWN`, `BLOCK` | **None** | Residual: duplicates *within* history aren't detected, and a conflicting pair created `chg=2`. A caller passing the snapshot inside history always gets `UNKNOWN` (needs documenting) |
| F-19 | MINOR | **FIXED** | `:771` | Now only from failed events; healthy segments no longer populate it | **None** | Residual: long-expired failures still appear (`240.0` at 16:00) |
| F-20 | MINOR | **PARTIALLY_FIXED** | `:283-288` | `command_executed=True` and `shadow_only=False` rejected at construction (verified) | **None** | `HOLD/ADVISE/BLOCK` vs `CONSIDER` naming, absent `output_priority`, and always-`None` `effective_*` remain |

**All 20 statuses:** F-01 FIXED; F-02 PARTIALLY_FIXED; F-03 FIXED; F-04 FIXED; F-05 FIXED; F-06 PARTIALLY_FIXED; F-07 PARTIALLY_FIXED; F-08 FIXED; F-09 PARTIALLY_FIXED; F-10 PARTIALLY_FIXED; F-11 PARTIALLY_FIXED; F-12 PARTIALLY_FIXED; F-13 PARTIALLY_FIXED; F-14 FIXED; F-15 PARTIALLY_FIXED; F-16 PARTIALLY_FIXED; F-17 PARTIALLY_FIXED; F-18 FIXED; F-19 FIXED; F-20 PARTIALLY_FIXED.

## 5. Unresolved BLOCKER and MAJOR findings

### F-02 (BLOCKER, PARTIALLY_FIXED) and F-06 (MAJOR, PARTIALLY_FIXED)

Persistence and the original F-06 cases are fixed. The remaining failure is one root cause, **R-01** in section 6. Both close only when R-01 closes.

### F-07 — MAJOR, PARTIALLY_FIXED — Sag false positives remain

**Location:** `:669-676` (adjacent-only load comparison), `:698-707` (one-sample path ignores settling)

**Actual and reproduction:**

```text
Invert, output rising 200 W per 2-min sample 500→1700 W, voltage 26.0→25.44 V
→ FAST_VOLTAGE_SAG, batt=INELIGIBLE (drop=0.40)

L 26.0 V | one Invert sample 25.6 V at the same load | L 26.0 V
→ FAST_VOLTAGE_SAG, COOLDOWN, lockout 60 min
```

The first accumulates load drift, because every adjacent step is within the 250 W tolerance. The second treats a normal step-down at the Line→Invert boundary as a failed trial, although design §5.3 says to omit that settling period.

**Expected:** compared samples must themselves be within tolerance. A single settling-period sample with a small step is not confirmed collapse; it may block as insufficient evidence.

**Correction:** check load tolerance between each compared pair, not only adjacent samples. Set a stronger threshold for the one-sample path, or classify it as an uncertain short trial with its own reason. The R-01 fix must be designed alongside this.

### F-09 — MAJOR, PARTIALLY_FIXED — Discharge current is only an upper bound

**Location:** `:331-332`, `:506`, `:508-510`; test `:225`

**Actual and reproduction** (default baseline, otherwise valid):

```text
Line, discharge=99 A                → CONSIDER
Line, output=0 W, discharge=90 A    → CONSIDER
Line, battery_voltage=99 V          → CONSIDER
Invert 70 min, 90 A at 500 W        → HOLD batt=STABLE
History sample output=-5000 W       → CONSIDER
History sample voltage=9999 V       → FAST_VOLTAGE_SAG, drop=9973
```

- The test's `output=0, discharge=300` case passes only because 300 A exceeds 100 A; no consistency check exists.
- Discharge doesn't affect eligibility below an unverified 100 A limit, and the same limit is applied to charge current.
- History isn't validated.

**Expected:** design §5.2 says to prefer observed battery discharge current. Line Mode discharge, or current contradicting output, should be classified rather than ignored. Implausible history must not feed calculations.

**Correction:** add a conservative rule for significant discharge in Line Mode, and a rough consistency check for high current with near-zero output, without claiming an AC/DC power balance. Validate history samples. Give charge current its own limit. Use a voltage bound derived from an operator-approved or nominal system setting instead of 100 V; the value itself needs operator input, since no chemistry or series count is verified.

### F-10 — MAJOR, PARTIALLY_FIXED — Lockouts end before their stated expiry

**Location:** `:744-749` (failure window), `:756-761` (churn window), `:598` (history horizon)

**Actual and reproduction:**

```text
(a) Transitions 09:04, 09:08, 09:12, 09:16; Line polled every 2 min
    09:18–10:03 → BLOCK, lockout_expires_at=10:16
    10:05       → CONSIDER, lockout=None

(b) Failed trials ended 08:05 and 08:35
    08:36–09:00 → lockout_expires_at=10:35 (escalated)
    09:10       → lockout_expires_at=09:35 (earlier failure left the window)
    09:36       → CONSIDER

(c) Three failures at 70-min spacing → 60-min lockout each time; no escalation

(d) failed_trial_lockout_seconds=7200, trial ended 10:05
    11:10 → CONSIDER (operator setting truncated by the 60-min window)
```

The calculations are recomputed from a window equal to the lockout, so a lockout's own trigger leaves the window before the lockout ends. Design §6 requires a timed quiet period, and a reported expiry must be reliable.

**What passes:** unknown modes at 2-min cadence no longer hide flapping (`chg=4`). Transitions stay observed-only, with no claim of a commanded origin. A minor rebound can't bypass a lockout. Normal single-trial expiry is correct (11:04 blocked, 11:05 `CONSIDER`).

**Expected:** once triggered, expiry doesn't move earlier, and `BLOCK` continues until the stated time, given the same evidence.

**Correction:** determine the trigger from events inside the required horizon, then compute expiry from the triggering events, without re-filtering by the current window. Extend `_required_history_seconds` to cover trigger window + lockout + maximum escalation. Define escalation across lockout-length spacing (c). Test each poll up to expiry. Where unknown-mode samples stretch known samples more than 300 s apart, `chg` was 0 in a probe (blocked by other checks here); count unknown-separated transitions conservatively.

### F-12 — MAJOR, PARTIALLY_FIXED — Decision meanings still undefined

**Location:** `:15-20`, `:347-348`, `:392-402`, `:801`

Blocker reasons are now reliable. What remains:

- Only `CONSIDER` has a stated meaning. `HOLD` and `BLOCK` don't, and `BLOCK` in Invert Mode covers exit-guard breach, a trial in progress, and stale data, though nothing is being "blocked" there. The plan's scenario table expects `HOLD`/`UNKNOWN` for stale data.
- Mis-dated sun times report `INSUFFICIENT_HISTORY` (`:347-348`), which is the wrong cause.
- Probes show conflicting pairs: `batt=ELIGIBLE` with `STALE` and `BLOCK`; `batt=STABLE` with `BLOCK`. `battery_eligibility` is not stated to be independent of data quality.
- `:801` emits `FAST_VOLTAGE_SAG` when any historical failure exists and any lockout is active, so a churn lockout can be attributed to an old sag (from source; not separately probed).

**Expected:** an operator can read the decision without reading the code.

**Correction:** document all three decisions per mode, and add a stale/fault outcome, or document `BLOCK` as covering it. Add a sun-times reason code. Tie sag reasons to the active failed-trial lockout. Add a test for specific blocker reasons.

### F-13 — MAJOR, PARTIALLY_FIXED — Tests still miss material failures

**Location:** test file, especially `:68`, `:123-138`, `:126-128`, `:92`, `:214-222`

- `:126-128` places Invert samples at −8, −5, −2 minutes, leaving two after settling. The original 10:01/10:03/10:05 scenario isn't tested, and it fails.
- `:130-137` evaluates with no Line samples between trial and snapshot, so `coverage_ok` is false. It can't show recovery; it asserts only that `COOLDOWN` is absent.
- The earlier determinism and no-I/O tests were **removed**. Plan Gate 1 explicitly requires zero hardware calls and deterministic decisions.
- `:68` still overrides the forecast default.
- No tests for DST, positive `CONSIDER` under default forecast settings, Line Mode sag, weak-stable PV, churn expiry, or escalation expiry.

**Correction:** add the tests listed in section 14.

## 6. Newly introduced findings

### R-01 — BLOCKER (regression) — Settling filter discards short battery collapses

- **File/lines:** `:665-666` (samples in first 180 s discarded), `:681-691` (needs two remaining samples), `:720-723` (fallback only when the segment has exactly one sample)
- **Violates:** design §6 (`BATTERY_TRIAL`: "quickly detect voltage sag under load"; "record `FAST_VOLTAGE_SAG`, prevent immediate retry"), design §9 scenario 3, plan acceptance scenario 2, design acceptance criterion 3.

**Actual behaviour.** Settling removes everything from the first 180 s. If fewer than two samples remain and the segment isn't exactly one sample, the result is `None`: no sag and no failed trial.

**Reproduction (the original review scenario):**

```text
Line 08:50–10:00 at 26.0 V | I@10:01 25.5 | I@10:03 25.0 | I@10:05 24.4 | Line
10:06 → BLOCK lockout=None reasons=[..., BATTERY_REBOUND_NOT_RECHARGE]   (no FAST_VOLTAGE_SAG)
10:07 → BLOCK lockout=None reasons=[..., MAINS_DWELL_UNPROVEN]
10:54 → CONSIDER batt=ELIGIBLE lockout=None                              (also with a 2000 W trial)
```

26.0→24.0 V collapse, by Invert sample timing (minutes from first Invert sample):

| Samples | Sag detected | Lockout |
|---|---|---|
| 0 | Yes | 60 min |
| 0, 2 | **No** | **None** |
| 0, 4 | **No** | **None** |
| 0, 2, 4 | **No** | **None** |
| 0, 1, 2, 3 | **No** | **None** |
| 0, 3, 6 | Yes | 60 min |
| 0, 2, 4, 6 | Yes | 60 min |

The plan documents about 120 s collector cadence in Invert Mode and a 60 s monitor poll, so these timings are realistic. Failed trials of about 5 minutes are the exact behaviour this system exists to detect.

**Expected:** `BLOCK` with `FAST_VOLTAGE_SAG` and `COOLDOWN` until 11:05 for every case in the table with a real collapse.

**Correction (recommendation).** Keep settling exclusion for slope estimation during a long or in-progress segment, but do not let it erase a completed short trial. For an Invert→Line segment within `failed_trial_max_seconds`:

- use settled samples where available;
- otherwise compare first and last Invert samples at comparable load;
- also evaluate last Invert voltage against the pre-trial Line voltage as supporting evidence.

An insufficient-sample short trial should end as a conservative outcome, never as `ELIGIBLE` after dwell alone. This also returns `failed_trial_max_seconds` to use (see R-05).

### R-02 — MAJOR (regression) — `productive_pv_w` is unused; marginal PV is "productive"

- **File/lines:** `:575-576`, `:350`, `:167-168`. `baseline.productive_pv_w` has zero reads. The prior code gated `STABLE` on `median_pv >= productive_pv_w`.
- **Violates:** design §9 ("strong sustained PV"), and §5.4 priority 4 (use battery only when conditions suggest a sustainable interval).

**Reproduction:**

```text
PV 260 W stable, output 500 W, 70 min history → CONSIDER solar=STABLE reasons=[PRODUCTIVE_SOLAR]
PV 300 W stable, output 500 W, 70 min history → CONSIDER solar=STABLE reasons=[PRODUCTIVE_SOLAR]
```

**Expected:** PV below `productive_pv_w` is not `PRODUCTIVE_SOLAR`, and can't return `CONSIDER` unless genuinely improving and supported by other evidence.

**Correction:** restore the gate at `:576`, and add boundary tests at 499/500 W. Reconsider whether `IMPROVING` should need the same evidence.

### R-03 — MAJOR (regression) — Line Mode voltage drift raises `FAST_VOLTAGE_SAG`

- **File/lines:** `:767-768` (segment selected for the current mode, including Line), `:801`, then `:838` (any reason is a blocker)
- **Violates:** design §5.3 and plan Phase 1 (sag from `Invert Mode` samples). Also an evidence-integrity problem.

**Reproduction:**

```text
Line Mode for 70 min, no battery trial; voltage 26.0→25.6 V over the last 10 min
→ BLOCK batt=ELIGIBLE drop=0.4 reasons=[..., FAST_VOLTAGE_SAG]
```

The result claims a battery-trial sag that didn't happen, and shows `ELIGIBLE` alongside it. Line Mode voltage decay after charging is a normal possibility. There is also no lockout or expiry, so the block ends only when the 15-minute window clears.

**Expected:** no `FAST_VOLTAGE_SAG` from Line Mode samples. Falling voltage below `enter_battery_v` is already covered by `BATTERY_GUARD`.

**Correction:** compute sag only for Invert segments. If Line Mode voltage decline should be a signal (RECOMMENDATION), give it a separate reason code.

### R-04 — MINOR (regression) — Sun-time date check uses UTC dates

- **File/lines:** `:545-546`, after `_utc()` normalisation.
- **Reproduction** (11:00 local, 2 Nov, valid same-day sun times):

| Zone | Result |
|---|---|
| Europe/London (Nov and June) | `CONSIDER` |
| America/New_York | `CONSIDER` |
| Asia/Kolkata | `CONSIDER` |
| America/Los_Angeles | `BLOCK`, solar `UNKNOWN`, `INSUFFICIENT_HISTORY` |
| Asia/Tokyo | same `BLOCK` |
| Australia/Sydney | same `BLOCK` |

Also, London 00:30 BST returns `UNKNOWN` instead of `NIGHT`.

`run.py:147-149` assumes Europe/London, so the likely site is unaffected in daylight, hence MINOR. The design still lists site timezone as unverified. **At a site where this applies, it would permanently block; raise it to MAJOR if so.**

**Correction:** validate the interval directly (`sunrise < sunset`, bounded length, snapshot within about 24 h of it) without comparing UTC dates, or pass the site timezone explicitly.

### R-05 — MINOR — `failed_trial_max_seconds` unused; long healthy segments count as failed trials

- **File/lines:** `:179` (zero reads), `:713-719`
- **Reproduction:** 3 h Invert, sag only in the first 20 minutes, voltage then flat, returns to Line → `FAST_VOLTAGE_SAG`, `COOLDOWN`, lockout to 09:58. A long successful run is labelled a failed trial. Conservative, but it pollutes replay statistics and contradicts YAML `failed_trial_window_min: 10`.
- **Correction:** use the parameter, or remove it. Classify sag near the end of a long segment separately from a failed short trial.

## 7. Determinism and telemetry-freshness assessment — PASS

- **Determinism:** 20,000 randomised scenarios gave equal repeat decisions; no global state, no clock tokens in source. Imports are `__future__`, `dataclasses`, `datetime`, `enum`, `math` only.
- **Freshness:** `evaluated_at` is mandatory and timezone-aware, supplied by the caller, and the evaluator doesn't read the clock. The 400-day case is `STALE`/`BLOCK`.
- **Future:** a future snapshot, history sample or forecast isn't fresh.
- **Timezones:** all datetimes normalise to UTC; naive input is rejected; DST fold gaps compute correctly.
- **Residuals:** `received_at` after `evaluated_at` isn't flagged; R-04.

## 8. Load and solar-window assessment

**Load — PASS.** `output_power_w` is authoritative, there's no Tuya input, and `None` isn't converted to zero. 5000 W/600 W blocks. PV minus output isn't treated as measured battery power; it only produces a warning code. See F-09 for current handling and OR-1.

**Solar — PARTIAL (FAIL on R-02).** Actual sun times apply; `NIGHT`, `LATE_DAY`, `WEAK`, `EARLY_MORNING`, `IMPROVING` and `STABLE` are distinguished. Weak PV near sunset blocks. Forecast staleness works. Gaps: R-02, a slow decline shown as `STABLE` (F-17), R-04.

## 9. Battery-sag and anti-flapping assessment

**Battery safety — FAIL.**

**Rapid collapse protection is not reliably effective after the corrections.** It works for segments with at least two samples after 180 s, and the exit guard works during Invert Mode. It fails for shorter trials (R-01), which then return `CONSIDER` once the 45-minute dwell passes.

Passing: **no code path lowers or modifies a protective boundary after a failed trial.** Rebound isn't treated as recharge, and the 60-minute lockout persists once a trial is detected.

**Anti-flapping — FAIL.** Observed transitions are counted, unknown modes don't fabricate transitions, and the code never treats a transition as a command by this application. However, timed lockouts end early and escalation doesn't hold (F-10).

## 10. Baseline and side-effect safety assessment

**Baseline — PASS (with observation).** Voltage thresholds default to `None`; unknown values block via `UNKNOWN_OUTPUT_CAPABILITY`; the baseline is frozen and echoed unchanged; no 25.5/26.5 V defaults exist.

Observation: `max_battery_discharge_a=100` and the 100 V plausibility limit are generic parameters, not verified hardware facts. The earlier docstring saying so was shortened, and should be restored.

**Side effects — PASS.**

| Requirement | Result |
|---|---|
| Pure deterministic function | Pass |
| Immutable inputs and outputs | Pass (frozen dataclasses; `object.__setattr__` only for UTC normalisation in `__post_init__`) |
| Hidden runtime state | None |
| Hardware, DESS or Tuya writes | None |
| Database or filesystem writes | None |
| Runtime integration | None; only the test file references the module |
| YAML modification | None; `devices.yaml` is git-ignored, modified 9 Jul 2026 (predates this work) |
| Fabricated hardware thresholds | None |
| SOC eligibility | None; only validation reads `battery_soc`, and 70%↔100% across history doesn't alter results |
| Charger-source behaviour | Unchanged; `git diff HEAD` empty |
| Transitions as issued commands | Never claimed |
| `command_executed` | Always `False`; `True` is rejected at construction |

## 11. Positive CONSIDER-path verification — PASS

Independent scenario, using production-default algorithm parameters (including the 180-minute forecast default, unlike the test helper):

```text
Time 11:15 UTC, evaluated 30 s later; sunrise 06:00, sunset 20:00
History: 80 min continuous Line Mode at 2-min cadence
Output 700–760 W; PV 950–990 W; battery 26.4 V; discharge 0 A; charge 3 A
SOC alternating 70%/100%; forecast 9 min old, valid 45 min ahead
Baseline: exit 24.5, enter 25.5, hysteresis 0.5 (test assumptions, not hardware values)

→ CONSIDER dq=FRESH solar=STABLE fc=FRESH batt=ELIGIBLE lockout=None chg=0 age=30.0
  reasons=[SHADOW_ONLY, PRODUCTIVE_SOLAR]
  shadow_only=True command_executed=False
```

Recovery: a detected trial that locks out until 11:05 returns `CONSIDER` at 11:05 and at 11:30.

**Caveat:** `CONSIDER` is also reachable where it shouldn't be (R-01, R-02, F-09, F-10).

## 12. Tests executed and results

| Command / method | Result |
|---|---|
| `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_adaptive_output_policy -v` (Python 3.14.7) | **18 run, 18 passed** |
| `PYTHONPATH=. python3 <scratchpad>/reprobe.py` (outside the repository) | Scenarios quoted throughout this report |
| Fuzz (seed 7, 20,000 cases) | `BLOCK` 19,943; `HOLD` 51; `CONSIDER` 6. Repeat decisions equal; `command_executed` never true; 0 `BLOCK` lacking a specific reason; 0 non-`FRESH` `CONSIDER` |
| `patch(builtins.open)`, `patch(socket.socket)` during evaluate | No calls raised |
| `sys.modules` check | No `requests`, `aiohttp`, Tuya, `psycopg2`, `asyncpg`, `yaml`, `sqlite3`, `httpx` |
| `git diff --stat HEAD` | Empty |

- No production service, cloud API, hardware or database was contacted.
- The interpreter is system Python, since `.venv/bin/python` doesn't resolve.
- The fuzz checks invariants, not safety correctness; it did not find R-01 to R-03.

## 13. Remaining risks and limitations

- **OR-1 (open, unverified).** `app/ml/ml_data_collector.py:585` says `output_power` is `None` in Line Mode. The evaluator would correctly `BLOCK`, but if production data confirms this, **`CONSIDER` is never reachable in practice**, despite section 11. Verify on stored telemetry before Phase 2.
- **OR-2 (strengthened).** The plan documents about 1800 s grid-mode cadence. History must now have gaps at or below 300 s. A 30-min cadence probe returned `STALE`/`BLOCK`, and a single old gap also blocks until the caller trims it. Replay of grid-mode periods can't reach `CONSIDER`, unless a higher-resolution source is used.
- **Caller contract undocumented:** required history length, no snapshot inside history, and trimming of old gaps.
- Plan Gate 1's "restart-safe read of baseline YAML" is still not implemented. Treated as outside Phase 1A, as in the first review.
- Numeric parameters remain unverified illustrative values (design §6).
- Reference documents were not re-read in this turn; `git diff HEAD` confirms they are unchanged from the earlier reading in this session.

## 14. Required corrections before approval

1. **R-01:** detect completed short trials regardless of settling (closes F-02 and F-06).
2. **F-10:** stop lockouts ending before expiry; extend history horizon; make escalation hold; honour configured lockout.
3. **R-02:** restore the `productive_pv_w` gate.
4. **R-03:** restrict sag to Invert segments.
5. **F-07:** compare load across each compared pair; handle the one-sample settling step.
6. **F-09:** handle sub-limit discharge conflicts; validate history; separate charge limit; system-appropriate voltage bound (operator input needed).
7. **F-12:** define `HOLD`/`BLOCK`/`CONSIDER` per mode; correct sun-time and sag attribution.
8. **F-13:** add tests below, and restore determinism and no-I/O tests.

**Required regression tests:**

- Original 10:01/03/05 scenario at 10:06, 10:07, 10:54, 11:04, 11:05, plus the cadence table in R-01.
- Recovery to `CONSIDER` with continuous Line samples after expiry.
- Churn at each poll to expiry; two failures with an escalated expiry; lockout above 60 min configured.
- PV 260, 499, 500 W stable. Line Mode voltage drift.
- Load ramp; one-sample settling step.
- 99 A, 0 W with 90 A; invalid history values.
- Every `BLOCK` has a specific reason.
- Determinism repeat; no-I/O check; default forecast boundary at 180/181 min.
- DST fold and spring change; non-UTC-adjacent sun times.

**Should be corrected in the same pass (MINOR, not approval blockers on their own):** R-04, R-05, F-11 (`valid_at` horizon), F-15, F-16, F-17, F-20.

## 15. Final recommendation

**Do not approve Phase 1A. Do not begin Phase 2 replay or any wiring.** Fix R-01 first, since it reopens the central safety case. Then fix F-10, R-02 and R-03.

There's no current hardware risk: the module stays passive, unwired and uncommitted. The `evaluated_at` contract, coverage model, UTC handling and shadow-only enforcement are sound and should stay. The remaining work is confined to sag, lockout and PV-classification logic and tests. A focused re-review of those areas should be enough afterwards.
