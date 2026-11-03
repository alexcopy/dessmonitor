# Independent Review — Adaptive Output Control, Phase 1A

| | |
|---|---|
| **Review date** | 2026-11-02 |
| **Reviewer role** | Independent senior software architect / safety-critical control systems reviewer (did not implement this code) |
| **Repository** | `alexcopy/dessmonitor` |
| **Branch** | `feat/adaptive-output-control` |
| **Reviewed HEAD** | `b19cd7a1c5cd71d8c56676f5ed2aeba286c13791` |
| **Reviewed state** | HEAD plus untracked working-tree files (the implementation and tests are not committed) |

## VERDICT: CHANGES_REQUIRED

The evaluator is passive: it has no side effects and is deterministic. It does **not** yet produce safe recommendations. All 12 existing tests pass, but probes reproduced `CONSIDER` for:

- telemetry 400 days old;
- 5,000 W of measured load with 600 W of PV;
- 49 minutes after a failed battery trial, inside its 60-minute lockout;
- two samples taken one second apart, with no evidence of mains dwell.

**Totals: 4 BLOCKER, 9 MAJOR, 7 MINOR.**

| Area | Result |
|---|---|
| Architecture compliance | PARTIAL |
| Determinism and time | FAIL (deterministic: yes; freshness: no) |
| Load and solar model | FAIL |
| Battery safety | FAIL |
| Anti-flapping | PARTIAL |
| Baseline integrity | PASS (one observation under F-05) |
| Side-effect safety | PASS |

---

## 1. Scope and method

### Files reviewed in full

- `app/control/adaptive_output_policy.py` (768 lines, untracked)
- `tests/test_adaptive_output_policy.py` (196 lines, untracked)
- `.project-memory/ADAPTIVE_OUTPUT_CONTROL_DESIGN.md` (architectural reference)
- `.project-memory/ADAPTIVE_OUTPUT_CONTROL_IMPLEMENTATION_PLAN.md` (architectural reference)

### Files inspected in part (data path and scope checks only)

- `app/control/__init__.py`: import list
- `app/ml/ml_data_collector.py:545-548, 585-593`: output power in Line Mode
- `app/api.py:40, 58-65, 170`: output field and working-mode names

### Evidence classes used in this report

- **VERIFIED DEFECT:** reproduced by running the actual code. Output is quoted.
- **DOCUMENT REQUIREMENT:** the design or plan requires it (section cited).
- **RECOMMENDATION:** the reviewer's own technical view, not approved architecture.
- **OPEN RISK:** evidence is suggestive but not proven. Not reported as a defect.

### Limitations

- "Phase 1A" does not appear in either architecture document. Its scope comes from the review request. The documents define only "Phase 1". The plan's Gate 1 includes "restart-safe read of baseline YAML", and no YAML parser exists here. This review treats the parser as out of 1A scope, so **Gate 1 as written is not complete**.
- No production data was accessed. Real telemetry behaviour (OR-1 and OR-2 below) is unverified.
- `.venv/bin/python` does not resolve on this machine, so everything ran on system Python 3.14.7. The project's intended interpreter version was not exercised.

---

## 2. Tests actually executed

| Check | Command or method | Result |
|---|---|---|
| Existing unit tests | `python3 -m unittest tests.test_adaptive_output_policy -v` (bytecode writing disabled) | **12 run, 12 passed** |
| Syntax | In-memory `compile()` | OK |
| Behaviour probes | Scratchpad scripts outside the repository, about 50 scenarios | Results quoted in the findings |
| Parameter usage | `grep` for `baseline.<param>` and field reads | See F-06, F-07, F-09, F-11 |
| Import side effects | `sys.modules` inspection after import | No `requests`, `aiohttp`, Tuya, `yaml` or DB driver loaded |
| Tracked-file changes | `git diff --stat HEAD` | Empty |

No live DESS, Tuya, cloud, database or hardware action was performed.

### Reproduction fixtures

Reproductions use the same defaults as the helpers in the test file:

- **`sample(at, ...)`:** Line Mode, output 500 W, PV 800 W, 26.0 V, discharge 2 A, sunrise 06:00, sunset 20:00 UTC on 2026-11-02, forecast 5 minutes old.
- **`baseline()`:** exit 24.5 V, enter 25.5 V, hysteresis 0.5 V, `forecast_max_age_seconds=600`.
- **Shorthand:** `L` is Line Mode and `I` is Invert Mode.

---

## 3. Findings summary

