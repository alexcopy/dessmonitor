# Energy-Aware Control Policy

## Purpose

This document defines the energy-aware control policy for dessmonitor. It captures
the product purpose, policy signals, staged architecture, and safety boundaries for
energy-aware device control. It is the authoritative requirements baseline for all
future control policy implementation.

PR 0011 establishes this document as a static requirements artifact only. It does NOT
enable runtime automation and does NOT modify runtime behavior.

## Product Truth

The core purpose of dessmonitor is **energy-aware control**:

1. Monitor battery/inverter voltage and power-source state.
2. Switch selected devices ON when energy conditions are favorable.
3. Switch selected devices OFF when voltage falls or the system moves to grid/network/mains power.
4. Periodically evaluate device readiness before switching ON.
5. Periodically check device health and observed ON/OFF state.
6. Use time-of-day and seasonal scheduling.
7. Use weather forecast to adjust policy.
8. Preserve evening battery reserve around **26.5V** after sunset.
9. Spend more energy on sunny days.
10. Conserve more energy on cloudy/rainy days.
11. Use ML later as advisory support, not direct hardware control.

## Current Repository State

- **PR 0007**: Platform control redesign strategy documented.
- **PR 0008**: Pump automation disabled by default; manual relay/switch ON/OFF preserved.
- **PR 0009**: Generic control domain types (SwitchableLoad, ControlCommand, etc.) introduced as passive data definitions.
- **PR 0010**: Adapter mapping relay-channel-shaped objects to SwitchableLoad instances.
- **PR 0011** (this PR): Energy-aware control policy requirements documented. No runtime automation enabled.

Runtime automation is not enabled by PR 0011. Manual relay/switch ON/OFF remains unchanged.
Pump automation remains disabled by default from PR 0008. ML control remains deferred.

## Energy-Aware Control Goals

1. Maximize solar energy utilization by switching discretionary loads ON during favorable conditions.
2. Protect battery reserve, targeting an **evening reserve around 26.5V** after sunset.
3. Conserve energy during cloudy/rainy days by adjusting thresholds and skipping discretionary loads.
4. Provide deterministic fallback when weather forecast is unavailable or stale.
5. Support manual override at all times — human control must always take priority.
6. Defer ML-based control behind safety-reviewed gates per ADR-0003.

## Policy Signals

The energy-aware control policy evaluates these signals:

| Signal | Description | Source |
|---|---|---|
| Battery voltage | Current battery/inverter voltage reading | DESS/inverter monitor |
| Voltage trend | Rising, falling, or stable over a configurable window | Derived from voltage history |
| Charging/discharging state | Whether the battery is charging or discharging (when available) | DESS/inverter |
| Power source | Solar, battery, grid, mains, or network (when available) | DESS/inverter |
| Time of day | Current time, used for scheduling windows | System clock |
| Season | Current season, used for seasonal profiles | System clock / date |
| Weather forecast | Sunny, cloudy, rainy, temperature, wind | OpenWeather (or deterministic fallback) |
| Temperature | Ambient temperature | OpenWeather (or DESS/inverter) |
| Device readiness | Whether a device is allowed to be switched ON now | Readiness evaluator (future) |
| Observed ON/OFF state | Currently observed state of the device | Tuya / device query |
| Device priority | Ordinal priority among loads | Per-device policy |
| Critical vs discretionary | Whether the load is critical or discretionary | Per-device policy |
| Evening reserve target | Desired voltage to preserve after sunset (26.5V) | Policy config (future) |

## Voltage and Power-Source Policy

Voltage is the **primary signal** for energy-aware control decisions.

- **Switch ON**: Devices may switch ON when voltage is above a configurable threshold AND the power source is favorable (solar/battery charging, not grid/mains).
- **Switch OFF**: Devices must switch OFF when voltage falls below a configurable threshold OR the power source changes to grid/network/mains (indicating conservation is required).
- **Voltage trend**: Rising voltage (charging) may allow more aggressive energy spending. Falling voltage (discharging) should trigger conservation earlier.
- **Power source priority**: Solar > battery > grid/mains. When on solar, discretionary loads are candidates. When on grid/mains, only critical loads may run.
- **Hysteresis**: Switch ON and switch OFF thresholds must have hysteresis to prevent rapid cycling.

## Device Readiness Model

Readiness is a **passive, side-effect-free computation** that determines whether a device
is allowed to be switched ON at the current moment.

Readiness is computed from:
1. Voltage — is voltage above minimum-to-switch-ON threshold?
2. Forecast — does weather forecast allow this device to run?
3. Time window — is the current time within the device's allowed window?
4. Cooldown — has sufficient time elapsed since the last switch action?
5. Current state — is the device currently OFF (switching ON makes sense)?
6. Season — does the seasonal profile allow this device?

Readiness is **separate from health**. A device can be ready but unhealthy, or healthy
but not ready. Readiness is safe to compute without changing device state.

