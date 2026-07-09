# Platform Control Redesign Strategy

## Status

Accepted — PR 0007. Strategy and inventory only. No implementation.

## 1. Background

The dessmonitor platform was originally designed for a pond monitoring and automation system
that included a physical water pump (pond pump). The pump was controlled via Tuya IoT relays
with variable speed (`P` value 0-100), voltage-driven speed adjustment, temperature-based
minimum speed rules, and pump-specific automation loops.

**The physical pump / water pump no longer exists.**

Pump-specific control logic is now obsolete. However, the platform must retain **generic
ON/OFF control capability** for switchable loads (lights, filters, heaters, future devices).

## 2. Principle: Do Not Rewrite From Zero

A full rewrite is not desired. The existing platform has substantial value:

- **DESS/inverter telemetry** — reliable data collection pipeline for solar/battery metrics.
- **Tuya relay adapter** — proven IoT control layer for physical switches.
- **RelayChannelDevice** — low-level device representation with status tracking,
  power consumption calculation, uptime tracking, and tick/energy accounting.
- **Validation/CI/project-memory** — comprehensive governance and quality gates
  (PR 0001-0006).
- **Docker image publishing** — stable GitHub Actions → Docker Hub → ArgoCD pipeline
  (image publishing only; real GitOps deployment lives in an external GitOps repository).
- **Weather and sensor telemetry** — OpenWeather integration, temperature/humidity
  monitoring, shared_state pattern.
- **Accumulated historical data** — SQLite, CSV, JSONL data stores with months of
  solar/battery/weather/device telemetry.

The migration strategy is to **preserve the useful skeleton, isolate the obsolete pump
coupling, and introduce generic abstractions** in staged PRs.

## 3. Inventory: Pump-Coupled Areas

Each area is classified by coupling type.

### 3.1 Active Runtime Coupling

These areas execute pump logic during normal application runtime:

| Area | File(s) | Coupling | Classification |
|---|---|---|---|
| `pump_int` startup interval | `run.py` line ~68 | `SmartHomeController(..., pump_int=120)` starts `_pump_loop` unconditionally | active runtime |
| `_pump_loop` | `app/service/smart_home_controller.py` | async loop that queries pump devices, adjusts speed via `PondPumpController` | active runtime |
| `PondPumpController` | `app/devices/pond_pump_controller.py` | full voltage/temperature-based speed decision logic | active runtime |
| `device_type == "pump"` | `app/device_initializer.py` | `_process_single_device` special-cases `"pump"` → sets `control_key="P"`, `state_key="Power"` | active runtime |
| `pump_mode` in shared_state | `app/tuya/status_updater_async.py` | reads `mode` from Tuya status, writes `pump_mode` to shared_state | active runtime |
| `SmartHomeController.__init__` | `app/service/smart_home_controller.py` | instantiates `PondPumpController`, creates `_pump_loop` task | active runtime |

### 3.2 Legacy/Inactive Coupling

These areas reference pump concepts but are not actively used in the running application:

| Area | File(s) | Coupling | Classification |
|---|---|---|---|
| PumpPreset enum | `app/service/smart_home_controller.py` | `STRICT=1, SUMMER=4, WINTER=5, AUTO=6` — used in pump loop | active (via `_pump_loop`) |
| PRESET_DESCR | `app/devices/pump_power_map.py` | human-readable descriptions for pump presets | documentation, active |
| PUMP_W_MAP | `app/devices/pump_power_map.py` | P-speed → watt mapping table | data, active (via `RelayChannelDevice._pump_w_from_p`) |
| TEMP_SPEED_MAP | `app/devices/pump_power_map.py` | temperature → min speed table | data, active (via `PondPumpController`) |

### 3.3 Documentation-Only Coupling

| Area | File(s) | Coupling | Classification |
|---|---|---|---|
| ML README pump fields | `app/ml/README.md` | documents `pump_speed`, `pump_mode`, `pump_uptime_today_sec` in data schema | documentation |

### 3.4 Data/ML Coupling