| ID | Severity | Summary | Location |
|---|---|---|---|
| F-01 | **BLOCKER** | No evaluation reference time; arbitrarily old data is `FRESH` | `:259-270, :420-427, :519-526` |
| F-02 | **BLOCKER** | Failed-trial lockout vanishes on the second Line sample | `:633-647, :650-665, :730-742` |
| F-03 | **BLOCKER** | High measured load alone does not block `CONSIDER` | `:307-313, :348` |
| F-04 | **BLOCKER** | Missing history satisfies dwell and battery-risk checks | `:549-564, :499, :73` |
| F-05 | MAJOR | Invert Mode returns `HOLD` before any safety check; exit threshold never evaluated | `:334-336, :540-548` |
| F-06 | MAJOR | Sag false negatives | `:662-683, :696-699` |
| F-07 | MAJOR | Sag false positives: no settling window, intermediate load ignored | `:668-683, :165` |
| F-08 | MAJOR | Invert segments bridge telemetry gaps, fabricating dwell | `:633-647, :538-548` |
| F-09 | MAJOR | Battery current never evaluated; implausible values accepted | `:444-460, :375-381` |
| F-10 | MAJOR | Anti-flap has no timed lockout, is hidden by unknown modes, never escalates | `:543, :559, :610-630, :730-742` |
| F-11 | MAJOR | Forecast validity unchecked; default age is 180 s where the design says 180 min | `:151, :519-526` |
| F-12 | MAJOR | Blocks without a blocker reason; misleading `SUNRISE_OPPORTUNITY`; decisions undefined | `:298-305, :334-354` |
| F-13 | MAJOR | Test suite misses the failures above and masks one default | `tests/test_adaptive_output_policy.py` |
| F-14 | MINOR | Same-timezone datetime arithmetic is wall-clock across DST | `:423, :439, :479-480, :741` |
| F-15 | MINOR | Sunrise/sunset not validated as the snapshot's day | `:470-472` |
| F-16 | MINOR | Weak input and baseline validation | `:173-209, :375-396` |
| F-17 | MINOR | Solar phases not distinguished; slow afternoon decline is `STABLE` | `:36-42, :510-515` |
| F-18 | MINOR | Duplicate-timestamp sample silently dropped | `:406-409` |
| F-19 | MINOR | `failed_trial_duration_seconds` is set for healthy segments | `:537-539` |
| F-20 | MINOR | Plan and contract drift | `:16-21, :251-252, :101-115` |

---

## 4. BLOCKER findings

### F-01 — BLOCKER — No independent evaluation time; old data is reported `FRESH`

- **Type:** VERIFIED DEFECT. Violates design §5.1 ("timestamped snapshot with age/freshness"), plan Phase 1 data-quality requirement, and design acceptance criteria 1 and 6.
- **File/lines:** `app/control/adaptive_output_policy.py:259-270` (contract and docstring), `:420-427` (freshness), `:519-526` (forecast age)

**Actual behaviour.** The snapshot's own `observed_at` is used as "now". Three things are measured:

- `received_at - observed_at`, which is transport latency rather than age, and is skipped entirely when `received_at` is `None` (`:422`);
- gaps between supplied samples;
- forecast age relative to the snapshot.

None of these can detect a whole data set that is old but internally consistent. The developer's claim that freshness is deterministic from `received_at` and history gaps is true of determinism but false of freshness.

**Reproduction.**

```text
snapshot and history dated 2025-09-28 (400 days old), received_at=None
→ CONSIDER dq=FRESH fc=FRESH batt=ELIGIBLE

snapshot and history from yesterday, received_at=observed_at+30s
→ CONSIDER dq=FRESH fc=FRESH batt=ELIGIBLE
```

So a snapshot from yesterday is treated as fresh when its history is also from yesterday. Stale PV and voltage can produce an optimistic result.

**Answers to the time questions in the review request:**

| # | Question | Answer |
|---|---|---|
| 1 | Fresh vs old-but-consistent data? | **Cannot be distinguished** |
| 2 | Can yesterday's snapshot be fresh? | **Yes**, verified |
| 3 | Is the reference time explicit? | **No** |
| 4 | Can stale PV/battery data be optimistic? | **Yes** |
| 5 | Timezone-aware enforced? | **Yes**, `:371` |
| 6 | Naive timestamps? | **Rejected** with `ValueError`, at construction |
| 7 | Out-of-order / future samples? | Sorted; a future history sample gives `STALE` (verified). There is no way to detect a future **snapshot** |
| 8 | Do gaps invalidate sag and transition logic? | Only above 900 s at the data-quality level; see F-08 |
| 9 | Midnight / DST? | See F-14, F-15 |

**Expected.** Age is measured against an independent evaluation time. Data older than the limit is `STALE`, and unprovable freshness is `UNKNOWN`. Neither can produce `CONSIDER`.

**Required correction (contract change, no wall clock in the evaluator).**

1. Add a required, timezone-aware parameter: `evaluate_output_policy(snapshot, baseline, history, evaluated_at)`.
2. The caller supplies it: runtime passes its clock, replay passes the historical replay instant. The evaluator stays pure, so `datetime.now()` must **not** be added to it.
3. Define `age = evaluated_at - observed_at`. Above `telemetry_max_age_seconds` is `STALE`. More than a small allowed clock skew in the future is `UNKNOWN`.
4. Use `evaluated_at` for forecast age, lockout expiry, and dwell.
5. Expose `evaluated_at` and data age in the decision (design acceptance criterion 1).
6. Keep `received_at` as a separate transport-latency check.

### F-02 — BLOCKER — Failed-trial lockout disappears on the second Line sample

- **Type:** VERIFIED DEFECT. Violates design §3 and §6 ("hold on mains longer", 30–60 min cooldown), plan acceptance scenario 2, YAML `failed_trial_lockout_min: 60`, and design acceptance criterion 3.
- **File/lines:** `:633-647`, `:650-665`, `:730-742`

