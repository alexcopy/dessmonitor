# Independent architecture and safety review — Phase 1B

## Metadata and verdict

- Review date: 2026-11-03. Repository: `/Users/alexred/Projects/Python/dessmonitor`; branch `feat/adaptive-output-control`; HEAD `1155d0776c74ae7705541fee8997f12d9a56bb87`, two commits ahead of upstream.
- Loader SHA-256 `c0b166dccf1cbf7063dbc5f273c88db3980ec4daf07deb572ece56fb08cb4f6d`; tests SHA-256 `45de3b57d63f43d238005be00346409c43104363d4be179aa71e6d408545fd32`. Both exactly match the requested versions. Both Phase 1B files are untracked and were read directly.
- Phase 1A evaluator SHA-256 `e671810fe66649032039c8cfb290d1e6431b077e7620199d233d6de50dbb8d25`; tests SHA-256 `fc63e662a2355a975307a41555e3ca9f7cca085622747c6836a9ebebf152420c`. Both match the approved `ADAPTIVE_OUTPUT_PHASE1A_M01_ACCEPTANCE.md`; `git diff HEAD` confirms the tracked evaluator, its tests, device initializer and devices.yaml are unchanged.
- Read the architecture, implementation plan, Phase 1A contract, configuration loader and tests, and existing device initializer. No production, test, configuration or earlier review file was modified.

**CHANGES_REQUIRED.** The file loader silently resolves duplicate safety-critical keys and can convert ambiguous current limits into apparently approved evidence that changes an evaluator result from BLOCK to CONSIDER. Public configuration constructors bypass the validation and deep-immutability guarantees. Additional validation and purity gaps are detailed below. The implementation has no hardware command path and is not runtime-wired; the findings do not assert any command was issued.

## YAML schema coverage matrix

The design's section 8 lists the original schema; the three optional limits are the explicitly approved extension in this review brief. Paths below are relative to `output_source_policy.`; `→` is the Phase 1A field and multiplication by 60 converts minutes to seconds. Unless marked optional, the implementation requires the key. All seven section mappings and top-level `version`, `mode`, `allow_output_writes` are required. Configuration fields have the same section and field names as the YAML, except the top-level config has a `configured` marker. “Retained” means stored without a Phase 1A mapping. Numeric validation shown describes implementation; defects and unsafe boundaries are identified separately.

