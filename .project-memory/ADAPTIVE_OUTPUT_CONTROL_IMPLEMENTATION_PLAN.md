# Adaptive Output-Source Control — Implementation Plan

**Project:** `alexcopy/dessmonitor`  
**Date:** 2026-11-02  
**Status:** PROPOSED — review gate before implementation  
**Branch:** `feat/adaptive-output-control`  
**Design authority:** `.project-memory/ADAPTIVE_OUTPUT_CONTROL_DESIGN.md`

## Objective

Reduce frequent observed `Line Mode` ↔ `Invert Mode` transitions while protecting the battery and preserving useful solar operation. Decisions must account for **measured total inverter output demand**, measured PV, measured battery current/voltage behavior, time remaining before sunset, and input freshness. Tuya device totals are **not** a substitute for household load (e.g., 30 W of relays vs 2 kW through the inverter).

Two independent windows must be evaluated:

1. **Solar opportunity window:** actual sunrise/sunset, remaining daylight, measured PV trend and fresh forecast; never solely hard-coded summer/winter clock hours.
2. **Output eligibility/hysteresis window:** observed inverter `Line`/`Invert` state, battery voltage under load, observed rapid sag, guarded thresholds, dwell/cooldown, and return-to-battery eligibility.

**Current safety boundary:** no verified API for changing inverter output-source thresholds has been established. All new adaptive-output logic starts as **shadow/advisory only**. It must send zero DESS output-source writes and zero Tuya commands. Existing emergency charger control is independent.

## Before phase work: protect unfinished Controls diagnostics

The prior Coder task implementing observed output-mode diagnostics was reported **uncommitted**. Preserve it. From the current working tree on the latest intended base:

1. Inspect `git status --short`, `git branch --show-current`, and `git diff --stat`; confirm the base branch and identify unrelated edits.
2. Create `feat/adaptive-output-control` **before staging**. A new branch at the current HEAD retains unstaged modifications; `git stash` is unnecessary.
3. Review the existing diagnostic diff first, including schema changes, `output_priority_raw`, `mains_status`, observed transition logic and data gaps. Do not commit until that diff is acceptable.
4. Stage **only** the prior diagnostic files, then commit them as a separately identifiable **diagnostics checkpoint**. Do not use `git add .` or amend a different change. If unresolved issues remain, do not commit; report them.
5. Add the approved design and implementation plan as Markdown files in `.project-memory/`, in a **separate commit** once reviewed. Keep production configuration, tokens, secrets, and runtime logs out of Project Memory.
6. Do not push unless explicitly instructed.

Suggested diagnostic checkpoint message: `feat(controls): diagnose observed output-mode transitions`.

## Phase 0 — inspect running architecture and hardware capability (read-only)

### Repository audit findings — 2026-11-02

The current production wiring is:

1. `run.py` creates `DeviceInitializer`, authenticates DESS, starts
   `InverterMonitor` at a 60-second poll interval, and attaches both
   `BatteryChargeController` and `SolarAwareController` to that monitor.
2. `InverterMonitor` fetches `DeviceData`, updates `shared_state`, then calls
   `SolarAwareController.tick()` followed by
   `BatteryChargeController.tick()` on each successful poll.
3. `StartupResetCoordinator` performs its existing startup Tuya relay reset
   before the control gate opens. This is existing load-reset behavior, not
   output-source control.
4. `SmartHomeController` is started, but switch automation is explicitly
   disabled in `run.py`. Pump automation is separately opt-in through
   `PUMP_AUTOMATION_ENABLED`; when enabled it can issue Tuya numeric pump
   commands.

The active command writers are therefore separate:

- `BatteryChargeController` is the only verified DESS writer in this audit. It
  writes charger source through `DessAPI.set_charger_source()` using the
  verified `bat_charger_source_priority` field and may assert the verified
  `eybond_ctrl_5` 2 A charging-current setting. It does not write inverter
  output-source thresholds.
- `SolarAwareController` calls `RelayTuyaController` to switch configured
  managed loads. Its `min_volt`/`max_volt` and coefficient overrides are relay
  thresholds, not inverter Line/Invert thresholds. Its existing hard floor and
  `26.5 V` sunset target must not be treated as inverter hardware baselines.
- `SmartHomeController` is not an active automatic switch writer in the
  default runtime because `switch_automation_enabled=False`; its optional pump
  loop remains a separate Tuya writer when explicitly enabled.

Telemetry coverage and freshness are currently limited as follows:

- DESS parsing verifies `working_state`, active `output_power`, PV power,
  battery voltage, charge/discharge currents, `mains_status`, charger source,
  and raw `output_priority` as read fields. The previous diagnostics checkpoint
  persists the relevant observations in `inverter_metrics`.
- The inverter poll is nominally every 60 seconds, but `shared_state` does not
  attach per-field observation ages or a freshness classification. The
  Timescale collector uses adaptive intervals (normally 120 seconds in inverter
  mode, 1800 seconds in grid mode, and 10 seconds during its own detected
  switching mode), so replay must preserve gaps and avoid assuming a uniform
  sample cadence.