**Actual behaviour.** In Line Mode, `_current_or_previous_invert_segment` looks only at the immediately preceding point (`end -= 1`; returns empty if it is not Invert, `:642`). The failed trial is therefore recognised only on the **first** Line sample. Every later sample loses `FAST_VOLTAGE_SAG`, `COOLDOWN` and `lockout_expires_at`. The 60-minute lockout is effectively never enforced, and only the 45-minute mains dwell remains.

**Reproduction (the 10:00–10:06 sag scenario from the review request):**

```text
L 26.0 | I@10:01 25.5 | I@10:03 25.0 | I@10:05 24.4, all at 2000 W

10:06 Line → BLOCK lockout=11:05 reasons include FAST_VOLTAGE_SAG, COOLDOWN   (correct)
10:07 Line → BLOCK lockout=None  reasons=[SHADOW_ONLY, SUNRISE_OPPORTUNITY]   (evidence lost)
10:54 Line → CONSIDER lockout=None batt=ELIGIBLE                              (inside the 60 min lockout)
```

The existing test passes only because it evaluates the first Line sample alone.

**Expected.** `BLOCK` with `COOLDOWN` and `lockout_expires_at=11:05` for every evaluation until 11:05.

**Required correction.** Search all supplied history for completed Invert segments, not just the one adjacent to the snapshot. Derive lockout from the most recent failed trial within the lockout horizon. Apply it in both modes where applicable. Test at each poll across the lockout, and at the expiry boundary. A lockout must not depend on the snapshot's position relative to the trial.

### F-03 — BLOCKER — High measured load alone does not prevent `CONSIDER`

- **Type:** VERIFIED DEFECT. Violates plan Phase 1 ("refuse ... when measured demand is high, PV is weak, history shows a failed short trial, or inputs are unreliable", which lists independent conditions), design §3 ("Higher current household demand ... should make returning to battery harder"), and design §9 ("modest output").
- **File/lines:** `:307-313`, `:348`

**Actual behaviour.** `HIGH_MEASURED_LOAD` needs `output >= 1500 W` **and** `pv <= 250 W`. Demand is otherwise not compared with anything. The evaluator never compares load with PV, and solar classification ignores load. So `output_power_w` is used correctly as the authoritative signal, but it influences the outcome only inside this narrow case.

**Reproduction.**

```text
output=2000 W, PV=600 W stable  → CONSIDER, no load reason
output=5000 W, PV=600 W stable  → CONSIDER, no load reason
output=2500 W, PV 251→401 W     → CONSIDER solar=IMPROVING, no load reason
```

The 2,000 W / 100 W scenario is blocked, and that is the only high-load scenario tested.

**Expected.** Measured demand above the limit blocks regardless of PV. Demand materially above PV counts as a negative signal, used as a rough warning and not as a power balance (design §5.2).

**Required correction.** Emit `HIGH_MEASURED_LOAD` from demand alone. Add a separate rough-warning signal for demand exceeding PV, named so it does not claim to be a measured battery or mains flow. Keep `PV_DEFICIT` independent of it. Test at 1,499 / 1,500 W with strong, moderate and weak PV.

### F-04 — BLOCKER — Absence of evidence satisfies dwell and battery-risk checks

- **Type:** VERIFIED DEFECT. Violates design §5.3 ("If data are insufficient, mark risk `UNKNOWN` and do not authorize a risky return to battery"), YAML `stable_condition_min: 10` and `unknown_risk_blocks_battery_return: true`, design acceptance criterion 6, and the restart rule in §4.
- **File/lines:** `:549-564` (dwell `None` falls through to `ELIGIBLE`), `:499` (sample count only, no duration), `:73` (reason defined, never emitted)

**Actual behaviour.**

- With no observed transition, `mains_dwell` is `None` and the branch returns `ELIGIBLE`, as if the dwell were complete. An empty `history` also gives `ELIGIBLE`.
- PV stability needs only 2 samples, with no minimum time span.
- No previous comparable battery trial is required.
- `INSUFFICIENT_HISTORY` is never emitted anywhere.

**Reproduction.**

```text
Two Line samples 2 min apart → CONSIDER batt=ELIGIBLE solar=STABLE
Two Line samples 1 s apart   → CONSIDER batt=ELIGIBLE solar=STABLE
```

Truncating history has the same effect. If a failed trial ended ten minutes earlier and a restart supplies only the last two Line samples, the result is also `CONSIDER`. The evaluator can't know what it wasn't given, and it doesn't check for that.

**Expected.** `BLOCK` with `INSUFFICIENT_HISTORY` until supplied history proves continuous coverage of the required horizon.

**Required correction.**

1. Require continuous evidence of Line Mode over `min_mains_dwell_seconds`, and history coverage reaching back across the longest lockout horizon.
2. Require a minimum stability span for PV, not only a sample count.
3. Emit `INSUFFICIENT_HISTORY` and classify the battery as `UNKNOWN` when any of these is unproven.
4. Document the coverage the caller must provide.

