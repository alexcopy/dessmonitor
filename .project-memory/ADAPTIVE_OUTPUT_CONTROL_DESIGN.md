# Adaptive Output-Source Control — Design Concept

**Project:** dessmonitor / smart pond  
**Status:** DRAFT — architecture and safety requirements only; no hardware control enabled  
**Date:** 2026-11-02  
**Version:** 0.2 — revised download / implementation handoff  
**Proposed repository path:** `.project-memory/ADAPTIVE_OUTPUT_CONTROL_DESIGN.md`

## Reading guide and control boundaries

This design is an **English Markdown working architecture**, not a runtime configuration and not permission to execute inverter commands. Use it together with `.project-memory/ADAPTIVE_OUTPUT_CONTROL_IMPLEMENTATION_PLAN.md`.

The key distinction is physical: **output power is the aggregate load measured at the inverter output**, whether or not any house appliance is represented as a Tuya device. A 2 kW house load with 30 W of controlled pond switches is still a 2 kW inverter load. The control algorithm must reason about that measured demand; it cannot "fix" the house load by assuming only registered devices exist.

The proposed two windows have different roles: **Window A** decides whether daylight/solar recovery is plausible; **Window B** decides whether a battery-mode trial is stable and safe. A favorable Window A is never sufficient to override a risky Window B. The operator-approved YAML baseline always remains the point of reference.

## 1. Problem and objective

Recent observed inverter telemetry (31 October–2 November) shows repeated changes between `Line Mode` and `Invert Mode`, sometimes within 4–10 minutes. During much of that period, `Charger Source Priority` stayed at `Solar only`. Battery voltage often changed around 25.2–26.0 V at mode changes, while inverter-reported SOC sometimes jumped from roughly 70–75% to 100% and back. These observations are **not proof of the inverter's exact switching thresholds, battery chemistry, actual energy state, or which actor caused the transitions**.

The immediate goal is to reduce unnecessary output-mode cycling without risking battery damage or sacrificing the smart pond's essential functions. This is a **power-source and energy-reserve control problem**, not primarily a Controls UI or database problem.

**Critical scope clarification:** Total household demand must **not** be estimated by summing Tuya device loads. The house may draw 2,000 W through the inverter while known managed devices draw only 30 W. The main load input must be **measured inverter output power** (`output_power_w`), corroborated where possible with measured battery currents, PV power and working mode. It covers loads measured on that inverter's output, not unrelated circuits bypassing it. Managed relays are optional actuators, not a substitute for total-load telemetry; shedding 30 W will not solve a 2,000 W deficit.

## 2. Three separate control domains

| Domain | Observed field(s) | Currently known control capability | Rule |
|---|---|---|---|
| **Charging-source policy** | `charger_priority`, `charger_priority_raw` | `BatteryChargeController` writes `bat_charger_source_priority` and verified 2 A charging-current setting | Keep its emergency recovery policy independent |
| **Output-source / household supply** | `working_mode`, `output_priority` (DESS read), mains status | No confirmed safe API write for output-source voltage thresholds or return-to-battery settings | **Observe/advice only until exact hardware support is verified** |
| **Managed loads** | Tuya observed state and per-device YAML | `SolarAwareController` acts on selected relays | Optional local shedding only; never infer total house load from relays |

In the current runtime, `SolarAwareController` adapts `min_volt/max_volt` for **Tuya loads**, not the inverter's internal `Line ↔ Invert` transition thresholds. `SmartHomeController` switch automation is configured off in `run.py`; active Tuya decisions are made by `SolarAwareController`. Its interpretation of `Line Mode` must be independently audited before using it as a permission to turn loads on.

**Non-negotiable:** No guessed DESS field IDs, no trial-and-error output-priority writes, and no claim that a mode transition was caused by this application unless a matching issued command and subsequent observation are logged.

## 3. Two adaptive windows

### Window A — solar opportunity, not fixed clock hours

The operating day is derived from actual local **sunrise and sunset**, adjusted for the configured installation timezone, season/day length and the remaining time until sunset. Use minute-resolution times, not only truncated integer hours. Divide the day into operational phases: **pre-sunrise / early morning / productive day / late-day reserve / night**. Their boundaries may shift dynamically with weather and energy conditions.

