# Adaptive Output Control, Phase 1A: M-01 Acceptance

## 1. Review details

| Item | Value |
|---|---|
| Review date | 2026-11-03 |
| Branch | `feat/adaptive-output-control` |
| HEAD | `b19cd7a1c5cd71d8c56676f5ed2aeba286c13791` (both source files untracked) |
| Python | 3.14.7 (system `python3`) |
| Evaluator SHA-256 | `e671810fe66649032039c8cfb290d1e6431b077e7620199d233d6de50dbb8d25`, matches expected (1282 lines) |
| Tests SHA-256 | `fc63e662a2355a975307a41555e3ca9f7cca085622747c6836a9ebebf152420c`, matches expected (1134 lines) |
| Previous review read | `.project-memory/reviews/ADAPTIVE_OUTPUT_PHASE1A_C01_ACCEPTANCE.md` (CHANGES_REQUIRED on M-01 only) |

**What changed since the C-01-accepted version.** I rebuilt the previous evaluator by reversing only the M-01 model edits. The rebuilt file hashes to `e7902b1c620b6ec9657db6e49567a76dabd4d27dadd4e4c2307ec18d9e10c347`, the version the C-01 review accepted. So the current evaluator is that version plus the M-01 changes and nothing else; no other production logic changed.

The M-01 changes are:

- validators added to `Evidence`, `SolarOpportunityAssessment`, `BatteryEligibilityAssessment` and `OutputPolicyDecision.__post_init__` (`:268-375`)
- five new helpers (`:582-611`)

## 2. Verdict: APPROVED

- **M-01** is corrected for every public constructor path in the decision graph.
- **C-01** current safety is unchanged and still valid.
- **No regression** was found.
- The evaluator remains pure, deterministic and shadow-only.

The remaining observations in section 9 are informational. None allows a public constructor to retain caller-owned mutable state.

## 3. Constructor and nested-model findings

| Check | Result | Source |
|---|---|---|
| A. Caller collections become tuples | PASS. `reason_codes` and `supporting_evidence` accept only exact `tuple` or `list`, and are copied into tuples. Subclasses, dicts, sets, generators and bare strings are rejected | `:364-373` |
| B. Changing caller collections after construction | PASS. Appending to, clearing or reversing the caller's lists leaves the decision equal to the original. `dataclasses.replace(..., reason_codes=[...])` is also normalized | `:372-373` |
| C. Nested assessments | PASS. They must be the exact assessment classes (subclasses rejected). Every field is an exact enum, a finite built-in `int` or `float` (or `None`), or a non-negative exact `int` | `:282-310`, `:338-341` |
| D. Wrong runtime types | PASS. Every field in the decision graph is type-checked; nothing wrongly typed is silently kept | `:336-371` |
| E. Enums and reason codes | PASS. Enum fields require the exact enum type. Reason codes accept an `OutputPolicyReason` or an exact `str` that names one; unknown names raise `ValueError` | `:582-584`, `:606-611` |
| F. Evidence | PASS. Items must be exact `Evidence`, whose `name` and `value` must be exact `str` | `:268-270`, `:370` |
| G. Numbers | PASS. `bool`, `int` and `float` subclasses, custom number objects and strings are rejected; NaN and ±inf raise `ValueError`. Negative `data_age_seconds` is still allowed for future telemetry | `:587-595` |
| H. Datetimes | PASS. Exact `datetime` only (subclasses, `date`, strings, numbers and `None` for `evaluated_at` rejected). Naive values and a tzinfo with no offset are rejected | `:598-603` |
| I. UTC normalization | PASS. All three timestamps are converted to `timezone.utc` at construction, so the caller's tzinfo object isn't kept | `:343-349` |
| J. `command_executed=True` | PASS. Raises `ValueError`. `shadow_only=False` is also rejected, and both flags must be exact `bool` | `:359-375` |
| K. Nested references | PASS. All four classes are frozen; changing any attribute raises `FrozenInstanceError`, and tuples have no `append` | |
| L. Real evaluator decisions | PASS. BLOCK, HOLD and CONSIDER decisions rebuild through the public constructor and compare equal | |

## 4. Independent mutation probes

Scripts are in `/private/tmp/claude-501/-Users-alexred-Projects-Python-dessmonitor/89f11ab5-3de5-4bf6-8c85-4f884d2e0cde/scratchpad/` (`m01probe.py`, `m01b.py`). The output is in `m01.out` there. The inputs were chosen independently of the unit tests.

**Rejected inputs: 61 of 61, with 0 unexpected acceptances.**