| Area | File(s) | Coupling | Classification |
|---|---|---|---|
| `pump_speed` in MLDataPoint | `app/ml/ml_data_collector.py` | field in ML data point dataclass | data/ML |
| `pump_mode` in MLDataPoint | `app/ml/ml_data_collector.py` | field in ML data point dataclass | data/ML |
| `pump_uptime_today_sec` in MLDataPoint | `app/ml/ml_data_collector.py` | field in ML data point dataclass | data/ML |
| `pump_current_uptime_sec` in MLDataPoint | `app/ml/ml_data_collector.py` | field in ML data point dataclass | data/ML |
| `optimal_pump_speed` in MLDataPoint | `app/ml/ml_data_collector.py` | target label in ML data point | data/ML |
| `pump_controller` ML training | `app/ml/ml_model_training_example.py` | `train_pump_controller()`, `predict_optimal_pump_speed()` | data/ML |
| `RelayChannelDevice._pump_w_from_p` | `app/devices/relay_channel_device.py` | power consumption calculation for pump type | active runtime |
| `RelayChannelDevice.power_consumption()` | `app/devices/relay_channel_device.py` | dispatches to `_pump_w_from_p` when `device_type == "pump"` | active runtime |

### 3.5 Configuration Coupling

| Area | File(s) | Coupling | Classification |
|---|---|---|---|
| `devices.yaml` | `devices.yaml` (gitignored) | device entries with `device_type: pump` and pump-specific extra fields | config |
| Pump-specific extra fields | `devices.yaml` | `weather`, `min_speed`, `max_speed`, `speed_step`, `p_code`, `mode_code` | config |

### 3.6 Monitoring Coupling

| Area | File(s) | Coupling | Classification |
|---|---|---|---|
| PUMP log handling | `app/monitoring/device_status_logger.py` | `_handle_pump()` method, `Power`, `P` status display | active runtime |
| Pump monitoring | `app/monitoring/device_status_logger.py` | pump state diff detection, last_pump_state tracking | active runtime |
| Pump watt display | `app/monitoring/device_status_logger.py` | `mode_name()` with pump-specific mode mapping | active runtime |

## 4. Target Architecture

The future platform should expose these domain abstractions, layered to decouple control
logic from hardware specifics.

### 4.1 Core Domain Types

| Type | Purpose |
|---|---|
| `SwitchableLoad` | A device that can be ON or OFF, optionally with a numeric set-point. Replaces `RelayChannelDevice` in control logic. |
| `ControlCommand` | A command to set a load to a desired state: `{load_id, desired_state, source, timestamp}`. |
| `ControlState` | The current state of a load: `{load_id, is_on, setpoint, source, last_updated}`. |
| `ObservedState` | A telemetry reading from a sensor or device: `{load_id, metric, value, unit, timestamp}`. |
| `DesiredState` | The target state computed by policy or manual command: `{load_id, target_on, target_setpoint, reason}`. |
| `CommandResult` | Outcome of executing a command: `{command_id, success, error, actual_state, timestamp}`. |
| `TelemetryPoint` | A timestamped metric reading from any source. |
| `PolicyDecision` | A decision produced by a policy engine: `{load_id, recommended_state, priority, reason}`. |

### 4.2 Adapter Layer

| Adapter | Responsibility |
|---|---|
| **Tuya adapter** | Translates `ControlCommand` → Tuya API calls. Translates Tuya device status → `ControlState`. |
| **DESS/inverter telemetry adapter** | Polls Dess API, translates raw inverter data → `ObservedState` / `TelemetryPoint`. |
| **Weather adapter** | Reads OpenWeather API, translates → `ObservedState` (temperature, humidity, forecast). |
| **Storage/data adapter** | Persists `TelemetryPoint`, `ControlState`, `CommandResult` to SQLite/TimescaleDB. |
| **Future UI/API adapter** | Exposes control surface via HTTP API, WebSocket, or MQTT. |

### 4.3 Service Layer

| Service | Responsibility |
|---|---|
| **Telemetry collection service** | Periodically gathers `ObservedState` from all adapters, writes to storage. |
| **Manual control service** | Accepts `ControlCommand` from human operators (UI/API), validates, dispatches to adapter. |
| **Device registry service** | Maintains mapping from load names/IDs to adapter-specific device configurations. |
| **Policy evaluation service** (later) | Evaluates rules against `ObservedState`, produces `PolicyDecision`. |
| **ML advisory service** (later) | Produces advisory `PolicyDecision` from ML models. |