Signals:

- Measured `pv_power_w`, its recent stability/trend and any curtailment caveat.
- Measured `output_power_w` and `battery_current_dis` / `battery_current_chg` with validated semantics.
- Time since sunrise, hours until sunset and daylight duration.
- Timestamped hourly forecast (clouds/rain/irradiance where actually available), with source and freshness.
- Measured battery voltage trend under **the same output mode and comparable load**.

A clear morning with hours of sunlight remaining may tolerate a modest, time-limited use of reserve. Late afternoon and winter's short day should become more conservative. A good forecast **never** overrides measured battery stress or a safety boundary. Missing/stale forecast disables aggressive adaptation rather than inventing sunlight.

This window answers: *Is there reliable time and generation left to recover today's battery reserve?*

### Window B — output-mode eligibility and hysteresis

Treat the inverter's battery/grid switchover as two **different** thresholds:

- `V_exit_battery`: when battery operation should stop and the load should be served from mains.
- `V_enter_battery`: when returning from mains to battery is permitted.

Normally `V_enter_battery > V_exit_battery`, creating hysteresis. A healthy gap also needs **minimum time in each mode**, stable-condition confirmation, a restart-safe cooldown and a maximum allowed command frequency. The actual device may already implement its own thresholds; application control is an optional future feature, not an assumed capability.

The effective window is constrained by Window A and battery safety. Higher current household demand, low PV, a rapid measured voltage sag, and limited daylight should make **returning to battery harder**, not easier. For example, after a failed 5-minute battery trial, do **not** lower the grid-fallback voltage just to prolong the next trial. Instead, hold on mains longer or require a demonstrably stronger recovery before reconsidering. Extreme voltages such as 27–29 V are **not generic recommended settings**: permissible values depend on battery chemistry, series-cell count, BMS, equipment and manufacturer limits.

This window answers: *Would a return to battery remain stable long enough to justify another transition?*

## 4. Immutable baselines and temporary effective values

Use two distinct config concepts:

**Baseline** = manufacturer/operator-approved reference settings stored durably in YAML. They must not drift as the controller learns or observes weather. **Effective settings** = `baseline + bounded_delta` computed in memory, stamped with a reason, revision, expiry time and allowable range. The controller never writes effective values back over baseline.

Current `devices.yaml` contains per-managed-device baseline fields (`min_volt`, `max_volt`, `coefficient`, `priority`, `time_delay`). **Those are relay thresholds, not confirmed inverter output-source thresholds.** Add a new top-level, separate `output_source_policy:` section to this same YAML only after implementing a validated parser; keep `devices:` and the existing `charge_policy:` intact. Until inverter-side baseline values are read and verified, represent them as `null`, not guessed 25.5/26.5 V.

Reconcile toward baseline at the end of the usable solar window, at the expiry of a temporary adaptation, on an operator reset and after recovery from stale telemetry. A noon checkpoint should **re-evaluate** opportunity and encourage return toward baseline, not blindly send hardware writes. Restoration of actual inverter configuration must be idempotent, conditional on fresh readback and device support, and rate-limited so that merely restoring settings cannot cause new chatter.

**Restart rule:** Reload the same baseline YAML; discard expired effective values; restore persisted anti-flap lockout and recent command history from a separate durable runtime-state journal; read the actual inverter settings before any potential write. If readback is unavailable, remain in observation/hold. Project Memory is for design documentation, **not** a runtime state store.

## 5. A small deterministic intelligence layer

Use a conservative deterministic policy before considering ML.

### 5.1 Measurement quality

Every decision requires a timestamped snapshot with age/freshness information. Accept `NULL` as unknown, not zero. Keep inverter output watts, battery currents, PV power, battery voltage, working mode and forecast separate; do not label output watts as measured mains import. Battery SOC remains **reported SOC** and is not an authoritative switching trigger while its meaning is unstable across modes.

### 5.2 Supply adequacy

An informal indicator `PV − inverter output` is useful only as a **rough warning**, not a true battery/grid power balance: conversion losses, battery charging, curtailment and inverter topology can invalidate equality. Prefer observed battery discharge current, measured voltage under a comparable load and actual output watts. A large measured load with weak PV is a negative signal even when managed Tuya devices appear nearly idle.