| Area | Inputs tried | Result |
|---|---|---|
| `reason_codes` | dict, set, generator, list subclass, tuple subclass, nested list item, `str` subclass item, unknown name `"EXECUTE_SWITCH"`, other enum (`OutputDecision.CONSIDER`), bare string | All rejected |
| `supporting_evidence` | dict, nested dict item, `Evidence` subclass item, tuple item | All rejected |
| `Evidence` fields | list value, dict name, `str` subclass, bytes | All rejected |
| `decision`, `data_quality` | plain strings, an enum from another class | All rejected |
| Nested assessment slots | dict, assessment subclass, a battery assessment in the solar slot, a list | All rejected |
| Assessment fields | wrong enum class or string classification; `TelemetryQuality` used as `forecast_quality`; dict or nested list for `pv_trend_w`; a custom `__float__` object; `float` subclass; `pv_samples` as `False`, `3.0`, `-1` or an `int` subclass; NaN; +inf; -inf; `True` as a voltage drop; `1.0` mode changes; a string duration | All rejected |
| Decision numbers and flags | `True` and NaN for `data_age_seconds`; dict, inf and `float` subclass for baseline and effective voltages; `shadow_only=1`; `command_executed=0`, `True`; `shadow_only=False` | All rejected |
| Datetimes | naive; `None`, `date`, ISO string or epoch float for `evaluated_at`; `datetime` subclass; tzinfo with no offset; list and naive `lockout_expires_at` | All 9 rejected |

**Accepted inputs, behaving correctly:**

- Caller lists are copied. Changing them after construction leaves the decision equal to the original.
- String reason names are converted to `OutputPolicyReason` members.
- Every evaluator-produced decision is hashable, which is only possible because all of its fields are immutable. Each rebuilt copy hashes the same as the original, and `deepcopy` and normal pickle round trips compare equal.

**Fuzzing:**

| Run | Cases | Decisions | Result |
|---|---|---|---|
| Varied history | 800 | all BLOCK | All rebuild and hash; 0 constructor errors |
| Mixed inputs | 1,200 | BLOCK 1,034, CONSIDER 80, HOLD 86 | All rebuild and hash; 0 constructor errors |

So the stricter validators reject no output the evaluator actually produces, including integer-valued inputs and baselines.

## 5. UTC timestamps

A lockout instant of 11:05 UTC was supplied in four timezones: America/New_York, Asia/Tokyo, Europe/London and a fixed UTC-03:30. Each produced 11:05 UTC with `timezone.utc` attached, and each decision equalled the one built directly in UTC.

Daylight-saving crossing: a failed trial was placed just before London's clocks went back (25 October 2026, 01:00 UTC), and the inputs were supplied in London time. The lockout was `2026-10-25T01:35:00+00:00`, identical to the all-UTC run, and the two decisions compared equal. So timezone handling doesn't shift lockout expiry.

The suite's adjustable-timezone test checks the same property from another angle: changing the caller's tzinfo after construction doesn't move the stored instant.

## 6. C-01 smoke tests

The C-01 scenario probe and the 384-case current-safety grid were re-run on this evaluator. Their output is byte-identical to the run accepted at e7902b1c (`c01_after.out` and `c01_grid.out` in the scratchpad).

| Check | Result |
|---|---|
| Unknown discharge limit can't authorize CONSIDER | PASS: 0 A with a `None` limit is BLOCK / UNKNOWN in Line and Invert, with `discharge_limit_unapproved` |
| Missing readings stay conservative | PASS: missing discharge or charge readings give BLOCK / UNKNOWN with a specific reason |
| Limit violations have specific reasons | PASS: inclusive limit; 70.5 A on 70 A gives `HIGH_BATTERY_DISCHARGE` / INELIGIBLE; 60.5 A on 60 A gives `HIGH_BATTERY_CHARGE`; never `INVALID_MEASUREMENT` |
| Positive charge with unknown charge limit | PASS: 0.5 A, 40 A, or 30 A in history only, all unverified |
| Charge and discharge both positive | PASS: `INCONSISTENT_BATTERY_CURRENT` / UNKNOWN in Line and Invert |
| Current issues don't hide sag | PASS: collapse at 0, 49, 50, 85, 100 and 300 A, limit `None`, 70 A or 90 A: always `FAST_VOLTAGE_SAG` with lockout to 11:05 |
| Supported CONSIDER still reachable | PASS: 26 positive outcomes in the grid, and 0 invariant violations |
| Invert HOLD can't hide a current problem | PASS: no HOLD when current safety is unverified or a limit is exceeded |

## 7. Regression protection and test results

| Area | Result |
|---|---|
| Short and long sag | PASS: short-trial collapse; 3-hour session with a 0.4 V settling step returns HOLD; 4-, 12- and 30-minute terminal sags; recovered dips |
| Lockouts | PASS: escalation case gives 16:10 with 480 minutes of history (479 minutes gives `INSUFFICIENT_HISTORY` plus the 16:10 lockout); churn lockout to 10:36, then CONSIDER at expiry |
| Telemetry freshness | PASS (suite) |
| Solar productivity | PASS: 300 W, then 800 W x6, then 460 W returns WEAK |
| Unknown modes | PASS: a recent Fault interval returns `MODE_HISTORY_UNCERTAIN` with no invented lockout |
| `DEMAND_EXCEEDS_PV` | PASS: 1000 W/700 W returns CONSIDER with the advisory reason |
| SOC excluded | PASS: SOC 1 and 99 give equal results |
| Determinism | PASS: 1,500 of 1,500 identical with shuffled history |
| Shadow-only | PASS |