| YAML field path | Accepted type / validation | Immutable configuration field | Phase 1A mapping / retained |
|---|---|---|---|
| `version` | exact int, 1 | `version` | retained |
| `mode` | exact string `observe` | `mode` | retained |
| `allow_output_writes` | exact false | `allow_output_writes` | retained |
| `baseline.exit_battery_v` | optional, null or finite positive number | `baseline.exit_battery_v` | → `exit_battery_v` |
| `baseline.enter_battery_v` | optional, null or finite positive number; greater than exit when both set | `baseline.enter_battery_v` | → `enter_battery_v` |
| `baseline.min_hysteresis_v` | optional, null or finite nonnegative number; no larger than known enter/exit gap | `baseline.min_hysteresis_v` | → `min_hysteresis_v` |
| `baseline.max_battery_voltage_v` | optional, null or finite positive number; at least known enter | `baseline.max_battery_voltage_v` | → `max_battery_voltage_v` |
| `baseline.max_battery_discharge_a` | optional, null or finite positive number | `baseline.max_battery_discharge_a` | → `max_battery_discharge_a` |
| `baseline.max_battery_charge_a` | optional, null or finite positive number | `baseline.max_battery_charge_a` | → `max_battery_charge_a` |
| `solar_opportunity.timezone` | optional, null or valid IANA name | `solar_opportunity.timezone` | retained; validation has environment dependency |
| `solar_opportunity.sunrise_offset_min` | finite nonnegative number | `solar_opportunity.sunrise_offset_min` | retained |
| `solar_opportunity.sunset_reserve_lead_min` | finite nonnegative number | `solar_opportunity.sunset_reserve_lead_min` | → `sunset_reserve_seconds` |
| `solar_opportunity.forecast_max_age_min` | finite positive number | `solar_opportunity.forecast_max_age_min` | → `forecast_max_age_seconds` |
| `solar_opportunity.pv_stability_window_min` | finite positive number | `solar_opportunity.pv_stability_window_min` | → `pv_history_window_seconds` and `pv_minimum_span_seconds` |
| `demand_and_sag.use_measured_output_w` | exact true | `demand_and_sag.use_measured_output_w` | retained |
| `demand_and_sag.use_measured_battery_current` | exact true | `demand_and_sag.use_measured_battery_current` | retained |
| `demand_and_sag.voltage_trend_window_min` | finite positive number | `demand_and_sag.voltage_trend_window_min` | → `voltage_sag_window_seconds` |
| `demand_and_sag.post_transition_settle_min` | finite nonnegative number | `demand_and_sag.post_transition_settle_min` | → `post_transition_settle_seconds` |
| `demand_and_sag.min_eligible_runtime_min` | finite positive number | `demand_and_sag.min_eligible_runtime_min` | retained; no unrelated Phase 1A mapping |
| `demand_and_sag.unknown_risk_blocks_battery_return` | exact true | `demand_and_sag.unknown_risk_blocks_battery_return` | retained |
| `hysteresis_adaptation.enabled_in_shadow` | bool | `hysteresis_adaptation.enabled_in_shadow` | retained |
| `hysteresis_adaptation.max_delta_exit_v` | optional, null or finite nonnegative number | `hysteresis_adaptation.max_delta_exit_v` | retained |
| `hysteresis_adaptation.max_delta_enter_v` | optional, null or finite nonnegative number | `hysteresis_adaptation.max_delta_enter_v` | retained |
| `hysteresis_adaptation.expiry_min` | finite positive number | `hysteresis_adaptation.expiry_min` | retained |
| `hysteresis_adaptation.midday_reassessment` | bool | `hysteresis_adaptation.midday_reassessment` | retained |
| `hysteresis_adaptation.restore_before_sunset` | bool | `hysteresis_adaptation.restore_before_sunset` | retained |
| `anti_flap.stable_condition_min` | finite positive number | `anti_flap.stable_condition_min` | retained; not mapped to an unrelated window |
| `anti_flap.min_mains_dwell_min` | finite positive number | `anti_flap.min_mains_dwell_min` | → `min_mains_dwell_seconds` |
| `anti_flap.min_battery_dwell_min` | finite positive number | `anti_flap.min_battery_dwell_min` | → `min_battery_dwell_seconds` |
| `anti_flap.failed_trial_window_min` | finite positive number | `anti_flap.failed_trial_window_min` | → `failed_trial_max_seconds` |
| `anti_flap.failed_trial_lockout_min` | finite positive number | `anti_flap.failed_trial_lockout_min` | → `failed_trial_lockout_seconds` |
| `anti_flap.max_optional_output_commands_per_hour` | exact nonnegative int | `anti_flap.max_optional_output_commands_per_hour` | retained |
| `safety.honor_bms_and_inverter_protection` | exact true | `safety.honor_bms_and_inverter_protection` | retained |
| `safety.no_optimistic_action_on_stale_data` | exact true | `safety.no_optimistic_action_on_stale_data` | retained |
| `safety.respect_manual_override` | exact true | `safety.respect_manual_override` | retained |
| `safety.leave_charge_policy_independent` | exact true | `safety.leave_charge_policy_independent` | retained |
| `audit.decision_log_enabled` | bool | `audit.decision_log_enabled` | retained |
| `audit.source_transition_log_enabled` | bool | `audit.source_transition_log_enabled` | retained |
| `audit.command_observation_correlation` | bool | `audit.command_observation_correlation` | retained |

For successfully parsed ordinary mappings, every designed field is retained. Exact built-in int/float inputs become float, bools are rejected as numbers. `devices` and `charge_policy` are not mutated or consumed by this loader; the real device initializer independently reads the top-level `devices` and `charge_policy` sections and has its own section-preserving writer. No relay `min_volt`/`max_volt`, charging current, or unrelated value feeds the baseline. Phase 1A's own defaults supply fields without matching configuration fields; no current safety limit is supplied by those defaults.

## Findings

### B-01 — BLOCKER: ambiguous YAML silently becomes approved current evidence

- **Location:** `app/control/output_policy_config.py:222–229`, especially `yaml.safe_load` at line 226; the ordinary Python mapping passed to validation has already lost duplicate keys.
- **Reproduction:** Create an otherwise valid v1 file with the following two entries in `output_source_policy.baseline`, and `exit_battery_v: 24.5`, `enter_battery_v: 25.5`, `min_hysteresis_v: 0.5`, `max_battery_voltage_v: 30`:

  ```yaml
  max_battery_discharge_a: 5
  max_battery_discharge_a: 75
  ```

  `load_output_policy(path).baseline.max_battery_discharge_a` is silently `75.0`. In an otherwise healthy complete Line history, a 20 A discharge gives CONSIDER / ELIGIBLE with no high-current reason. The same document with the sole 5 A entry yields BLOCK / INELIGIBLE and `HIGH_BATTERY_DISCHARGE`. A duplicate `mode: active` followed by `mode: observe` is likewise accepted as observe. YAML merges/aliases also require deliberate ambiguity handling.