### 5.3 Battery endurance under observed demand

From several fresh samples in the **same stable `Invert Mode` segment**, measure a robust slope `dV/dt` over 5–15 minutes, omitting the immediate settling period after mode changes and discontinuities caused by large load steps. With sufficient quality, derive a **rough time-to-guard**:

`T_to_guard ≈ (V_now − V_guard) / abs(dV/dt)` for a consistently negative slope.

This is a risk indicator, not an exact runtime or state-of-charge estimate. Do not extrapolate a voltage recovery on `Line Mode` as evidence that the battery has recharged. Use previous *comparable* battery-mode trials to estimate whether a new trial would collapse within minutes. If data are insufficient, mark risk `UNKNOWN` and do not authorize a risky return to battery.

### 5.4 Prioritised outcomes

1. **Safety:** battery and equipment hardware limits; emergency grid charging remains controlled separately by the existing charge policy (`<24.0 V`, release at `≥24.5 V`, per currently configured defaults).
2. **Stability:** avoid rapid `Line ↔ Invert` cycling, including attempts that fail in minutes.
3. **Reserve:** retain adequate voltage/energy for the evening, short winter days and essential pond functions.
4. **Solar utilization:** use battery only when measured conditions suggest a sustainable interval; forecast is advisory.
5. **Optional controllable load reduction:** only if measured managed loads are substantial enough to matter, with per-device safety rules and confirmation.

## 6. Decision states and anti-flapping

Proposed *software decision states* (not hardware states):

- `OBSERVE`: monitor and learn; default deployment mode; do not send output-source writes.
- `GRID_HOLD`: output observed on mains; do not request battery return until conditions and cooldown allow.
- `BATTERY_CANDIDATE`: candidate return supported by stable PV, acceptable demand, remaining daylight and battery margin.
- `BATTERY_TRIAL`: observe the first several minutes after an actual `Line → Invert` transition; quickly detect voltage sag under load.
- `BATTERY_STABLE`: sustained battery operation under acceptable voltage slope and demand.
- `CHURN_LOCKOUT`: repeated fast reversal or high transition count; require stronger conditions and a timed quiet period before any new voluntary attempt.
- `FAULT_HOLD`: stale/conflicting data, readback mismatch, unsupported controls or command failure; no optimistic writes.

Illustrative initial *policy candidates for later simulation*, not safe hardware limits: conditions stable for 5–10 minutes; minimum healthy battery dwell around 30 minutes; minimum mains dwell around 30–60 minutes; a 30–60-minute cooldown after a failed trial; max 2 optional output-source commands/hour. All durations must be tuned against actual telemetry and the inverter's documented operation, and must never delay urgent hardware/BMS safety protection.

When a trial shows a fast sag, record `FAST_VOLTAGE_SAG`, prevent immediate retry, and raise the required confidence or return-to-battery eligibility. **Do not lower the protective exit threshold** in response to fast collapse. Any future relaxation must remain bounded, reversible, time-limited and supported by evidence of adequate capacity/solar recovery.

If the observed inverter changes modes on its own and there is no safe writable output-source interface, the policy can **detect and advise**, but cannot guarantee prevention of hardware cycling. The remedy may require operator-approved changes on the inverter itself or real reduction of household load; managing an unrelated 30 W relay is not an adequate remedy for a 2 kW load.

## 7. Command provenance and why each transition happened

Maintain **two independent event streams** with correlation IDs:

**A. Our decisions and issued commands:** UTC timestamp; `decision_id`; component; policy version; fresh measurements and timestamps; baseline/effective thresholds; mode before action; reason code; `ALLOW`, `HOLD`, `RECOMMEND`, `BLOCK`, `COMMAND`; operator override; command target and exact field ID (only when verified); request result; observed readback; expiry/cooldown.

**B. Observed physical changes:** UTC observation time; from/to `working_mode`; voltage/PV/output watts/battery currents before and after; known `charger_priority` and `output_priority_raw` if available; nearby command correlation; telemetry sampling gap. Do not mislabel the time the mode was **first seen** as the exact electrical switching instant.