**Validation commands (actual results):**

| Command | Result |
|---|---|
| `unittest discover -s tests -p 'test_*.py' -v` | 70 total, 70 passed, 0 failed |
| `py_compile` on both files | exit 0 |
| `git diff --check` | clean |
| `git status --short` | three untracked paths |

**New M-01 tests (4):**

- `test_m01_assessments_reject_mutable_and_wrongly_typed_fields`
- `test_m01_decision_rejects_mutable_or_incompatible_public_fields`
- `test_m01_valid_public_graph_is_frozen_and_evaluation_is_repeatable`
- `test_m01_decision_normalizes_mutable_timezone_state`

They test real behaviour through the public constructors and through `evaluate_output_policy`: rejections, caller-mutation isolation, frozen nested objects, and the real CONSIDER, BLOCK and HOLD decisions. They don't test private helpers.

**Pre-fix check.** I ran the current suite against the rebuilt e7902b1c evaluator:

| Test | Result on e7902b1c |
|---|---|
| `test_m01_decision_rejects_mutable_or_incompatible_public_fields` | 12 failures, 3 errors |
| `test_m01_assessments_reject_mutable_and_wrongly_typed_fields` | 1 failure |
| `test_m01_decision_normalizes_mutable_timezone_state` | 1 failure |
| `test_m01_valid_public_graph_is_frozen_and_evaluation_is_repeatable` | passes |
| All other 66 tests | pass |

That totals 14 failures and 3 errors; the valid-graph test passes on both versions, as a guard against regressions should. The developer reported 9 failures and 3 errors. The difference is probably a different subtest count, and it doesn't change the conclusion: each new failing test fails on the old code and passes on the new.

**Test integrity:** compared with the saved pre-C-01 test file, the only changes to existing content are the import list and the C-01 fixture edits that pass `None` explicitly. Every other difference is a new test. No existing test was removed or weakened.

## 8. Determinism and side-effect safety

| Check | Result |
|---|---|
| Imports | `__future__`, `dataclasses`, `datetime`, `enum`, `math` only (AST) |
| Clock and I/O calls | none: no `now`, `utcnow`, `today`, `time`, `open`, `write`, `connect`, `print`, `eval` or `exec` |
| Module state | only `__all__` at module level; no `global` or `nonlocal` |
| Runtime integration | no module under `app/` imports the evaluator; only the test does |
| No-I/O at runtime | CONSIDER, BLOCK and HOLD paths completed with `open`, `socket.socket`, `subprocess.Popen` and `time.time` patched to raise |
| Deterministic results | identical inputs gave identical decisions |
| Shadow-only | the constructor rejects `command_executed=True` and `shadow_only=False` |

No DESS, Tuya, network, database, hardware or production system was accessed.

## 9. Unresolved findings

**Blockers: none. Major findings: none.**

Informational observations (not M-01 defects):

1. **Python-level bypass.** Without `__slots__`, any frozen dataclass can still be changed deliberately with `object.__setattr__(decision, "reason_codes", [...])` or `vars(decision)["command_executed"] = True`. The same goes for state written directly into `__dict__` before pickling, since unpickling skips `__post_init__`; I reproduced this in `m01b.py`. None of these are constructor paths, and no ordinary constructor or `dataclasses.replace` call can do this. If hostile in-process code ever matters, add `slots=True` and a `__reduce__` that rebuilds through the constructor.
2. **Exact-type rule rejects library types.** Subclasses of `float`, `int` and `datetime` are rejected, including `pandas.Timestamp`- and numpy-style values. Any future replay adapter must convert to built-in types first. This is a conservative choice, not a defect.
3. **Input models aren't type-checked as deeply.** `OutputTelemetry.forecast` accepts a list (no check at `:138-158`); evaluation then raises `AttributeError` and returns no decision. That fails closed and is outside the M-01 decision graph. Optional hardening: type-check `forecast`.

## 10. Operational prerequisites

These are not implementation defects:

- **Current limits:** hardware- or BMS-approved discharge and charge limits must be supplied before `CONSIDER` or `HOLD` can occur in practice. Until then the evaluator blocks, which is the intended behaviour.
- **Line Mode charging:** grid charging in Line Mode needs an approved charge limit, or any positive charge reading in the 8-hour window blocks.
- **Sensor semantics:** whether charge and discharge can both be positive at once is unverified. That case is currently treated as uncertain and blocks.
- **Voltage limit:** the upper voltage limit (`max_battery_voltage_v`) needs operator ownership.
- **Later phases:** replay data must use built-in types (section 9, item 2). Runtime wiring, replay cadence and hardware validation remain later-phase work.

## 11. Recommendation

**APPROVED.** M-01 is fully corrected for public construction, C-01 remains valid, no new regression was found, and Phase 1A remains a deterministic, shadow-only offline evaluator.

This approves the offline evaluator only, not deployment or hardware readiness. The section 10 prerequisites still apply.