### 4.4 Safety Boundaries

- **Core domain types must not know Tuya command keys, DESS raw formats, or OpenWeather raw formats.**
- **Core domain types must not depend on `pump` or any device-specific type.**
- **Policy must not directly call hardware.** Policy produces `PolicyDecision`; the control service executes.
- **ML must not directly control hardware.** ML produces `PolicyDecision` in advisory mode; human or policy gate must approve.
- **ML control must not be enabled without safety policy, shadow/advisory mode, and fallback mechanism** (per ADR-0003).

## 5. Migration Roadmap

### PR 0007 (this PR) — Strategy and Inventory

- [x] Document platform control redesign strategy.
- [x] Inventory pump-coupled areas.
- [x] Define target architecture.
- [x] Define staged migration plan.
- [x] Add static validation for strategy document.

**Risk:** None — documentation only.

**Files:** `.project-memory/PLATFORM_CONTROL_REDESIGN.md`, `scripts/check-platform-control-redesign.sh`,
`.project-memory/CURRENT_STATE.md`, `.project-memory/ROADMAP.md`, `.github/workflows/validate.yml`.

**Runtime behavior changes:** None.

---

### PR 0008 — Disable Pump Automation, Preserve Manual Switch Control

**Goal:** Stop active pump-specific automation from running by default while keeping
manual relay/switch ON/OFF capability fully functional.

**Likely changes:**
1. Gate `_pump_loop` creation behind an opt-in environment flag (`PUMP_AUTOMATION_ENABLED=false` default).
2. Preserve `_switch_loop` and all relay ON/OFF logic unchanged.
3. Preserve Tuya status updater (pump mode sync can be disabled when no pump configured).
4. Add validation that active pump loop does not start by default.
5. Update `CURRENT_STATE.md`.

**Risk:** Low — only gating, no logic modification. Manual switch control is exercised by
`_switch_loop` which is completely independent of the pump loop.

**Validation:** Confirm `_pump_loop` task is not created when flag is false.
Confirm `_switch_loop` still runs and toggles relays.
Confirm existing validation suite passes.

**Runtime behavior changes:** Pump automation stops by default. Manual switch control unchanged.

---

### PR 0009 — Introduce Generic Control Domain Types

**Goal:** Define `SwitchableLoad`, `ControlCommand`, `ControlState` and related types in a
new `app/control/` or `app/domain/` package, without migrating any existing code.

**Likely changes:**
1. Create `app/domain/` package.
2. Define `SwitchableLoad`, `ControlCommand`, `ControlState`, `ObservedState`, `DesiredState`,
   `CommandResult`, `TelemetryPoint`, `PolicyDecision` as dataclasses or simple classes.
3. No migration of existing code yet.
4. Add unit tests for domain types.

**Risk:** Low — greenfield types with no dependencies on existing code.

**Validation:** Module import succeeds. Tests pass.

**Runtime behavior changes:** None.

---

### PR 0010 — Backward-Compatible Adapter From Old Config to SwitchableLoad

**Goal:** Add a compatibility layer that reads existing `devices.yaml` Tuya device configs and
presents them as `SwitchableLoad` instances, without changing `RelayChannelDevice`.

**Likely changes:**
1. Add adapter function `devices_to_switchable_loads()` that reads `DeviceInitializer` devices.
2. Map `control_key` to `SwitchableLoad.control_key`, `device_type` to generic load type.
3. Preserve pump-specific fields as legacy annotations.

**Risk:** Low — adapter is additive, existing code path unchanged.

**Validation:** Adapter produces correct `SwitchableLoad` list for existing configs.
Existing device initialization unchanged.

**Runtime behavior changes:** None.

---

### PR 0011 — Migrate Service Layer From Pump Loop to Generic Control Service

**Goal:** Replace `_pump_loop` with a generic `_control_loop` that operates on `SwitchableLoad`
instances and uses `ControlCommand` to issue state changes.