Without a matching command, state **`origin: unknown_external_or_device`**; do not assert that it was the inverter itself, a person, or the controller. If an application command was sent but no matching observed change followed, record `UNCONFIRMED`, never success. No logging of secrets or Tuya identifiers in public UI.

Suggested reason codes: `SUNRISE_OPPORTUNITY`, `SUNSET_RESERVE`, `PV_DEFICIT`, `HIGH_MEASURED_LOAD`, `FAST_VOLTAGE_SAG`, `BATTERY_GUARD`, `COOLDOWN`, `EXCESSIVE_MODE_CHANGES`, `STALE_TELEMETRY`, `UNKNOWN_OUTPUT_CAPABILITY`, `MANUAL_OVERRIDE`, `BASELINE_RECONCILIATION`.

**Operational logs are separate from the Markdown Project Memory design.** Persist structured machine-readable events in existing logs/Loki or a separately reviewed audit sink. Do not silently treat inferred event duration as an actual switch command.

## 8. Proposed YAML shape (design only; NOT wired to runtime)

This proposed section belongs at the **top level of the existing `devices.yaml`**, alongside `devices:` and `charge_policy:`. This exact schema is a proposal, not a description of a currently supported parser. New policy must be validated, versioned and hot-reloaded safely without overwriting any other YAML section.

```yaml
output_source_policy:
  version: 1
  mode: observe                # observe | advisory | active (active not yet permitted)
  allow_output_writes: false   # HARD OFF until device-specific validation/readback

  baseline:                   # hardware setpoints: unknown until verified
    exit_battery_v: null
    enter_battery_v: null
    min_hysteresis_v: null

  solar_opportunity:
    timezone: Europe/London    # replace with actual site timezone if different
    sunrise_offset_min: 30
    sunset_reserve_lead_min: 120
    forecast_max_age_min: 180
    pv_stability_window_min: 10

  demand_and_sag:
    use_measured_output_w: true
    use_measured_battery_current: true
    voltage_trend_window_min: 10
    post_transition_settle_min: 3
    min_eligible_runtime_min: 45
    unknown_risk_blocks_battery_return: true

  hysteresis_adaptation:
    enabled_in_shadow: true
    max_delta_exit_v: null     # configure only after battery/hardware review
    max_delta_enter_v: null
    expiry_min: 120
    midday_reassessment: true
    restore_before_sunset: true

  anti_flap:
    stable_condition_min: 10
    min_mains_dwell_min: 45
    min_battery_dwell_min: 30
    failed_trial_window_min: 10
    failed_trial_lockout_min: 60
    max_optional_output_commands_per_hour: 2

  safety:
    honor_bms_and_inverter_protection: true
    no_optimistic_action_on_stale_data: true
    respect_manual_override: true
    leave_charge_policy_independent: true

  audit:
    decision_log_enabled: true
    source_transition_log_enabled: true
    command_observation_correlation: true
```

**Warning:** The example `Europe/London` is not a claim about the installation site. Use its verified timezone. `null` baseline values explicitly block output threshold writes; do not replace with guessed values. This design does **not** authorize changing charge-current settings, battery low-voltage safety limits, or extreme voltage values.

## 9. Example operating scenarios

| Situation | Evidence | Proposed decision in observation/advisory mode |
|---|---|---|
| Morning: 2 kW output, 100 W PV, weak/unknown battery support | High measured output, low measured PV, potential fast sag | Stay conservative; reject battery-return recommendation; monitor battery current and slope |
| Mid-morning: modest output, strong sustained PV, long daylight remaining | Stable PV, acceptable battery voltage trend, fresh weather | Eligible for **candidate** battery trial subject to dwell/lockout and hardware support |
| Battery mode lasts five minutes, voltage rapidly collapses | Confirmed under-load sag within one stable mode segment | Mark failed trial, enforce longer mains hold, never lower protective exit threshold |
| `Line Mode` causes instantaneous voltage rebound | Removal of discharge load, no confirmed recharge | Do **not** treat rebound as a charging achievement or new SOC |
| Late-day cloudy weather, short winter daylight | Little recovery time, weak PV | Preserve baseline/conservative reserve; no aggressive return to battery |
| SOC jumps 72% ↔ 100% as working mode changes | Inconsistent inverter-reported SOC | Display as raw reported SOC; do not switch because of this field alone |
| Tuya devices total 30 W, measured inverter output 2 kW | Independent household demand | Do not claim that turning off Tuya devices solves the deficit |
| Output mode alternates, charger source remains `solar_only` | Two distinct recorded states | Investigate output thresholds, load and external/manual actions; do not blame charger commands |
| No fresh DESS or forecast data | Stale/unknown signals | `FAULT_HOLD`/observe; no new aggressive commands; keep physical protections independent |

