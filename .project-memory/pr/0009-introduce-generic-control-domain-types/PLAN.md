# PR 0009 — Introduce Generic Control Domain Types

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `3f069dcb477013f0ccfb23831f0bece8f0b064a0` |
| Branch | `git branch --show-current` | `0009-introduce-generic-control-domain-types` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0009-introduce-generic-control-domain-types` and working tree is clean.

## 2. Why PR 0009 Exists

PR 0007 documented the platform control redesign strategy. PR 0008 disabled active pump
automation by default while preserving manual relay/switch ON/OFF control.

The next step is to introduce a small, import-safe domain vocabulary for controllable
devices. This vocabulary must be independent of Tuya command keys, DESS raw formats,
OpenWeather schemas, and the old pump concept. It must not wire into any runtime
control paths yet. It must remain passive data definitions only.

Adding these types now gives the platform a shared vocabulary to build upon in later PRs
(adapter mapping, manual control API, monitoring/data migration, policy, ML advisory).

## 3. Grep Evidence Summary

**Domain types not yet in app code:**
```
SwitchableLoad, ControlCommand, ControlState, ObservedState,
DesiredState, CommandResult, TelemetryPoint, PolicyDecision
```
These names exist only in `.project-memory/PLATFORM_CONTROL_REDESIGN.md` and related
planning/validation artifacts. They have zero presence in runtime code.

**Pump references confined to legacy code:**
Pump references exist only in `app/ml/` (obsolete ML training/docs), `app/tuya/status_updater_async.py`
(compatibility shim for `pump_mode`), `app/monitoring/device_status_logger.py` (read-only logging),
`app/device_initializer.py` (control_key/state_key special case). The active automation `_pump_loop`
is now gated by `PUMP_AUTOMATION_ENABLED` (default disabled) as of PR 0008.

**Manual switch methods preserved and verified:**
All 4 methods on `RelayTuyaController` + `RelayDeviceManager.toggle_device` + `SmartHomeController._switch_loop`
are confirmed present and unchanged.

**Dataclasses and Enums already used in the repository:**
`app/api.py` uses `@dataclass`, `app/service/smart_home_controller.py` uses `IntEnum`,
`app/devices/relay_channel_device.py` uses `@dataclass`. No new dependencies needed.

## 4. PR 0009 Scope

### 4.1 Domain Module

**Create `app/control/domain.py`** containing these types as passive data definitions:

| Type | Kind | Semantics |
|---|---|---|
| `SwitchableLoad` | `@dataclass` | Logical controllable electrical load. Fields: `id: str` (stable ID), `name: str` (display name), `device_type: str | None` (optional classification), `metadata: dict` (free-form extra info). Must NOT contain Tuya command keys as required fields. |
| `DesiredState` | `Enum` | Requested target state: `ON`, `OFF`, `UNKNOWN`. No numeric setpoint in this PR. |
| `ObservedState` | `Enum` | Current known state: `ON`, `OFF`, `UNKNOWN`. May be extended with numeric telemetry later. |
| `ControlState` | `@dataclass` | Combined desired + observed state snapshot. Fields: `load_id: str`, `desired: DesiredState`, `observed: ObservedState`, `last_updated: float` (Unix timestamp). |
| `ControlCommand` | `@dataclass` | Explicit command request. Fields: `target_id: str`, `desired_state: DesiredState`, `source: CommandSource`, `request_id: str` (UUID string), `timestamp: float` (Unix timestamp). Must NOT execute hardware calls. |
| `CommandSource` | `Enum` | Origin of a command: `MANUAL`, `API`, `POLICY`, `ML_ADVISORY`, `TEST`, `UNKNOWN`. Note: `ML_ADVISORY` is advisory output, NOT ML control. ML control is deferred. |
| `CommandResult` | `@dataclass` | Outcome of executing a command. Fields: `command_id: str` (matches `ControlCommand.request_id`), `success: bool`, `error: str | None`, `timestamp: float`. |
| `TelemetryPoint` | `@dataclass` | Future generic telemetry shape. Fields: `load_id: str`, `metric: str`, `value: float | None`, `unit: str | None`, `timestamp: float`. Must NOT replace current `MLDataPoint` or `MLDataCollector` schema yet. |
| `PolicyDecision` | `@dataclass` | Future policy-layer output. Fields: `load_id: str`, `recommended_state: DesiredState`, `priority: int`, `reason: str`, `timestamp: float`. Must NOT call hardware. Must NOT be enabled in PR 0009. |

Also create `app/control/__init__.py` that re-exports all types for clean imports:
```python
from app.control.domain import (
    SwitchableLoad,
    DesiredState,
    ObservedState,
    ControlState,
    ControlCommand,
    CommandSource,
    CommandResult,
    TelemetryPoint,
    PolicyDecision,
)