**Likely changes:**
1. Replace `_pump_loop` with `_control_loop` that iterates `SwitchableLoad` instances.
2. For loads with numeric setpoints: use voltage or policy-based adjustment (like old pump logic
   but generic).
3. Retire `PondPumpController`.
4. Remove `pump_int` startup parameter.

**Risk:** Medium — requires careful state management to avoid toggling relays unintentionally.

**Validation:** All existing validation passes. Manual ON/OFF control still works.
Generic setpoint adjustment works for any load type.

**Runtime behavior changes:** Pump-specific automation stops. Generic load control starts.
Backward-compatible: non-pump switches are unaffected.

---

### PR 0012 — Migrate Monitoring Labels From PUMP to Generic Controllable Load

**Goal:** Update `device_status_logger.py` to remove pump-specific monitoring paths and use
generic load display logic.

**Likely changes:**
1. Remove `_handle_pump` method.
2. Remove `PUMP` log entries.
3. Add generic load monitoring that reads `SwitchableLoad` state.
4. Remove `ANALOG_TYPES` special-casing or make it config-driven.

**Risk:** Low — monitoring is read-only.

**Validation:** Status log output changes format but still displays all loads correctly.

**Runtime behavior changes:** Status log format changes for former pump devices.
No control logic impact.

---

### PR 0013 — Migrate Data Collection to Generic Telemetry Fields

**Goal:** Update `MLDataCollector` to emit generic telemetry fields (`load_on_count`, `load_power_w`)
instead of pump-specific fields (`pump_speed`, `pump_mode`, `pump_uptime_today_sec`).

**Likely changes:**
1. Add generic fields to `MLDataPoint`: `load_count`, `load_power_w`, `numeric_setpoint`, etc.
2. Map legacy pump fields to `None` or derive from generic load query.
3. Preserve old pump history in existing SQLite/CSV/JSONL data (legacy format, not migrated).

**Risk:** Low — additive fields, old fields depopulated but schema unchanged.

**Validation:** Data collector emits new fields. Old pump fields are `None` (or absent).
Historical data in storage unchanged.

**Runtime behavior changes:** Data schema changes for new records. Old records preserved.

---

### PR 0014 — Backend Manual ON/OFF API

**Goal:** Expose a simple HTTP API or internal interface to toggle any `SwitchableLoad`
ON/OFF, bypassing automation policies.

**Likely changes:**
1. Add API endpoint (e.g., Flask route) or internal service method.
2. Implement authorization/validation.
3. Wire through `ControlCommand` → Tuya adapter.
4. Add test coverage.

**Risk:** Low-Medium — depends on the existing Flask API surface.

**Validation:** API can toggle any configured load. Existing automation does not interfere.

**Runtime behavior changes:** New API surface added. Existing control loop unchanged.

---

### PR 0015+ — UI/UX Control Panel (Planning)

**Goal:** Design and plan a user interface for manual control, scheduling, and monitoring.
Implementation later.

---

### Later — Policy Layer

**Goal:** Rules-based automation engine operating on `ObservedState` → `PolicyDecision`.
Not before PR 0016.

---

### Later — ML Advisory

**Goal:** ML models produce advisory `PolicyDecision` in shadow mode.
Requires safety policy, shadow mode, and operator approval gate (per ADR-0003).

---

### Much Later — ML Control

**Goal:** ML models produce control decisions after safety-reviewed gates.
Requires:
- Demonstrated safety policy.
- Demonstrated fallback mechanism.
- Demonstrated shadow/advisory mode in production.
- Human operator confidence established over months.

## 6. Immediate Next PR

**PR 0008: `0008-disable-pump-automation-and-preserve-manual-switch-control`**

Scope:
1. Stop active pump-specific automation from running by default (`PUMP_AUTOMATION_ENABLED=false`).
2. Preserve manual relay/switch ON/OFF capability (`_switch_loop` unchanged).
3. Avoid config file commits with secrets.
4. Avoid broad refactor.
5. Avoid ML.
6. Avoid data schema migration.
7. Add validation that active pump loop does not start by default.
8. Update `CURRENT_STATE.md`.