**Note:** the test `test_stable_productive_day_is_considered` encodes this defect, because it expects `CONSIDER` from 4 minutes of history. It must change with the fix.

---

## 5. MAJOR findings

### F-05 — MAJOR — Invert Mode `HOLD` short-circuits safety; exit threshold never evaluated

- **Type:** VERIFIED DEFECT. Violates plan Phase 1 ("apply separate return-to-battery and exit-battery reasoning") and design §3 (`V_exit_battery`).
- **File/lines:** `:334-336`, `:540-548`

**Actual behaviour.** `baseline.exit_battery_v` is read only for null checks, validity and echoing (`:319, :342, :363, :593-598`). It is never compared with measured voltage. The Invert `STABLE` branch runs first and returns `HOLD` before stale, missing-data and guard checks. `STABLE` is also the fall-through result when nothing is known.

**Reproduction.**

```text
Invert 60 min, slow decline to 24.2 V (exit=24.5) → HOLD batt=STABLE reasons include BATTERY_GUARD
Invert 60 min at 26.0 V, then 20.0 V with +1500 W → HOLD batt=STABLE drop=None
Invert with voltage, output and discharge all None → HOLD batt=STABLE reasons include UNKNOWN_TELEMETRY
```

**Expected.** Below exit voltage, the result is an explicit non-stable outcome (a battery-stress classification and exit-advisory reason), recorded as a shadow recommendation only. `STABLE` must require positive evidence: fresh data, voltage present, and an acceptable slope under comparable load.

**Required correction.** Evaluate exit-voltage breach independently, before the `STABLE` branch. Return `UNKNOWN` instead of `STABLE` when evidence is missing or not comparable. Define what the decision means in Invert Mode (see F-12).

### F-06 — MAJOR — Rapid-sag false negatives

- **Type:** VERIFIED DEFECT. Violates design §5.3 (robust slope over 5–15 minutes), plan Phase 1 ("several comparable Invert Mode samples"), and design §6.
- **File/lines:** `:662-665`, `:668-683`, `:696-699`; `voltage_sag_window_seconds` (`:161`) has **zero** reads in evaluation logic.

**Actual behaviour.** Sag compares only the first and last samples of the entire segment. Four failure modes follow:

1. The slope is diluted by long segments.
2. A load difference above 250 W between the endpoints disables detection (`None`), even though intermediate comparable samples exist.
3. A one-sample trial is never evaluated.
4. A collapse lasting over 10 minutes gets no lockout.

**Reproduction.**

```text
(a) 2 h Invert at 26.0 V, then 25.0 V in the last 5 min
    → HOLD batt=STABLE drop=1.0, no FAST_VOLTAGE_SAG (slope 0.008 V/min < 0.02)

(b) I@9:52 25.9 V 1000 W | I@9:54 25.0 V 1800 W | I@9:56 24.0 V 2000 W | L@9:57
    → no FAST_VOLTAGE_SAG, no lockout (1 V in 2 min at comparable load missed)

(c) L 26.0 | I 24.0 (one sample) | L 25.8
    → no FAST_VOLTAGE_SAG, no lockout

(d) 11-min collapse 26.0→24.0 V | L
    → FAST_VOLTAGE_SAG reported, lockout=None (duration > failed_trial_max_seconds)
```

Case (c) matters in practice because the plan documents a roughly 120 s collector cadence.

**Expected.** All four are recognised as failed or stressed battery operation, with cooldown where applicable.

**Required correction.** Evaluate sag over a sliding `voltage_sag_window_seconds` window across runs of comparable-load samples, using a robust slope. Handle a one-sample trial as a failed short trial using duration and pre-transition evidence. Decouple lockout from segment length, or define long-collapse handling explicitly.

### F-07 — MAJOR — Rapid-sag false positives

- **Type:** VERIFIED DEFECT. Violates design §5.3 ("omitting the immediate settling period after mode changes and discontinuities caused by large load steps").
- **File/lines:** `:668-683`; `post_transition_settle_seconds` (`:165`) has **zero** reads in evaluation logic.

**Actual behaviour.** All samples from the start of the segment are used, and only the endpoints are compared for load comparability.

**Reproduction.**

```text
(a) L | I@9:51 26.0 V | I@9:52 25.6 V (one minute after transition, constant load)
    → FAST_VOLTAGE_SAG, INELIGIBLE

(b) I 26.0 V 500 W | I 25.5 V 2500 W | I 25.6 V 500 W
    → FAST_VOLTAGE_SAG (endpoints "comparable"; the middle 2,000 W step is ignored)
```

**Expected.** Settling samples are excluded, and load discontinuities split the comparable run. Insufficient comparable data gives `UNKNOWN`, not a confirmed sag.

**Required correction.** Apply `post_transition_settle_seconds`, and split runs where adjacent samples differ by more than `comparable_load_tolerance_w`. In a failure-averse design, the outcome for case (a) may still be a block, but it should say "insufficient/settling", not record a failed trial.

### F-08 — MAJOR — Telemetry gaps are bridged inside Invert segments