- `OpenWeatherService` refreshes about every 600 seconds when enabled. It stores
  the raw hourly forecast, source, and a direct cloud value, but no explicit
  forecast timestamp/freshness field in `shared_state`. Forecast entries expose
  `dt`, clouds, precipitation probability/rain where supplied by OpenWeather;
  irradiance is not normalized by this repository. Sunrise and sunset are
  reduced to integer local hours, so current runtime precision is insufficient
  for the minute-resolution design until a read-only time model is added.
- The current `devices.yaml` contains `tuya:` and `devices:`. A
  `charge_policy:` section is optional today: `BatteryChargeController` loads
  it when present and otherwise applies validated defaults. There is currently
  no `output_source_policy` parser or runtime section.

Capability conclusion:

- `DessAPI.ctrl_device()` is a generic authenticated DESS request method, but
  its existence is not evidence that an output-source threshold field is valid
  for this inverter. The only device-specific write mappings verified in code
  are the charger-source field and 2 A current field above.
- `output_priority` is currently read from telemetry only. No dedicated,
  device-specific readback or write mapping for output thresholds, return to
  battery, return to grid, or output-source priority has been verified.
- Inverter output-source control capability is therefore **UNKNOWN**, and all
  Phase 1 decisions must remain pure, read-only shadow/advisory results.

The Phase 1 file boundary is consequently limited to a pure evaluator and
validated configuration/input models under `app/control/`, plus focused tests.
It must not be wired into `run.py`, `InverterMonitor`, either existing command
writer, the DESS API, or the Tuya controller. Historical replay should consume
the existing persisted inverter observations and explicitly report missing
freshness, time precision, forecast, and hardware-capability evidence as
`UNKNOWN`.

**Review actual HEAD and effective runtime wiring:**

- `run.py`: `InverterMonitor`, `SolarAwareController`, `BatteryChargeController`, disabled `SmartHomeController` switch automation.
- `app/service/solar_aware_controller.py` and `app/tuya/relay_tuya_controller.py`: load thresholds, command paths, status freshness, mode interpretation, cooldowns.
- `app/service/battery_charge_controller.py` and `app/api.py`: charger-source commands **versus** output-source capabilities; do not conflate them.
- `service/inverter_monitor.py`, `app/ml/timescale_data_collector.py`, `/api/charge/history`: actual field names, timestamps, data freshness, observed output mode and charger mode.
- `app/weather/openweather_service.py`: sunrise/sunset precision (currently hours are truncated), hourly forecast timestamps and freshness.
- `app/device_initializer.py` and the real configuration schema: `devices:`, `charge_policy:`, their save/backup semantics.

**Hardware prerequisite list (do not invent):** inverter manufacturer/model/firmware; battery chemistry and BMS manufacturer limits; real inverter return-to-grid/return-to-battery parameters and their readback; ability to control them via a documented safe API, if any; installation timezone; whether `output_power_w` covers the relevant output circuits.

**Deliverable:** short section in this plan: verified capabilities, unknowns, file-level impact map, risk and rollback strategy. No live writes.

**Gate 0:** maintain the distinction between charger priority, inverter output priority, observed working mode and managed load relays. Report missing device-specific information as `UNKNOWN`, never infer field IDs or safe voltage limits.

## Phase 1 — pure deterministic shadow evaluator (first code milestone)

Define a pure, side-effect-free decision interface, for example:

```python
# Illustrative contract only; final types and module layout follow repo conventions.
evaluate_output_policy(snapshot, baseline, recent_history) -> OutputPolicyDecision
```

Inputs include timestamp/freshness, observed `working_mode`, `output_power_w`, `pv_power_w`, `battery_voltage`, charge/discharge current, raw/known `output_priority`, minute-precision sunrise/sunset, forecast freshness and recent comparable-load battery-mode history. Battery SOC is *reported telemetry*, not an authoritative eligibility signal.

The evaluator must:

- classify data quality (`FRESH`, `STALE`, `UNKNOWN`), recognizing null distinctly from zero;
- classify solar opportunity and hours until sunset without relying solely on hard-coded seasons;
- use inverter-measured output watts as demand, not summing managed Tuya loads;
- detect a rapid voltage sag from several **comparable** `Invert Mode` samples, allowing for settling after mode changes;
- refuse an optimistic return-to-battery recommendation when measured demand is high, PV is weak, history shows a failed short trial, or inputs are unreliable;
- apply separate return-to-battery and exit-battery reasoning, plus dwell/cooldown/anti-flap guards;
- keep immutable baseline parameters separate from bounded, expiring proposed effective settings;
- return `HOLD`/`ADVISE`/`BLOCK` and stable reason codes, with a snapshot reference and evidence; **never execute commands**.