- **Expected:** reject duplicate or otherwise ambiguous safety-relevant keys with a clear path, before mapping construction discards evidence; do not guess which setting was approved. `parse_output_policy` on an already-created dict cannot recover duplicates, so this needs to be enforced at the YAML decoding boundary as well as documenting the pre-parsed API's limitation.
- **Impact:** a disputed/unapproved higher bound is represented as verified evidence and can produce a positive shadow recommendation. This fits the critical-invariant category even though no live hardware write path exists in this phase.
- **Correction:** use a safe YAML loader that detects duplicates and ambiguous merge collisions at all relevant nesting levels and rejects them with useful paths; test repeated root/section/safety keys and safe aliases without permitting object constructors.

### M-01 — MAJOR: public config models bypass validation and deep immutability

- **Location:** `app/control/output_policy_config.py:18–99` and `:101–120`; validation exists only in `parse_output_policy` at `:185–219`.
- **Reproduction:** With `cfg` obtained from a valid parse:

  ```python
  values = []
  b = BaselineSettings(None, None, None, max_battery_charge_a=values)
  invalid = dataclasses.replace(cfg, baseline=b, mode="active", allow_output_writes=True)
  values.append(42)
  assert invalid.baseline.max_battery_charge_a == [42]
  assert invalid.mode == "active" and invalid.allow_output_writes is True
  ```

  Constructors also accept wrong types or nested mutable mappings directly, and `UnconfiguredOutputPolicy(configured=True)` is possible. `to_baseline` has no guard that the configuration came from parsing. Normal parser-produced graphs contain immutable built-in primitives and are isolated from ordinary caller dict mutations, but the public graph itself is not guaranteed validated or immutable.
- **Expected:** public construction/replacement must reject unsafe mode/write settings and unsupported nested objects or be made inaccessible as validated configuration; all reachable fields must be immutable validated values.
- **Impact:** callers can receive or create something named `OutputPolicyConfig` that falsely represents unsupported active permission or changes after construction. There is no current hardware invocation; this is a configuration-contract failure and a hazard for future consumers.
- **Correction:** enforce invariants at every public constructor or expose only a validated immutable construction path; validate nested field types and flags, and test direct construction and `dataclasses.replace`.

### M-02 — MAJOR: inconsistent partial voltage thresholds are accepted

- **Location:** `app/control/output_policy_config.py:202–210`; maximum voltage is compared with enter only when enter is present.
- **Reproduction:** A valid document with baseline `exit_battery_v: 25`, `enter_battery_v: null`, `max_battery_voltage_v: 24` parses successfully and projects those values. Equality of exit and maximum (25/25) is also accepted even though no possible `enter` can be both strictly greater than exit and at most the maximum.
- **Expected:** reject a known upper battery bound at or below known exit, independent of whether enter has been supplied, since no valid completion can satisfy both threshold relations. Unknown values must remain allowed; this requires no invented physical limits.
- **Impact:** an internally contradictory operator configuration is presented as validated. The evaluator remains conservative when enter is missing; this is not evidence of an unsafe live action.
- **Correction:** validate feasibility across every known pair and return an error naming `output_source_policy.baseline.max_battery_voltage_v`; cover partial/null cases and exact boundary.

### M-03 — MAJOR: accepted duration can crash the evaluator

- **Location:** `app/control/output_policy_config.py:135–148` and `:110–119`.
- **Reproduction:** Set `solar_opportunity.pv_stability_window_min = timedelta.max.total_seconds() / 60` in an otherwise valid document. Parsing and projection succeed; evaluating a normal snapshot at `2026-11-02 10:00 UTC` raises `OverflowError: days=1000000000; must have magnitude <= 999999999`. The accepted upper comparison is insufficient after floating-point rounding, and even smaller representable timedeltas can underflow a particular snapshot datetime when subtracted.
- **Expected:** configurations accepted for projection must not routinely cause evaluator arithmetic to fail; reject unrepresentable/unsupported windows with the YAML path, and handle date arithmetic limits safely at the appropriate layer without silently changing operator values.
- **Impact:** malformed yet “validated” configuration can interrupt shadow evaluation rather than yield a conservative decision. No physical action was observed.
- **Correction:** define and validate safe duration bounds for each mapped use and add arithmetic boundary coverage, including datetime limits.

### M-04 — MAJOR: timezone validation depends on external timezone files

- **Location:** `app/control/output_policy_config.py:165–172` (`ZoneInfo(value)`).
- **Reproduction:** In a fresh Python process, run `zoneinfo.ZoneInfo.clear_cache(); zoneinfo.reset_tzpath([])` with no installed `tzdata` package, then parse an otherwise valid document with `solar_opportunity.timezone: Europe/London`. The parser rejects this real IANA zone as invalid because its external timezone database is unavailable. With the normal host database, it accepts the same input.
- **Expected:** the stated pure mapping-validation API should not require filesystem access or depend on host timezone installation to validate the same document.
- **Impact:** portability and the explicit no-I/O contract fail for non-null timezones; the error conflates unavailable timezone data with an invalid operator value. No network/hardware/clock access is involved.
- **Correction:** separate environment-dependent zone resolution from pure validation or inject a controlled timezone catalog; if the architecture instead permits external timezone data, explicitly revise the contract and distinguish unavailable data from an invalid name.

