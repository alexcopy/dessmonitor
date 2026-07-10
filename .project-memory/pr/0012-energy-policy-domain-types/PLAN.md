# PR 0012 — Passive Energy Policy Domain Types

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `8d45a583c2a55c6f37d3211599932f8f9f1db433` |
| Branch | `git branch --show-current` | `0012-energy-policy-domain-types` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0012-energy-policy-domain-types` and working tree is clean.

## 2. Purpose

PR 0012 follows PR 0011 (energy-aware control policy requirements) and introduces passive
energy policy domain types. These types define the vocabulary for power source, time-of-day
scheduling, seasonal profiles, weather conditions, device policy, readiness, health, and
policy decisions — without evaluating or executing any policy.

The types are passive data definitions only. They are NOT wired into any runtime component
(`SmartHomeController`, `RelayTuyaController`, `DeviceInitializer`, weather, ML, monitoring)
in PR 0012.

## 3. Product Context

1. The project's core purpose is energy-aware device control.
2. The system should switch devices ON when energy conditions are favorable (high voltage,
   solar generation, sunny forecast).
3. The system should switch devices OFF when voltage falls or grid/mains operation indicates
   conservation.
4. Device readiness must be checked before switching ON.
5. Device health must be checked periodically and after switching.
6. Weather forecast, season, time of day, and later ML advisory can adjust policy.
7. Evening battery reserve (~26.5V after sunset) is an important target.
8. ML remains advisory-only and must not directly control hardware.
9. Pump automation remains obsolete and disabled by default (per PR 0008).
10. Manual relay/switch ON/OFF remains available (per PR 0008).

## 4. Current Repository State

| Capability | File | Status |
|---|---|---|
| Generic control domain types | `app/control/domain.py` | Passive (PR 0009) |
| Relay-to-SwitchableLoad mapping | `app/control/relay_mapping.py` | Passive (PR 0010) |
| Energy-aware control requirements | `.project-memory/ENERGY_AWARE_CONTROL_POLICY.md` | Documented (PR 0011) |
| SwitchableLoad, ControlCommand, etc. | `app/control/__init__.py` | Passive |
| Battery voltage telemetry | `app/api.py` → `shared_state` | Active |
| Weather forecast | `app/weather/openweather_service.py` → `shared_state` | Active |
| Manual switch control | `RelayTuyaController`, `_switch_loop` | Active |
| Pump automation | gated by `PUMP_AUTOMATION_ENABLED` (default disabled) | Disabled |
| **Energy policy domain types** | `app/control/energy_policy.py` | **This PR** |

## 5. Required Type Vocabulary (17 Types)

The module `app/control/energy_policy.py` must define these 17 types:

| # | Type | Kind | Purpose |
|---|---|---|---|
| 1 | `PowerSource` | `Enum` | `SOLAR`, `BATTERY`, `GRID`, `MAINS`, `NETWORK`, `UNKNOWN` |
| 2 | `TimeOfDay` | `Enum` | `MORNING`, `DAY`, `EVENING`, `NIGHT`, `UNKNOWN` |
| 3 | `Season` | `Enum` | `SPRING`, `SUMMER`, `AUTUMN`, `WINTER`, `UNKNOWN` |
| 4 | `WeatherCondition` | `Enum` | `SUNNY`, `CLOUDY`, `RAINY`, `SNOWY`, `STORM`, `UNKNOWN` |
| 5 | `LoadClass` | `Enum` | `CRITICAL`, `DISCRETIONARY` |
| 6 | `DevicePriority` | `Enum` | `HIGH`, `NORMAL`, `LOW` |
| 7 | `VoltageSnapshot` | `@dataclass` | Current voltage, optional trend, optional timestamp, optional charging/discharging state, optional power source |
| 8 | `WeatherForecastSignal` | `@dataclass` | Forecast condition, optional temperature, optional confidence, optional expected solar opportunity |
| 9 | `BatteryReservePolicy` | `@dataclass` | Evening reserve target voltage (default may reference 26.5V), configurable |
| 10 | `DeviceEnergyPolicy` | `@dataclass` | Per-device policy: load id, priority, critical/discretionary, allowed time windows, voltage thresholds (min_on, min_stay_on, off), cooldown, intervals, weather sensitivity, season profile, allow_always_on, skip_when_cloudy_or_rainy, manual override, fail-safe off |
| 11 | `ReadinessInput` | `@dataclass` | Context needed to determine if a device may be switched ON (no hardware action) |
| 12 | `ReadinessResult` | `@dataclass` | `READY` / `NOT_READY` with reason (no hardware action) |
| 13 | `HealthInput` | `@dataclass` | Expected state, observed state, status age, failure count |
| 14 | `HealthStatus` | `Enum` | `HEALTHY`, `STALE`, `MISMATCH`, `UNREACHABLE`, `UNKNOWN` |
| 15 | `HealthCheckResult` | `@dataclass` | Health status, reason, recommended follow-up (no switching) |
| 16 | `EnergyPolicyContext` | `@dataclass` | Combined voltage/weather/time/season/reserve context (passive data) |
| 17 | `EnergyPolicyDecision` | `Enum` | `ALLOW_ON`, `PREFER_OFF`, `FORCE_OFF`, `HOLD`, `NO_ACTION` |

## 6. Detailed Type Semantics

### 6.1 Enums

**`PowerSource`**: Represents where the system's energy is coming from.
```python
class PowerSource(Enum):
    SOLAR = "solar"
    BATTERY = "battery"
    GRID = "grid"
    MAINS = "mains"       # synonym for grid/network power
    NETWORK = "network"
    UNKNOWN = "unknown"