- **Type:** VERIFIED DEFECT. Violates plan Phase 2 ("Preserve gaps; do not fabricate") and design §5.3 ("fresh samples in the same stable Invert Mode segment"; "telemetry sampling gap").
- **File/lines:** `:633-647`, `:538-548`

**Actual behaviour.** Segments join adjacent Invert samples regardless of the time between them. A gap at or below 900 s passes the data-quality check and still counts as continuous dwell. Mode changes inside the gap are unobservable.

**Reproduction.**

```text
I@9:20 | I@9:35 | I@9:50 (15-min gaps) → dq=FRESH batt=STABLE HOLD (30 min "observed" from 3 samples)
I@07:00 | I@10:00 (3 h gap)            → dq=STALE batt=STABLE HOLD
```

**Expected.** A long gap ends a segment for dwell, sag and failed-trial purposes. A dedicated, shorter continuity limit is appropriate. `STABLE` and `dq=STALE` should not be returned together.

**Required correction.** Split segments on gaps above a continuity limit. Apply this to `_mode_change_count` and `_last_transition_at` as well. Report a gap as uncertainty.

### F-09 — MAJOR — Battery current is never evaluated; implausible values accepted

- **Type:** VERIFIED DEFECT (battery_current_dis unused). Window B and design §5.2 ("Prefer observed battery discharge current") are documented; the plausibility check is a RECOMMENDATION aligned with `FAULT_HOLD` ("stale/conflicting data").
- **File/lines:** `:454` (presence only), `:277` (display only), `:375-381`

**Actual behaviour.** `battery_current_dis` is checked for presence and displayed, nothing more. `battery_current_chg` is validated and never used. Conflicting electrical values pass silently.

**Reproduction.**

```text
discharge=500 A                     → CONSIDER (identical except for evidence text to discharge=2 A)
output=0 W, discharge=300 A         → CONSIDER
output=-5000 W                      → CONSIDER
battery_voltage=9999 V              → CONSIDER
```

**Expected.** Measured discharge affects eligibility, conflicting signals are classified as `UNKNOWN`, and physically impossible readings don't reach `CONSIDER`.

**Required correction.** Use discharge current as a Window B signal, with a parameter marked as algorithmic rather than hardware fact. Reject negative or impossible values, or classify them `UNKNOWN`. Do not combine DC and AC quantities into a power balance without a validated electrical model (design open questions in §11).

### F-10 — MAJOR — Anti-flap has no timed lockout, can be hidden, and never escalates

- **Type:** VERIFIED DEFECT. Violates design §6: `CHURN_LOCKOUT` ("require stronger conditions and a timed quiet period") and FAST_VOLTAGE_SAG ("raise the required confidence").
- **File/lines:** `:543`, `:559`, `:610-630`, `:730-742`

**Actual behaviour.** Excessive changes block only while the sliding window contains at least 4 transitions. No churn lockout expiry exists, `lockout_expires_at` stays `None`, and `COOLDOWN` is absent. An interleaved unknown mode resets the comparison, so real transitions go uncounted. Repeated failed trials have identical cost.

**Reproduction.**

```text
(a) L→I→L→I→L ending 09:16, Line polled every 2 min
    → first CONSIDER at 10:02; lockout_expires_at never set
(b) L, Fault, I, Fault, L, Fault, I, Fault, L → changes=0, CONSIDER
(c) Three failed trials at 70-min spacing → lockout 60 min each time; no increase
```

**What passes:**

- Transitions come from observed `working_mode` only, not SOC.
- Unknown modes fabricate no transitions.
- The evaluator never claims a transition was commanded, or attributes one to a cause.
- The first-sample failed-trial expiry is correct (11:05 = last Invert sample + 60 min).
- A rebound cannot bypass it on that first sample. The bypass is via F-02.

**Expected.** A deterministic timed churn lockout with an explicit expiry, tolerance for interleaved unknown samples, and raised conservatism after repeat failures.

**Required correction.** Derive churn expiry from the observed transition times. Count transitions between known modes across unknown samples without inventing them (or treat high unknown density as `UNKNOWN`). Implement bounded escalation. Exact numbers need operator or simulation review, as design §6 calls them "illustrative".

### F-11 — MAJOR — Forecast validity unchecked; default age has a unit mismatch

- **Type:** VERIFIED DEFECT. Violates design §3 ("Timestamped hourly forecast"; "Missing/stale forecast disables aggressive adaptation") and YAML `forecast_max_age_min: 180`.
- **File/lines:** `:151`, `:519-526`

**Actual behaviour.**

- The default is `forecast_max_age_seconds = 180.0` (3 minutes), while the design value is 180 **minutes**. `OpenWeatherService` refreshes about every 600 s (per the plan), so most forecasts would be stale at the default. This fails safe but disables `CONSIDER` for most of each cycle. The test helper overrides it to 600 s, masking this.
- `valid_at` is never read after validation. Neither are clouds, rain, probability or irradiance.
- A forecast with an issue time and no contents counts as `FRESH`.

**Reproduction.**