## 10. Implementation path and acceptance gates

**Phase 0 — document and read-only diagnosis.** Validate actual wiring/topology, battery chemistry/BMS limits, inverter model/firmware, current physical output thresholds, inverter-measured total output power, raw `output_priority`, telemetry freshness and device-specific supported readback. Keep the user's pending Controls output-mode diagnostics separate from this design.

**Phase 1 — shadow/advisory decision engine.** Build a pure deterministic `evaluate_output_policy(snapshot, baselines, recent_history) -> decision` without I/O. Replay 7–30 days of real telemetry, label unsupported or insufficient evidence as `UNKNOWN`, and log what the controller *would* have done, without commanding an inverter. Evaluate whether it would have prevented short battery trials while preserving productive solar hours. Compare mode churn, conservative dwell and battery-sag risk.

**Phase 2 — limited managed-load intervention (optional).** Only where controlled loads are actually material, and only with explicit per-device classification (critical pond life-support vs discretionary), verified current observations, operator consent, one actuator at a time, readback and cooldown. Avoid competing command writers. For a 2 kW unmanageable house load, this phase alone cannot solve the problem.

**Phase 3 — guarded inverter-output control (not approved in v0.2).** Requires vendor/inverter-specific confirmation of exact fields, permitted ranges, read-before-write, safe rollback, manual override priority, rate limits, actuator arbitration, tests with a non-live fake, and an explicit separate operator approval. If firmware only offers a manual panel, do not invent an API path.

Acceptance criteria before any active output-control deployment:

1. Every decision has a measurement snapshot, data age, baseline, effective value, reason and recommended outcome.
2. A 2 kW measured load cannot be mistaken for 30 W because only Tuya devices were summed.
3. A fast battery collapse increases conservatism/lockout rather than allowing deeper unsafe discharge.
4. Repeat `Line ↔ Invert` transitions are counted separately from charger-source changes; no assumed origin.
5. Baseline values survive restart; expired adaptive offsets do not; command cooldown is restart-safe.
6. Unknown/stale input cannot cause optimistic switches or uncontrolled retries.
7. Safety thresholds, emergency charger policy and essential pond loads are not overridden.
8. Shadow-mode replay produces **zero** DESS writes and **zero** Tuya switch commands.
9. Any later active feature is independently gated and demonstrably safe for the actual device and battery.

## 11. Explicit open questions before numeric tuning

- Which exact inverter hardware/firmware and battery chemistry/BMS are installed? What are the manufacturer-approved voltage ranges and actual stored return-to-grid/return-to-battery settings?
- Does `output_power_w` cover all relevant house loads (including bypass modes), or is an additional AC power meter needed?
- Are battery charge/discharge currents accurate and fresh across both `Line Mode` and `Invert Mode`? Does `output_priority` reflect a priority configuration or something else on this inverter?
- Can output thresholds be **read and written** through a documented DESS control method? If not, which settings require operator action on the inverter?
- What is the required nighttime reserve, and which pond functions are safety-critical and must not be interrupted?
- What is the verified site timezone and the actual sunrise/sunset time precision of the weather source?

---

**Design principle:** **Immutable YAML baselines + two time/energy windows + inverter-measured demand + observed battery sag + anti-flapping + explainable decisions.** Monitor and replay first. Do not change inverter output settings until hardware capabilities and safe ranges are proved.

**Implementation handoff:** On the new development branch, commit the previously unfinished *diagnostic* changes separately after review, then add this Markdown file and the implementation plan under `.project-memory/`. The first new implementation milestone is an offline deterministic evaluator with no DESS output-source writes and no Tuya actuation.