Use the proposed `output_source_policy` section of the **same `devices.yaml`**, next to `devices:` and `charge_policy:`, only with safe validation and preserved unrelated sections. Default `mode: observe`, `allow_output_writes: false`, and unverified inverter baseline voltages `null`. Do not invent 25.5/26.5/27–29 V as safe device-specific values.

**Gate 1:** unit tests demonstrate zero hardware calls, deterministic decisions, valid null/freshness handling, restart-safe read of baseline YAML, and refusal of an unsafe 2 kW return-to-battery trial even if managed Tuya loads total 30 W.

## Phase 2 — historical replay and transparent decision telemetry

Replay the retained inverter observations for 7–30 days where fields are available. For each candidate decision, retain: UTC time, measured inputs, source and freshness, baseline, temporary recommendation, state/lockout, reason, and whether it would have avoided a short battery trial. Preserve gaps; do **not** fabricate missing mode transitions or energy.

Keep these two event streams separate:

1. **Our decisions/requests:** proposed/blocked action, reason, actual command (if any), readback and command outcome.
2. **Physical observations:** first sample where `working_mode` changed; no unwarranted attribution to the application or operator.

If no matching command is known, origin is `unknown_external_or_device`. A recommendation is never recorded as an executed switch.

**Gate 2:** replay demonstrates no output writes, documented measurement coverage and quantitative anti-flap effect, with explicit unknown/inconclusive samples. No live control yet.

## Phase 3 — optional interventions and integration review

Only consider selective Tuya shedding when **observed** controllable demand is material to total output and the device is explicitly discretionary. Critical pond life-support remains protected. Check for competing control writers (`SolarAwareController`, overrides and manual controls); do not introduce a second writer without arbitration.

**Gate 3:** operator sign-off per device, safe observation/readback, cooldown and clear rollback. Skip this phase if the house demand is mostly uncontrollable.

## Phase 4 — hardware-specific inverter output control (separate approval)

**NOT APPROVED BY THIS PLAN.** Requires documented exact DESS/inverter controls, voltage range constraints derived from the real battery/BMS and firmware, confirmed read-before-write/readback, active-controller arbitration, write rate limits, manual override, permanent anti-flap state and a tested rollback/reconciliation path. Review scope and request explicit permission before any real output-source commands.

**Gate 4:** independent hardware-specific safety review plus controlled deployment. A missing capability means **remain advisory**, not an invented API workaround.

## Baseline, effective settings, restart

- Persist **operator-approved baselines** in `devices.yaml` under a distinct `output_source_policy` section. Never rewrite baselines with adaptive proposals.
- Compute bounded effective recommendations in memory with expiry, policy revision, provenance and reason. Reset/re-evaluate at noon and before sunset **without** blindly sending a write.
- Restart must recover the same YAML baseline. If a future active implementation needs command cooldown/history across restart, use a separate durable operational state journal; Project Memory holds **documentation only**, not mutable runtime state.
- Existing `charge_policy` emergency recovery stays independent and unchanged unless a specifically approved integration fix is necessary.

## Critical acceptance scenarios

| Scenario | Expected shadow result |
|---|---|
| 2,000 W inverter output, 100 W PV, 30 W managed Tuya devices | Treat demand as ~2 kW **measured inverter output**; no optimistic battery return based on Tuya totals |
| `Invert Mode` ends after a 5-minute loaded-voltage collapse | Failed trial, longer mains hold/lockout; do not lower protective discharge floor |
| Sunrise/sunset known, productive day and stable PV | Battery candidate only if load, battery evidence and cooldown also permit |
| Winter/late afternoon, weak PV, little daylight remaining | Preserve reserve; avoid risky voluntary return |
| `Line Mode` instantly rebounds battery voltage | Do not interpret rebound as energy replenishment |
| Reported SOC jumps 70% ↔ 100% | Display raw SOC; no switching decision on SOC jump alone |
| Stale current/mode/voltage telemetry | `HOLD`/`UNKNOWN`; no optimistic new command |
| Mode changes without a logged app command | Record observed change; origin unknown, not “controller switched” |
| Restart | Baselines intact; expired proposal ignored; no output writes |

## Files and change boundaries

Keep Phase 1 narrowly scoped. Likely inspect/change `app/control/` for a pure evaluator, `app/device_initializer.py` for validated policy read (only when needed), and an existing read-only integration boundary if chosen. Avoid unrelated Controls redesign, DESS authentication, Tuya refactor, charge-controller changes, or new migration tables unless needed for an approved audit requirement.

**Validation before review:** focused pure unit tests, Python compilation, current smoke checks, `git diff --check`; no live inverter commands, no production DB mutation, no commits/push except the explicitly reviewed diagnostic/document checkpoint.

**Planning gate:** before implementation, confirm the audit, file map, proposed decision state machine, replay input fidelity, numeric safety constraints and defaults. Unknown hardware capabilities stay unknown, and active output control stays disabled.