```text
valid_at = yesterday                         → fc=FRESH, CONSIDER
ForecastObservation(observed_at=...) only    → fc=FRESH, CONSIDER
clouds=100, rain=50 mm, prob=100, irradiance=0 → fc=FRESH, CONSIDER
Default baseline with 5-min-old forecast     → fc=STALE, BLOCK
```

**Expected.** A forecast with a past or irrelevant validity time, or no usable content, is not `FRESH`.

**Required correction.** Correct the default, or explicitly record approval of a different value. Check `valid_at` against the evaluation time. Require at least one usable forecast value. Decide explicitly how forecast content applies; it may only make the outcome more conservative, since "a good forecast never overrides measured battery stress" (design §3). Content-based blocking itself is a RECOMMENDATION.

### F-12 — MAJOR — Decisions lack blocker reasons, and semantics are undefined

- **Type:** VERIFIED DEFECT. Violates plan Phase 1 ("stable reason codes ... and evidence") and design acceptance criterion 1.
- **File/lines:** `:298-305`, `:334-354`

**Actual behaviour.**

```text
Mains dwell unsatisfied   → BLOCK reasons=[SHADOW_ONLY, SUNRISE_OPPORTUNITY]
Healthy Invert trial      → BLOCK batt=TRIAL reasons=[SHADOW_ONLY, SUNRISE_OPPORTUNITY]
Night (22:00)             → BLOCK reasons=[SHADOW_ONLY]; no NIGHT reason
```

- `SUNRISE_OPPORTUNITY` is emitted for every `STABLE` or `IMPROVING` classification at any time of day (including 16:00), and alongside `BLOCK`, which reads as contradictory.
- `UNKNOWN_BATTERY_RISK` is emitted for an unknown **solar** classification (`:303`), which is the wrong domain.
- `BLOCK` in Invert Mode has no defined meaning, since there is nothing left to return to.
- `CONSIDER` is never defined in the code. In practice it means "no blocker detected".
- `HOLD` means both "maintain Invert" and is returned with `STALE` or missing data.

**Expected.** Every non-`CONSIDER` outcome has at least one specific blocker reason, and the operator can tell the decisions apart without reading the code.

**Required correction.** Document each decision's precise meaning, with `CONSIDER` stated as "candidate, not safe-to-switch and not a command". Add reasons for mains dwell, in-progress trial and night. Only emit `SUNRISE_OPPORTUNITY` for the early-morning phase, and add a neutral code for productive solar. Add a test asserting every `BLOCK` has a specific reason.

### F-13 — MAJOR — Test coverage is materially insufficient

- **Type:** VERIFIED. Plan Gate 1 requires tests demonstrating "valid null/freshness handling".
- **File/lines:** `tests/test_adaptive_output_policy.py`, especially `:68` (masking override), `:84-94`, `:106-119`, `:181-188`

The tests pass while F-01 to F-12 are present. Specifically:

- `:106-119` evaluates only the first Line sample, hiding F-02.
- `:84-94` encodes F-04 as desired behaviour.
- `:68` hides the default in F-11.
- `:181-188` patches `open` and `socket.socket` only during the call. It proves little beyond the stdlib-only import check (performed separately in this review).

The tests that assert behaviour for SOC, baseline nulls and immutability are sound. See section 12 for the scenario map and tests to add.

---

## 6. MINOR findings

Each of these is verified.

- **F-14 — DST arithmetic (`:423, :439, :479-480, :741`).** Python ignores offsets when subtracting datetimes sharing a `tzinfo`. With `Europe/London` at 25 Oct 01:10 BST and 01:50 GMT, the real gap is 6,000 s but the code computes 2,400 s. With a 3,000 s gap limit it returned `FRESH`. On 29 Mar, 00:30 to 18:00 computes as 17.5 h, while the real value is 16.5 h. Daytime logic is unaffected in the usual case, because the clock change is at night, and mixed-offset UTC input is correct. **Correction:** convert to UTC on construction.
- **F-15 — Sun times not tied to the snapshot's day (`:470-472`).** A sunrise today and a sunset tomorrow gives `STABLE`/`CONSIDER` at 23:00 given PV input. Yesterday's sun times give a conservative `NIGHT`. Today's sunset with tomorrow's sunrise gives a conservative `UNKNOWN`. **Correction:** bound the day length and require the interval to apply to the snapshot.
- **F-16 — Input validation (`:173-209, :375-396`).** Negative baseline values are accepted (`telemetry_max_age_seconds=-5`), and `allow_zero=True` makes that argument ineffective at every call site. A non-string `working_mode` raises `AttributeError` during evaluation instead of validation.
- **F-17 — Solar phases (`:36-42, :510-515`).** The design §3 phases (pre-sunrise, early morning, productive day, late-day, night) are not represented. 830→700 W at 16:00 returns `STABLE` and `CONSIDER`. Two samples 300→500 W, one minute after sunrise, give `IMPROVING` and `CONSIDER`; no `sunrise_offset_min`. Late-day handling does use actual sunset rather than fixed hours, so that part passes. Larger drops correctly give `WEAK`.
- **F-18 — Same-timestamp samples (`:406-409`).** A history sample with the snapshot's timestamp and a conflicting mode is silently dropped and not reported as `UNKNOWN`.
- **F-19 — Misleading field (`:537-539`).** `failed_trial_duration_seconds` is populated for healthy segments.
- **F-20 — Plan drift.**
  - The plan names `HOLD`/`ADVISE`/`BLOCK`; the code uses `CONSIDER`.
  - The plan's raw `output_priority` input is absent.
  - `effective_*` fields are always `None`, which is safe but gives no bounded-proposal model.
  - `command_executed=True` and `shadow_only=False` can be constructed directly (the evaluator never sets them). **Recommendation:** reject them in `__post_init__`.