## Device Health Model

Health is a **passive, side-effect-free computation** that assesses whether a device
is operating as expected.

Health evaluates:
1. **State match**: Whether observed state matches expected/commanded state.
2. **Query availability**: Whether the device status can be queried at all.
3. **Status staleness**: Whether the last observed status is too old.
4. **Switch failure pattern**: Whether repeated switch attempts have failed.
5. **Unexpected ON**: A device is observed ON when commanded OFF.
6. **Unexpected OFF**: A device is observed OFF when commanded ON.

Health evaluation must NOT create unsafe repeated switching loops. A device flagged
as unhealthy should be excluded from automatic switching until manually cleared
or a configurable cooldown expires.

## Time-of-Day Scheduling Model

Different time periods may have different check intervals and policy behavior:

| Period | Typical hours | Check interval | Behavior |
|---|---|---|---|
| Morning | Sunrise to mid-morning | Moderate (e.g., every 5 min) | Begin evaluating readiness as solar production ramps up |
| Day | Mid-morning to late afternoon | Standard (e.g., every 5–15 min) | Full readiness evaluation, allow discretionary loads |
| Evening | Late afternoon to sunset | Frequent (e.g., every 1 minute) | Protect evening reserve, tighten thresholds, begin conservation |
| Night | Sunset to sunrise | Infrequent (e.g., every 15–30 min) | Minimal checks, all discretionary loads OFF, protect reserve |

Evening requires more frequent checks (for example, every minute) to protect the evening reserve target. Night can be less frequent.

## Seasonal Scheduling Model

Summer and winter may use different profiles:

| Season | Voltage thresholds | Allowed load types | Behavior |
|---|---|---|---|
| Summer | Higher solar production expected | More discretionary loads allowed | Wider windows for load usage |
| Winter | Lower solar production expected | Fewer discretionary loads allowed | Narrower windows, tighter voltage thresholds |
| Transition (spring/autumn) | Moderate production | Balanced | Intermediate thresholds |

Seasonal profiles adjust voltage thresholds, allowed time windows, and device priority
rankings. The specific thresholds will be configurable in a future policy configuration file.

## Weather Forecast Adjustment Model

Weather forecast adjusts the energy-aware control policy:

1. **Sunny forecast**: May allow more energy spending — relaxed voltage thresholds,
   wider time windows, more discretionary loads allowed.
2. **Cloudy/rainy forecast**: Should conserve energy — tightened voltage thresholds,
   narrower windows, discretionary loads skipped.
3. **Temperature influence**: High temperature may increase critical load priority
   (e.g., cooling devices). Low temperature may alter battery reserve targets.
4. **Forecast adjusts voltage thresholds and windows**: Forecast quality maps to
   threshold adjustment coefficients, not binary ON/OFF decisions.
5. **Forecast can skip discretionary devices**: On poor-forecast days, all
   discretionary loads may be excluded from readiness evaluation.
6. **Forecast is advisory input, not hardware execution**: The forecast feeds into
   the policy evaluator; it does not directly switch devices.
7. **Forecast failures fall back conservatively**: If the forecast is unavailable,
   stale beyond a threshold, or fails validation, the policy defaults to conservative
   thresholds (treat as cloudy/rainy).
8. **Poor forecast should protect reserve earlier**: When forecast is poor,
   begin reserve protection well before sunset.

## Battery Reserve Model

The battery reserve model targets an **evening reserve around 26.5V** after sunset.

Key behaviors:
1. Evening reserve around 26.5V after sunset is an important target.
2. The reserve target is configurable and may vary by season.
3. As sunset approaches, voltage thresholds for switch-ON tighten progressively.
4. At sunset, all discretionary loads switch OFF regardless of voltage (unless manually overridden).
5. After sunset, only critical loads may run, and only if voltage remains above the reserve target.
6. Good forecast may allow more daytime energy spending (relaxed reserve protection during day).
7. Poor forecast should protect reserve earlier (begin conservation well before sunset).
8. Overnight reserve protection remains in effect until the next morning.
9. When voltage is well above reserve (e.g., >27V, actively charging), more loads may be allowed
   even in the evening window, subject to forecast and season.

## Per-Device Policy Requirements

Future per-device policy will include these fields. This is a **requirements specification**
only — not a schema implementation in PR 0011.