```

**`TimeOfDay`**: Represents a time slot for scheduling.
```python
class TimeOfDay(Enum):
    MORNING = "morning"   # e.g., 06:00-12:00
    DAY = "day"           # e.g., 12:00-18:00
    EVENING = "evening"   # e.g., 18:00-22:00
    NIGHT = "night"       # e.g., 22:00-06:00
    UNKNOWN = "unknown"
```

**`Season`**: Seasonal profile selector.
```python
class Season(Enum):
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"
    UNKNOWN = "unknown"
```

**`WeatherCondition`**: Simplified weather classification for policy adjustment.
```python
class WeatherCondition(Enum):
    SUNNY = "sunny"
    CLOUDY = "cloudy"
    RAINY = "rainy"
    SNOWY = "snowy"
    STORM = "storm"
    UNKNOWN = "unknown"
```

**`LoadClass`**: Whether a load is critical (must never auto-OFF) or discretionary.
```python
class LoadClass(Enum):
    CRITICAL = "critical"
    DISCRETIONARY = "discretionary"
```

**`DevicePriority`**: Priority level for load shedding and allocation.
```python
class DevicePriority(Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
```

**`HealthStatus`**: Result of a health check.
```python
class HealthStatus(Enum):
    HEALTHY = "healthy"
    STALE = "stale"          # status too old
    MISMATCH = "mismatch"    # observed != expected
    UNREACHABLE = "unreachable"  # device not responding
    UNKNOWN = "unknown"
```

**`EnergyPolicyDecision`**: Policy outcome vocabulary.
```python
class EnergyPolicyDecision(Enum):
    ALLOW_ON = "allow_on"       # conditions favorable, may switch ON
    PREFER_OFF = "prefer_off"   # conditions marginal, prefer OFF
    FORCE_OFF = "force_off"     # immediate switch OFF recommended
    HOLD = "hold"               # keep current state, no change
    NO_ACTION = "no_action"     # no policy input, no recommendation
```

### 6.2 Dataclasses

**`VoltageSnapshot`**: Current voltage state.
```python
@dataclass
class VoltageSnapshot:
    voltage: float                          # Current battery voltage in volts
    trend: Optional[str] = None             # "rising", "falling", "stable"
    timestamp: float = field(default_factory=time.time)
    charging_current: Optional[float] = None
    discharging_current: Optional[float] = None
    power_source: Optional[PowerSource] = None
```

**`WeatherForecastSignal`**: Current or near-term weather forecast for policy adjustment.
```python
@dataclass
class WeatherForecastSignal:
    condition: WeatherCondition                     # Primary forecast condition
    temperature: Optional[float] = None             # Ambient temperature °C
    confidence: Optional[float] = None              # 0.0-1.0 forecast confidence
    expected_solar_opportunity: Optional[str] = None  # "good", "moderate", "poor"
```

**`BatteryReservePolicy`**: Configuration for evening battery reserve.
```python
@dataclass
class BatteryReservePolicy:
    evening_reserve_target: float = 26.5            # Target voltage after sunset
    reserve_priority: int = 10                      # Higher = more aggressive reserve
```

**`DeviceEnergyPolicy`**: Per-device energy policy configuration (passive data).
```python
@dataclass
class DeviceEnergyPolicy:
    load_id: str
    priority: DevicePriority = DevicePriority.NORMAL
    load_class: LoadClass = LoadClass.DISCRETIONARY
    allowed_time_windows: list[tuple[TimeOfDay, TimeOfDay]] = field(default_factory=list)
    min_voltage_on: float = 24.0
    min_voltage_stay_on: float = 23.5
    voltage_off: float = 22.0
    cooldown_seconds: int = 120
    readiness_check_interval: int = 180
    health_check_interval: int = 300
    weather_sensitive: bool = False
    season_profile: Optional[str] = None
    allow_always_on_when_good: bool = False
    skip_when_cloudy_or_rainy: bool = False
    manual_override: str = "none"           # "none", "prefer_manual", "lock_manual"
    fail_safe_off: bool = True
```

**`ReadinessInput`**: Context for a readiness evaluation.
```python
@dataclass
class ReadinessInput:
    load_id: str
    policy: DeviceEnergyPolicy
    voltage: VoltageSnapshot
    forecast: Optional[WeatherForecastSignal] = None
    time_of_day: Optional[TimeOfDay] = None
    season: Optional[Season] = None
    last_switch_timestamp: Optional[float] = None
    is_currently_on: bool = False
```

**`ReadinessResult`**: Output of a readiness evaluation.
```python
@dataclass
class ReadinessResult:
    ready: bool
    reason: str = ""
    policy_decision: EnergyPolicyDecision = EnergyPolicyDecision.NO_ACTION
```

**`HealthInput`**: Context for a health evaluation.
```python
@dataclass
class HealthInput:
    load_id: str
    expected_state: bool           # True=ON, False=OFF
    observed_state: Optional[bool] = None
    status_age_seconds: Optional[float] = None
    consecutive_failures: int = 0
```

**`HealthCheckResult`**: Output of a health evaluation.
```python
@dataclass
class HealthCheckResult:
    status: HealthStatus
    reason: str = ""
    recommended_follow_up: str = ""  # "none", "retry", "flag_operator", "force_off"
```

**`EnergyPolicyContext`**: Aggregated context for the policy evaluation pipeline.
```python
@dataclass
class EnergyPolicyContext:
    voltage: VoltageSnapshot
    forecast: Optional[WeatherForecastSignal] = None
    time_of_day: Optional[TimeOfDay] = None
    season: Optional[Season] = None
    reserve_policy: Optional[BatteryReservePolicy] = None
```

## 7. Module Design

### Module Location
- **Create**: `app/control/energy_policy.py`
- **Update**: `app/control/__init__.py` (export new types)

### Module Dependencies
The module must use ONLY:
- Python standard library (`dataclasses`, `enum`, `typing`, `time`)
- `app.control.domain.SwitchableLoad` if needed (optional; not required for the 17 types listed above)

The module must NOT import:
- `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`
- `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
  `relay_device_manager`, `device_initializer`, `openweather`, `dess`
- Any runtime service, config reader, network client, or hardware adapter

### Safety Requirements
The module types must:
1. Be passive data definitions only.
2. Not execute hardware calls.
3. Not switch devices.
4. Not read environment variables.
5. Not read config files.
6. Not open network connections.
7. Not import runtime app services.
8. Not change startup behavior.
9. Not change pump automation behavior.
10. Not change manual switch behavior.
11. Not enable automation or ML control.
12. Represent ML advisory as advisory only (no control wire).

## 8. Files Coder May Edit

| File | Action |
|---|---|
| `app/control/energy_policy.py` | Create — 17 passive energy policy types |
| `app/control/__init__.py` | Edit — export new types |
| `scripts/check-energy-policy-domain-types.sh` | Create — static validation script |
| `.github/workflows/validate.yml` | Edit — add one validation step |
| `.project-memory/CURRENT_STATE.md` | Edit — add PR 0012 section |
| `.project-memory/ROADMAP.md` | Edit — mark PR 0012 |
| `.project-memory/pr/0012-energy-policy-domain-types/CODER_REPORT.txt` | Create |

## 9. Files Coder Must NOT Edit

All files not listed in section 8, including but not limited to:
- `run.py`, `app/service/*`, `app/devices/*`, `app/tuya/*`, `app/monitoring/*`,
  `app/ml/*`, `app/weather/*`, `service/*`, `shared_state/*`
- `app/control/domain.py` (frozen from PR 0009)
- `app/control/relay_mapping.py` (frozen from PR 0010)
- Config files, data files, Docker/deployment files
- `.github/workflows/build-and-deploy.yml`
- Existing validation scripts

## 10. Static Validation Script

`scripts/check-energy-policy-domain-types.sh` must verify:

1. `app/control/energy_policy.py` exists.
2. All 17 required type names exist in the file:
   `PowerSource`, `TimeOfDay`, `Season`, `WeatherCondition`, `LoadClass`,
   `DevicePriority`, `VoltageSnapshot`, `WeatherForecastSignal`,
   `BatteryReservePolicy`, `DeviceEnergyPolicy`, `ReadinessInput`,
   `ReadinessResult`, `HealthInput`, `HealthStatus`, `HealthCheckResult`,
   `EnergyPolicyContext`, `EnergyPolicyDecision`.
3. Required concept phrases exist: `26.5`, `switch ON`, `switch OFF`,
   `readiness`, `health`, `weather forecast`, `ML advisory`, `ML control`.
4. Forbidden imports absent from `app/control/energy_policy.py`:
   `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`,
   `app.weather`, `smart_home_controller`, `relay_tuya_controller`,
   `relay_channel_device`, `relay_device_manager`, `openweather`, `dess`.
5. Forbidden action calls absent: `switch_on_device`, `switch_off_device`,
   `switch_binary`, `switch_device`, `toggle_device`, `set_numeric`,
   `update_status`, `mark_switched`, `can_switch`, `ready_to_switch_on`,
   `ready_to_switch_off`, `is_device_on`.
6. Runtime files not modified (git diff check).
7. Exit 0 on pass, 1 on failure. Read-only, no network, no secrets.

## 11. Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are LOCKED after approval |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |

## 12. Future PR Boundary

Deferred to later PRs:

| Work | Target PR |
|---|---|
| Static policy configuration example (no secrets) | 0013 |
| Readiness evaluator (pure function) | 0014 |
| Health evaluator (pure function) | 0015 |
| Schedule/season profile model | 0016 |
| Weather adjustment evaluator | 0017 |
| Deterministic policy decision engine (no hardware exec) | 0018 |
| Manual control API or command queue | 0019 |
| Wire policy decisions to command proposal | 0020 |
| Controlled execution with safety gates | 0021+ |
| ML advisory integration | Later |
| ML control (safety gates per ADR-0003) | Much later |

## 13. Boundary Confirmations

- **Passive types only**: 17 dataclass/Enum types for the energy policy vocabulary.
  No evaluation logic, no hardware calls, no runtime wiring.
- **No runtime code changed**: `run.py`, `app/service/`, `app/devices/`, `app/tuya/`,
  `app/monitoring/`, `app/ml/`, `app/weather/` — all untouched.
- **No pump code changed**: Pump code already gated by PR 0008.
- **Manual switch control preserved**: All 4 methods + `toggle_device` + `_switch_loop`.
- **No ML control enabled**: `EnergyPolicyDecision` is a passive vocabulary type.
  ML advisory is advice-only. ML control deferred per ADR-0003.
- **No Docker image publishing change**: `build-and-deploy.yml` untouched.
- **No external GitOps/ArgoCD change**: Publishing boundary respected.
- **No dependencies added**: Standard library only.
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval.