## Hardware limit and version compatibility probes

All 27 combinations of missing/null/present across the three optional safety fields were parsed and projected with corresponding None or independently supplied 31.2 V, 73.3 A and 58.8 A values. For each field, `False`, `True`, string `"4"`, zero, negative, NaN, positive and negative infinity, list, dict and a huge integer were rejected with the field path; 0.001 and 3.14159 were accepted as illustrative finite positive values. These are test values, not physical ratings. The partial-voltage and duplicate-key defects above remain exceptions to overall validation safety. No numeric fallback creates a current or voltage limit. Maximum voltage maps to the upper safety bound, not an enter/exit switching threshold.

Original v1 without the extension, extended v1, every partial/null/present combination, and an empty optional baseline mapping work. Version 2, boolean version, `advisory`/`active`, true output writes, unknown fields, and malformed typed values are rejected. Missing section returns `UnconfiguredOutputPolicy(configured=False)` and has no baseline projection. Non-mapping roots (None, list, number, string) raise `OutputPolicyConfigError`. Unsafe Python YAML tags are rejected by safe loading. Nonduplicate unknown policy keys are rejected with key paths; unrelated top-level sections remain allowed. Duplicate YAML and partial-threshold handling fail as described; parse of an already-built dictionary cannot detect duplicates discarded upstream.

## Immutability, restart, side effects and compatibility

For objects returned by the parser, mutating the original nested baseline and solar dictionaries did not change the previously parsed config or repeated projection. Projection returns equivalent frozen Phase 1A baselines without changing the config, and retained-only fields remain in the config. Repeated loading of a temporary YAML returned equivalent objects and baseline and left the file bytes unchanged. This simulates restart/reload, not an actual running service restart. Direct constructors fail the stronger public-graph property (M-01).

The module imports dataclasses, datetime, math, pathlib, zoneinfo, PyYAML and the Phase 1A baseline. It does not import hardware, Tuya, database or cloud clients, does not write files, and has no runtime references outside tests. `load_output_policy` reads its supplied file only; it does not trigger runtime automation or alter charging. Monkeypatching builtins.open, sockets, subprocess and time.time to raise still allowed pure parse/projection with a null timezone. A real IANA timezone requires the timezone data source (M-04), so no-I/O cannot be passed unqualified. Baselines are not rewritten by adaptation in this module; no actual adaptive runtime exists here.

With illustrative explicit `30 V / 75 A / 55 A` limits and otherwise adequate independent evidence, the projected types were `OutputPolicyBaseline` with floats and the evaluator returned CONSIDER / ELIGIBLE, shadow only, no command. Replacing discharge limit with None returned BLOCK / UNKNOWN with `UNKNOWN_BATTERY_CURRENT_SAFETY`. The safety impact of ambiguous duplicate values is demonstrated in B-01; otherwise projection directly maps each of the three limits, with no inferred defaults.

## Tests and independent probes

`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`: **79 tests, 79 passed, 0 failed**, including 70 Phase 1A and 9 Phase 1B tests. Tests exercise public parse/load/projection and use explicitly illustrative limits; the positive candidate and unknown-current assertions are not merely unrelated BLOCK assertions. Their claim of deep immutability checks a frozen assignment on a parser-produced object but does not exercise public constructors, duplicate YAML, partial-threshold feasibility, extreme durations or timezone database availability. Nine Phase 1B tests therefore pass while the above defects remain.

Independent temporary probes (temporary files outside the repository, or in-memory scripts) covered the 27 limit-presence combinations, numeric types and boundaries, unknown keys, invalid mode/write/version, unsafe YAML tags, duplicate keys and resulting evaluator behavior, direct constructor/nested mutation, source mapping mutation, unsupported root values, config reload/file bytes, projected candidate/unknown decisions, extreme durations, and timezone data unavailability. No repository tests were changed. No network or hardware was contacted.

## Operational prerequisites and final recommendation

Actual inverter/BMS-approved current and voltage ranges, installation timezone, directional current sensor semantics, telemetry and hardware capability must be verified for later operational use. Illustrative values in tests or probes do not constitute such approval. Missing hardware values are intentionally supported and are not by themselves a defect.

**Do not approve Phase 1B** until B-01 and M-01 through M-04 are resolved and independently tested, preserving version-1 compatibility, retained fields, conservative unknowns, no hardware writes and the Phase 1A contract.