| Field | Type | Description |
|---|---|---|
| `load_id` | string | References a SwitchableLoad.id |
| `priority` | int | Ordinal priority (lower = higher priority) |
| `critical` | bool | Is this a critical load? (always try to keep ON) |
| `discretionary` | bool | Is this a discretionary load? (can be skipped) |
| `allowed_time_windows` | list[(time, time)] | Time windows when this device may be switched ON |
| `min_voltage_on` | float | Minimum voltage to switch ON |
| `min_voltage_stay` | float | Minimum voltage to stay ON (lower than min_voltage_on for hysteresis) |
| `max_voltage_off` | float | Voltage at or below which device must switch OFF |
| `cooldown_seconds` | int | Minimum seconds between switch actions |
| `readiness_check_interval_s` | int | Seconds between readiness evaluations |
| `health_check_interval_s` | int | Seconds between health evaluations |
| `weather_sensitivity` | float (0.0–1.0) | How much weather forecast influences this device |
| `season_profile` | string | Which seasonal profile to apply |
| `allow_always_on_when_good_conditions` | bool | Keep ON indefinitely when conditions remain good |
| `skip_when_cloudy_or_rainy` | bool | Skip this load when forecast is poor |
| `manual_override_behavior` | string | How manual override interacts with policy |
| `fail_safe_off` | bool | Switch OFF on policy engine failure (default: true) |

This is a requirements specification only. The actual schema, configuration file format,
and runtime policy engine are deferred to future PRs.

## ML Advisory Model

ML serves as **advisory support**, not direct hardware control:

1. ML may predict energy availability (solar production forecast).
2. ML may predict consumption patterns (expected load profiles).
3. ML may recommend voltage thresholds (adaptive threshold tuning).
4. ML may recommend load windows (optimal time windows for each load).
5. **ML advisory must not directly switch devices.** ML output feeds into the
   deterministic policy evaluator, which has final authority.
6. **Deterministic fallback must exist before ML control.** The policy engine
   must always be able to operate without ML input.
7. ML control remains deferred behind separate safety-reviewed gates per ADR-0003.
8. PR 0011 does not modify ML code.

## Safety Boundaries

1. **Manual override must remain possible at all times.** Any automatic policy
   decision can be reversed by manual human action.
2. **Pump automation remains obsolete and disabled by default** from PR 0008.
3. **ML control is deferred** behind safety-reviewed gates per ADR-0003.
4. **Deterministic fallback is required** for all policy decisions — if weather
   forecast or ML advisory is unavailable, the system must fall back to safe
   conservative defaults.
5. **No direct ML-to-hardware path** is permitted. All hardware commands flow
   through the deterministic policy engine.
6. **Fail-safe OFF**: On policy engine failure, all discretionary loads must
   switch OFF (fail closed). Critical loads may remain ON subject to manual review.
7. **External GitOps boundary remains unchanged.** No deployment changes are
   part of the energy-aware control policy.
8. **No automation is enabled by PR 0011.** This is a documentation and static
   validation baseline only.

## Out-of-Scope for PR 0011

The following are explicitly **out of scope** for PR 0011:

- Runtime automation enablement
- Runtime behavior modification
- Policy engine implementation
- Readiness evaluator implementation
- Health evaluator implementation
- Schedule profile implementation
- Weather adjustment evaluator implementation
- ML advisory integration
- ML code modification
- Config file changes
- Runtime data file changes
- Docker or deployment changes
- Hardware wiring of policy decisions
- Pump automation changes
- Manual relay/switch behavior changes
- Tuya behavior changes
- DESS/inverter behavior changes
- Weather runtime behavior changes

## Staged Implementation Roadmap

Energy-aware control policy will be implemented in these stages:

| PR | Stage | Description |
|---|---|---|
| **0011** | Policy requirements | This document — static documentation and validation baseline |
| 0012 | Passive policy domain types | Define PolicyRule, PolicySignal, PolicyDecision domain types |
| 0013 | Static policy configuration | Example policy config file without secrets |
| 0014 | Readiness evaluator | Pure function — computes device readiness from signals |
| 0015 | Health evaluator | Pure function — computes device health from observed state |
| 0016 | Schedule profile model | Time-of-day and seasonal schedule profiles |
| 0017 | Weather adjustment evaluator | Pure function — adjusts thresholds from forecast |
| 0018 | Deterministic decision engine | Policy engine that outputs ControlCommand proposals, no direct hardware execution |
| 0019 | Manual control API | API or command queue for manual operator actions |
| 0020 | Command proposal | Proposed commands before automatic execution (dry-run mode) |
| Later | Controlled execution | Execute proposed commands with safety gates |
| Later | ML advisory | ML suggests thresholds/windows to policy engine; engine retains final authority |
| Much later | ML control | ML directly influences control decisions — only after separate safety-reviewed approval per ADR-0003 |

## Validation and Rollout Notes

1. Each stage in the roadmap must include its own static validation script.
2. Static validation scripts must be read-only, require no network access, no secrets,
   no Docker Hub, no GitHub API, no Kubernetes, no ArgoCD, no Tuya, no DESS.
3. Each stage must not enable runtime automation until explicitly planned.
4. ML control remains gated behind ADR-0003 safety review.
5. The policy document may be updated as requirements evolve, but updates must go
   through the same plan-review-implement cycle.
6. All future policy runtime code must be covered by the existing repository safety
   and validation scripts (check-repo-safety.sh, check-project-memory.sh, validate-yaml.py, etc.).