__all__ = [
    "SwitchableLoad",
    "DesiredState",
    "ObservedState",
    "ControlState",
    "ControlCommand",
    "CommandSource",
    "CommandResult",
    "TelemetryPoint",
    "PolicyDecision",
]
```

### 4.2 Safety Requirements (Hard Rules)

The new domain module must be **passive data definitions only**. It must NOT:

1. Import any Tuya module (`app.tuya.*`), DESS module (`app.api`, `service.inverter_monitor`),
   OpenWeather module (`app.weather.*`), ML module (`app.ml.*`), service loop (`app.service.*`),
   device model (`app.devices.*`), or hardware adapter (`shared_state.*`).
2. Call any hardware, Tuya API, DESS API, or network service.
3. Read environment variables (`os.getenv`).
4. Read config files (`config.json`, `devices.yaml`).
5. Open network connections.
6. Change startup behavior of any existing component.
7. Change pump automation behavior (already disabled by default from PR 0008).
8. Change manual switch behavior (still runs via `_switch_loop`).
9. Enable automation or ML control.

Approved imports:
- `dataclasses` (standard library)
- `enum` (standard library)
- `typing` (standard library)
- `uuid` (standard library, for `request_id` in `ControlCommand`)
- `time` (standard library, for Unix timestamps)

### 4.3 Files Coder May Edit

| File | Action |
|---|---|
| `app/control/__init__.py` | Create — re-export all domain types |
| `app/control/domain.py` | Create — define all domain types |
| `scripts/check-generic-control-domain.sh` | Create — static validation script |
| `.github/workflows/validate.yml` | Edit — add one validation step |
| `.project-memory/CURRENT_STATE.md` | Edit — add PR 0009 section |
| `.project-memory/ROADMAP.md` | Edit — mark PR 0009 as planned, PR 0008 as done |
| `.project-memory/pr/0009-introduce-generic-control-domain-types/CODER_REPORT.txt` | Create |

### 4.4 Files Coder Must NOT Edit

All files not listed in 4.3 above, including but not limited to:

- `run.py` — no startup changes needed
- `app/service/smart_home_controller.py` — no wiring of domain types yet
- `app/devices/*` — all device model files untouched
- `app/tuya/*` — all Tuya adapter files untouched
- `app/monitoring/*` — all monitoring files untouched
- `app/ml/*` — all ML/data files untouched
- `app/weather/*`, `app/api.py`, `app/config.py`, `app/logger.py`, `app/device_initializer.py`
- `service/*`, `shared_state/*`
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.gitignore`
- `.github/workflows/build-and-deploy.yml`
- `app/docker/*`
- `init-db.sql`, `requirements.txt`
- `.project-memory/pr/0009-.../PLAN.md` (locked)
- `.project-memory/pr/0009-.../PLAN_REVIEW.yaml` (locked)
- All `.project-memory/ADR/*`, `.project-memory/AGENT_STANDARD.txt`, `.project-memory/ORCHESTRATOR_STANDARD.txt`
- `scripts/check-repo-safety.sh`, `scripts/check-project-memory.sh`, `scripts/validate-yaml.py`,
  `scripts/check-runtime-smoke.py`, `scripts/check-image-publishing-boundary.sh`,
  `scripts/check-platform-control-redesign.sh`

### 4.5 Artifact Layout

```
.project-memory/pr/0009-introduce-generic-control-domain-types/
    PLAN.md                    ← This file (planning agent, LOCKED)
    PLAN_REVIEW.yaml           ← Plan-review agent (LOCKED after approval)
    CODER_REPORT.txt           ← Coder agent implementation report
    PRECOMMIT_REVIEW.yaml      ← Precommit-review agent
```

### 4.6 Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are LOCKED after approval |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |

## 5. Detailed Implementation Specification

### 5.1 `app/control/domain.py`

```python
"""Generic control domain types for dessmonitor.

These types define a shared vocabulary for controllable loads, commands,
state observation, and policy decisions. They are passive data definitions
only and must NOT import any runtime modules (Tuya, DESS, OpenWeather, ML,
service loops, device models, or hardware adapters).

This module uses only Python standard library: dataclasses, enum, typing, uuid, time.
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DesiredState(Enum):
    """The requested target state for a controllable load."""
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"


class ObservedState(Enum):
    """The currently observed state of a controllable load."""
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"


class CommandSource(Enum):
    """Origin of a control command.

    ML_ADVISORY indicates an advisory recommendation from an ML model,
    NOT direct ML control. ML control is deferred until safety-reviewed gates
    are passed (per ADR-0003).
    """
    MANUAL = "manual"
    API = "api"
    POLICY = "policy"
    ML_ADVISORY = "ml_advisory"
    TEST = "test"
    UNKNOWN = "unknown"


@dataclass
class SwitchableLoad:
    """A logical controllable electrical load.

    This is NOT a pump. It represents any device that can be switched ON/OFF
    (and optionally set to a numeric level).
    
    Must NOT contain Tuya command keys as required fields.
    """
    id: str
    name: str
    device_type: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ControlState:
    """Combined desired and observed state snapshot for a load."""
    load_id: str
    desired: DesiredState
    observed: ObservedState
    last_updated: float = field(default_factory=time.time)


@dataclass
class ControlCommand:
    """A command to set a load to a desired state.

    Must NOT execute hardware calls. This is a passive data object.
    """
    target_id: str
    desired_state: DesiredState
    source: CommandSource
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)


@dataclass
class CommandResult:
    """Outcome of executing a ControlCommand."""
    command_id: str
    success: bool
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class TelemetryPoint:
    """A generic timestamped metric reading for any load.

    Does NOT replace the current MLDataPoint schema. Data schema migration
    is deferred to a later PR (PR 0013 per PLATFORM_CONTROL_REDESIGN.md).
    """
    load_id: str
    metric: str
    value: Optional[float] = None
    unit: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class PolicyDecision:
    """A decision produced by a policy engine.

    Must NOT call hardware. Must NOT be enabled in PR 0009.
    The policy engine will be introduced in a later PR.
    """
    load_id: str
    recommended_state: DesiredState
    priority: int = 0
    reason: str = ""
    timestamp: float = field(default_factory=time.time)
```

### 5.2 `app/control/__init__.py`

```python
from app.control.domain import (
    SwitchableLoad,
    DesiredState,
    ObservedState,
    ControlState,
    ControlCommand,
    CommandSource,
    CommandResult,
    TelemetryPoint,
    PolicyDecision,
)

__all__ = [
    "SwitchableLoad",
    "DesiredState",
    "ObservedState",
    "ControlState",
    "ControlCommand",
    "CommandSource",
    "CommandResult",
    "TelemetryPoint",
    "PolicyDecision",
]
```

### 5.3 Static Validation Script (`scripts/check-generic-control-domain.sh`)

Checks:
1. `app/control/domain.py` exists.
2. `app/control/__init__.py` exists.
3. Required type names exist in `app/control/domain.py`:
   `SwitchableLoad`, `ControlCommand`, `ControlState`, `ObservedState`,
   `DesiredState`, `CommandResult`, `CommandSource`, `TelemetryPoint`, `PolicyDecision`.
4. `app/control/domain.py` does NOT import forbidden adapters:
   `tuya`, `dess`, `openweather`, `ml`, `smart_home_controller`, `relay_tuya_controller`,
   `pond_pump_controller`, `device_initializer`, `device_status_logger`.
5. No runtime files modified (simple git diff check).

Exit 0 on pass, 1 on failure. Read-only, no network, no secrets, no mutations.

### 5.4 validate.yml Update

Add one step after the existing pump automation check:

```yaml
      - name: 🔍 Generic control domain check
        run: bash scripts/check-generic-control-domain.sh
```

### 5.5 CURRENT_STATE.md Update

Add after the PR 0008 section:
```
## PR 0009 — Generic Control Domain Types Introduced
PR 0009 introduces generic control domain types (SwitchableLoad, ControlCommand, etc.)
in `app/control/domain.py`. These are passive data definitions only — no runtime
control paths are wired yet. Manual relay/switch ON/OFF remains unchanged. Pump
automation remains disabled by default from PR 0008.
```

### 5.6 ROADMAP.md Update

Mark PR 0008 as done and PR 0009 as in-progress:
```
- [x] PR 0008 — Disable pump automation, preserve manual switch ON/OFF
- [ ] PR 0009 — Introduce generic control domain types (SwitchableLoad, ControlCommand)
```

## 6. Validation Commands

Run these after implementation to verify correctness:

```bash
# 1. Verify domain module exists
test -f app/control/domain.py && echo "domain.py: EXISTS" || echo "domain.py: MISSING"
test -f app/control/__init__.py && echo "__init__.py: EXISTS" || echo "__init__.py: MISSING"

# 2. Verify domain module is importable
python -c "from app.control.domain import SwitchableLoad, ControlCommand, DesiredState, ObservedState, ControlState, CommandSource, CommandResult, TelemetryPoint, PolicyDecision; print('All types imported successfully')"

# 3. Verify domain module has no forbidden imports
python -c "
import ast
with open('app/control/domain.py') as f:
    tree = ast.parse(f.read())
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            print(f'IMPORT: {alias.name}')
    elif isinstance(node, ast.ImportFrom):
        print(f'FROM {node.module} IMPORT: {[a.name for a in node.names]}')
"

# 4. Run the static validation script
bash scripts/check-generic-control-domain.sh
echo "Exit code: $?"

# 5. Run all existing validations
python -m compileall -q . -x '/(ml_data|\.project-memory|\.git|venv|\.venv)/'
echo "compileall: $?"

bash scripts/check-repo-safety.sh
echo "safety: $?"

bash scripts/check-project-memory.sh
echo "project-memory: $?"

python scripts/validate-yaml.py
echo "yaml: $?"

bash scripts/check-image-publishing-boundary.sh
echo "image-boundary: $?"

bash scripts/check-platform-control-redesign.sh
echo "control-redesign: $?"

bash scripts/check-pump-automation-disabled.sh
echo "pump-automation: $?"

# 6. Verify locked artifacts unchanged
git diff --name-only HEAD -- .project-memory/pr/0009-introduce-generic-control-domain-types/PLAN.md
# Should produce no output

git diff --name-only HEAD -- .project-memory/pr/0009-introduce-generic-control-domain-types/PLAN_REVIEW.yaml
# Should produce no output

# 7. Verify runtime files NOT modified
git diff --name-only HEAD -- run.py app/service/ app/devices/ app/tuya/ app/monitoring/ app/ml/ Dockerfile docker-compose.yml .github/workflows/build-and-deploy.yml
# Should produce no output

# 8. Verify only allowed files changed (summary)
git diff --name-only HEAD

# 9. Verify coder artifact exists
test -f .project-memory/pr/0009-introduce-generic-control-domain-types/CODER_REPORT.txt && echo "CODER_REPORT: OK"
```

## 7. CODER_REPORT.txt Requirements

The coder must produce `CODER_REPORT.txt` with these sections:
- `TASK COMPLETE`
- `BLOCKERS`
- `WARNINGS`
- `FILES CHANGED`
- `VALIDATION RUN`
- `DEVIATIONS FROM PLAN`
- `BOUNDARY CONFIRMATIONS`
- `NEXT REQUIRED ACTION`

## 8. Deferred to Later PRs

| Work | Target PR |
|---|---|
| Adapter mapping from `RelayChannelDevice` to `SwitchableLoad` | PR 0010 |
| `SmartHomeController` integration with `ControlCommand` | PR 0011 |
| `RelayTuyaController` integration with domain types | PR 0011 |
| `DeviceInitializer` migration to produce `SwitchableLoad` | PR 0010 |
| Monitoring label migration from PUMP to generic load | PR 0012 |
| Telemetry schema migration (MLDataPoint → TelemetryPoint) | PR 0013 |
| Manual ON/OFF API | PR 0014 |
| UI/UX control panel | PR 0015 |
| Policy layer (PolicyDecision → automation) | Later |
| ML advisory (shadow mode) | Later |
| ML control (after safety-reviewed gates per ADR-0003) | Much later |

## 9. Boundary Confirmations

- **Domain types only**: No wiring into `SmartHomeController`, `RelayTuyaController`,
  `DeviceInitializer`, or any runtime component. Zero integration.
- **No runtime behavior change**: `run.py`, `app/service/`, `app/devices/`, `app/tuya/`,
  `app/monitoring/`, `app/ml/` — all untouched.
- **No pump code changed**: Pump-related code remains untouched (already gated by PR 0008).
- **Manual switch control preserved**: All 4 methods on `RelayTuyaController` +
  `RelayDeviceManager.toggle_device` + `SmartHomeController._switch_loop` unchanged.
- **No ML control enabled**: `ML_ADVISORY` in `CommandSource` is documentation/placeholder
  only. ML control is deferred per ADR-0003.
- **No Docker image publishing change**: `build-and-deploy.yml` untouched.
- **No external GitOps/ArgoCD change**: Image publishing boundary respected.
- **No dependencies added**: Standard library only.
- **Import-safe**: Module can be imported in CI and smoke validation without requiring
  Tuya configs, device configs, network, or external services.
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval.