---

## 7. Assessment by area

### Architecture compliance — PARTIAL

**Pass:** pure function, immutable models, separated Window A and B assessments, null baseline handling, SOC exclusion, no commands.

**Fail:** Window B doesn't enforce the documented behaviour (F-02 to F-08). Gate 1 "valid freshness handling" is unmet (F-01).

### Determinism and telemetry freshness — FAIL

**Determinism — pass.** Identical inputs gave equal decisions and hashes. Models are frozen, no global state, no clock, no environment or randomness. `command_executed` is always `False` from the evaluator.

**Freshness — fail.** See F-01. Aware timestamps are enforced, and naive ones rejected.

### Inverter-measured household load — FAIL

**Pass:**

- `output_power_w` is the only demand input.
- Tuya totals are not an input at all.
- `None` stays unknown (blocks, never zero).
- The evidence label correctly says "measured inverter output power".
- There is no mains-consumption mislabel.
- No `PV − output` battery calculation, and no AC/DC combination.

**Fail:** high load is only considered with weak PV (F-03), and contradictory measurements are unflagged (F-09).

### Solar opportunity and remaining daylight — PARTIAL

**Pass:** actual sunrise/sunset, not fixed clock hours, so a shorter winter day shifts the 2-hour reserve automatically; night and late-day block; PV trend from timestamps; missing forecast blocks.

**Fail:** forecast validity (F-11), phase distinction (F-17), date sanity (F-15).

### Battery voltage sag detection — FAIL

The reference scenario is detected only on the first Line sample. See F-02, F-06, F-07, F-08. Rebound is correctly not treated as recharge on that sample. **No code path lowers a protective boundary after a failed trial**, so that specific requirement passes. There is no time-to-guard estimate (design §5.3 "with sufficient quality"); this is noted, not raised as a defect.

### Anti-flapping and cooldown — PARTIAL

See F-10 and F-02. Detection from observed modes works for the reference pattern. Cooldown semantics do not hold beyond the first sample.

### YAML baseline integrity — PASS

- `exit_battery_v`, `enter_battery_v` and `min_hysteresis_v` default to `None`. Unknown values block and are reported via `UNKNOWN_OUTPUT_CAPABILITY`.
- The baseline is frozen and is never written.
- The decision echoes baseline values unchanged.
- No voltage limit is hardcoded. Numeric defaults are all documented as algorithmic parameters at `:138-144`, with no 25.5/26.5 V values in the module.
- Weak PV or a collapse can't relax any boundary.
- No hardware write capability is presumed.

**Default-value categories:** all are **generic algorithm parameters**, and none are hardware claims. Those with a YAML source match, apart from `forecast_max_age_seconds` (F-11) and windows at 900 s where the design has 10 min (`pv_stability_window_min`, `voltage_trend_window_min`). Parser and YAML loading are not present in this phase (see limitations).

### SOC — PASS

An AST and grep check found `battery_soc` is read only by validation (`:131`). The test varying 70% and 100% produced identical decisions. It cannot authorise, establish recharge, define a transition, or bypass anything.

### Side-effect and hardware safety — PASS

- Module imports are only `__future__`, `dataclasses`, `datetime`, `enum`, `math`.
- Only the new test imports the module, so it isn't wired into any runtime.
- Importing through the `app.control` package also loads its 18 sibling modules. No network, Tuya, YAML or DB driver modules were loaded.
- `git diff HEAD` is empty for tracked files: `run.py`, the controllers, Tuya and DESS code, `init-db.sql`, the Controls UI and both architecture documents are unchanged since HEAD.
- `devices.yaml` is git-ignored, so git can't confirm it. Its modification time is 9 July 2026, predating this work.

---

## 8. Open risks (not established defects)

- **OR-1 — `output_power_w` may be null in Line Mode.** A comment at `app/ml/ml_data_collector.py:585` says "In Line Mode output_power is None", and the code at `:545-548` falls back to apparent power. The evaluator treats null as critical (verified: `BLOCK` with `UNKNOWN_TELEMETRY`, which is the right fail-safe). If the comment is true of production data, **`CONSIDER` can never occur in Line Mode, and the evaluator could never recommend anything.** Check this against stored `inverter_metrics` before Phase 2 replay. The source of any replacement load value needs explicit approval, and it should not be silently substituted.
- **OR-2 — Replay cadence.** The plan says the collector samples at about 1,800 s in grid mode, above the 900 s `max_telemetry_gap_seconds` default. Replay of grid-mode periods may therefore always be `STALE`. Because gap checks cover all supplied history, a single old gap also keeps the result `STALE` until the caller trims it. This is safe, but could make replay uninformative.

---

## 9. Required corrections before approval

1. **F-01:** add a required `evaluated_at` input; measure age against it. No `datetime.now()` in the evaluator.
2. **F-02:** make failed-trial lockout persist across all samples until expiry.
3. **F-03:** make high measured demand block independently of PV.
4. **F-04:** missing dwell, stability or history coverage yields `INSUFFICIENT_HISTORY`, not eligibility.
5. **F-05:** evaluate exit voltage; `STABLE` requires positive evidence.
6. **F-06, F-07, F-08:** rework sag and segments: windowed slope, settling exclusion, comparable-load runs, gap splitting, short trials.
7. **F-09:** evaluate discharge current; handle impossible and conflicting values.
8. **F-10:** timed churn lockout, unknown-mode robustness, repeat-failure escalation.
9. **F-11:** fix forecast default and validity.
10. **F-12:** define decisions, and give every block a specific reason.
11. **F-13:** add the tests in section 12, and correct `test_stable_productive_day_is_considered`.

## 10. Optional recommendations (not required for approval; not approved architecture)

- Fix the MINOR findings F-14 to F-20 in the same pass, particularly UTC normalisation (F-14), since it's small and prevents a class of errors.
- Add a property-based test: for any input, `command_executed` is `False`, and non-`FRESH` data never yields `CONSIDER`.
- Add a time-to-guard risk indicator (design §5.3) once slope quality is reliable.
- Resolve OR-1 and OR-2 with a read-only sample of stored telemetry before Phase 2.

## 11. Final recommendation

**Do not approve Phase 1A. Do not start Phase 2 replay or any wiring until F-01 to F-04 are fixed and independently re-verified.** Replay results from the current evaluator would be misleading, since they would overstate safe `CONSIDER` opportunities and understate failed-trial protection.

There is no current hardware risk: the module is passive, unwired and uncommitted. The pure, immutable structure is sound and should be kept. The corrections are to decision logic and one contract parameter, not a redesign. After the fixes, re-review the changed logic and the expanded tests.

---

## 12. Test scenario coverage

| # | Scenario | Existing test? | Probe result | Status |
|---|---|---|---|---|
| 1 | High load with weak PV | Yes | Correct | Covered |
| 2 | Productive midday | Yes | `CONSIDER` on insufficient evidence | Wrong expectation (F-04) |
| 3 | Weak PV near sunset | Yes | Correct | Covered |
| 4 | 5-min voltage collapse | First sample only | Lost on 2nd sample | **Gap (F-02)** |
| 5 | Repeated oscillation | Yes | Detected, no timed lockout | **Partial (F-10)** |
| 6 | Rebound after mains return | First sample only | Correct on that sample | Partial |
| 7 | SOC jumps | Yes | Correct | Covered |
| 8 | Missing hardware baselines | Yes | Correct | Covered |
| 9 | Stale telemetry | Partly | Old data passes | **Gap (F-01)** |
| 10 | Gaps and out-of-order history | Large gap only | Sorting OK; small gaps bridged | **Gap (F-08)** |
| 11 | Timezone / daylight boundaries | Naive rejection only | Wall-clock errors | **Gap (F-14, F-15)** |
| 12 | Unknown working modes | No | Blocks; hides flapping | **Gap (F-10)** |
| 13 | Missing / inconsistent current or power | Null only, indirectly | Inconsistent accepted | **Gap (F-09)** |
| 14 | Determinism | Yes | Correct | Covered |
| 15 | No I/O or commands | Weak | Verified by import check | Weak |
| 16 | Recovery after lockout expiry | No | Not reachable as designed | **Gap (F-02)** |
| 17 | Old snapshot with recent-looking history | No | `CONSIDER` | **Gap (F-01)** |
| 18 | Load change between voltage samples | No | False negative and positive | **Gap (F-06, F-07)** |

### Tests to add

- **Freshness:** yesterday's and 400-day-old data with consistent history; missing `received_at`; future snapshot; exact age boundary.
- **Lockout:** evaluate at every poll from 10:06 through 11:05 and after, for a failed trial; confirm block until expiry and recovery after.
- **Load:** 1,499 / 1,500 W and 5,000 W against weak, moderate and strong PV.
- **Evidence:** empty history; 1-second-apart samples; dwell unproven; history truncated to hide a trial.
- **Exit voltage:** Invert below exit; `None` voltage must not be `STABLE`.
- **Sag:** late collapse in a long segment; growing load; one-sample trial; 11-minute collapse; settling step; middle load spike.
- **Gaps:** bridged Invert segment; gaps exactly at limit.
- **Current:** high discharge; output 0 with discharge 300 A; negative output.
- **Anti-flap:** interleaved unknown modes; churn expiry; repeated failed trials; transition at window boundary.
- **Forecast:** past `valid_at`; empty forecast; future `observed_at`; default-baseline age; 180-minute boundary.
- **Time:** both DST changes with a shared `ZoneInfo`; mismatched-day sunset; near-midnight.
- **Reasons:** every `BLOCK` has a specific reason; `SUNRISE_OPPORTUNITY` absent at midday/afternoon.
