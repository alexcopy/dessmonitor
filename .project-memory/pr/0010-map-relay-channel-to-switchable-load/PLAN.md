# PR 0010 — Map Relay Channel to SwitchableLoad

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `0c7b00b3cd37d8c924fa4e276c1268cbee551f46` |
| Branch | `git branch --show-current` | `0010-map-relay-channel-to-switchable-load` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0010-map-relay-channel-to-switchable-load` and working tree is clean.

## 2. Why PR 0010 Exists

Previous PRs in the platform control redesign:

- PR 0007 documented the strategy.
- PR 0008 disabled pump automation by default while preserving manual ON/OFF control.
- PR 0009 introduced passive generic control domain types (`SwitchableLoad`, `ControlCommand`,
  etc.) under `app/control/domain.py` with zero runtime integration.

PR 0010 adds a **passive structural adapter** that maps relay-channel-shaped objects to
`SwitchableLoad` instances. This is the bridge between existing `RelayChannelDevice` instances
and the new domain vocabulary. It uses a `Protocol` to avoid importing `RelayChannelDevice`
directly and triggering its dataclass dependencies (`PUMP_W_MAP`, `logging`, `datetime`, etc.).

The adapter is NOT wired into any runtime component (`SmartHomeController`, `DeviceInitializer`,
`RelayTuyaController`, API, monitoring, ML, config, data) in PR 0010. It exists as a pure
transformation function that can be imported and tested independently.

## 3. Grep Evidence Summary

**Domain types exist only in `app/control/` and docs:**
All 9 types (`SwitchableLoad` through `PolicyDecision`) are confirmed present in
`app/control/domain.py` and `app/control/__init__.py`. No runtime code references them.

**RelayChannelDevice surface:**
The dataclass has 22+ fields including `id`, `name`, `desc`, `tuya_device_id`, `device_type`,
`control_key`, `state_key`, `api_key`, `min_volt`, `max_volt`, `priority`, `status`, `extra`,
`load_in_wt`, and many more. The mapping layer must extract only the non-secret, non-Tuya-specific
subset for `SwitchableLoad`.

**Manual switch methods preserved:**
All 4 methods on `RelayTuyaController` + `RelayDeviceManager.toggle_device` confirmed present.

**Pump automation disabled:**
`PUMP_AUTOMATION_ENABLED` gate confirmed in `run.py` and `SmartHomeController`.
`_pump_loop` is conditionally started under `if self.pump_automation_enabled`.

**Forbidden imports absent from domain module:**
`app/control/domain.py` does not import `tuya`, `dess`, `openweather`, `ml`,
`smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
or `relay_device_manager`. Confirmed by static validation script.

## 4. PR 0010 Scope

### 4.1 Adapter Module

**Create `app/control/relay_mapping.py`** containing:

1. A `Protocol` class `_RelayChannelLike` defining the duck-typed interface expected from
   relay-channel-shaped objects (has `id`, `name`, `desc`, `device_type`, and optional
   `control_key`, `state_key`, `extra`).
2. A function `relay_channel_to_switchable_load(relay: object) -> SwitchableLoad` that:
   - Creates a stable `load_id` from `relay.id` if present, else `relay.name`.
   - Sets `display_name` from `relay.name` if present, else `load_id`.
   - Preserves `device_type` if present.
   - Sets `metadata` dict with safe non-secret hints only:
     - `legacy_device_type`: the original `device_type` string.
     - `source_class`: `"RelayChannelDevice"` string literal (not an import).
     - `has_control_key`: `True`/`False` based on presence of `control_key` attribute.
     - `has_state_key`: `True`/`False` based on presence of `state_key` attribute.
     - `control_kind`: `"discrete"` or `"analog"` based on device_type heuristics.
   - NEVER includes `tuya_device_id`, `api_key`, `status`, `extra`, `min_volt`, `max_volt`,
     `load_in_wt`, or any raw config/secret values.
   - NEVER calls `relay.is_device_on()`, `relay.can_switch()`, `relay.ready_to_switch_on()`,
     `relay.ready_to_switch_off()`, `relay.mark_switched()`, `relay.update_status()`,
     `relay.set_on()`, `relay.set_off()`, `relay.tick()`, or any other method that could have
     side effects.
   - NEVER reads `relay.status` (could contain live Tuya state).
3. A function `relay_channels_to_switchable_loads(relays: Iterable[object]) -> list[SwitchableLoad]`
   that maps each relay through the single-object mapper, preserving input order, raising
   `ValueError` for entries without `id` or `name`.

### 4.2 Update `app/control/__init__.py`

Re-export the two mapping functions alongside the existing domain types.

### 4.3 Protocol: Duck-Typed, No Direct Import

The `Protocol` class must be defined inside `app/control/relay_mapping.py` and must NOT
import `RelayChannelDevice`. It must act as documentation and type-checker guidance only.
The mapping functions must accept plain `object` with `hasattr` guards, using the Protocol
for type hints only.

### 4.4 Safety Requirements

The mapping module must NOT:
- Import `app.devices`, `app.tuya`, `app.service`, `app.monitoring`, `app.ml`, `app.weather`,
  `smart_home_controller`, `relay_tuya_controller`, `device_initializer`, `device_status_logger`.
- Import `RelayChannelDevice` directly.
- Execute hardware calls (`switch_on_device`, `switch_off_device`, etc.).
- Read environment variables.
- Read config files.
- Open network connections.
- Mutate files.
- Be wired into any runtime code — the module exists as a pure transformation library.

### 4.5 Files Coder May Edit

| File | Action |
|---|---|
| `app/control/relay_mapping.py` | Create — adapter module with Protocol + mapping functions |
| `app/control/__init__.py` | Edit — re-export mapping functions |
| `scripts/check-relay-switchable-load-mapping.sh` | Create — static validation script |
| `.github/workflows/validate.yml` | Edit — add one validation step |
| `.project-memory/CURRENT_STATE.md` | Edit — add PR 0010 section |
| `.project-memory/ROADMAP.md` | Edit — mark PR 0010 as planned |
| `.project-memory/pr/0010-map-relay-channel-to-switchable-load/CODER_REPORT.txt` | Create |

### 4.6 Files Coder Must NOT Edit

All files not listed in 4.5, including but not limited to:

- `run.py` — no startup changes needed
- `app/service/smart_home_controller.py` — no wiring yet
- `app/devices/*` — all device model files untouched
- `app/tuya/*` — all Tuya adapter files untouched
- `app/monitoring/*` — all monitoring files untouched
- `app/ml/*` — all ML/data files untouched
- `app/weather/*`, `app/api.py`, `app/config.py`, `app/logger.py`, `app/device_initializer.py`
- `service/*`, `shared_state/*`
- `app/control/domain.py` — already frozen from PR 0009, no changes needed
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.gitignore`
- `.github/workflows/build-and-deploy.yml`
- `app/docker/*`
- `init-db.sql`, `requirements.txt`
- `scripts/check-repo-safety.sh`, `scripts/check-project-memory.sh`, `scripts/validate-yaml.py`,
  `scripts/check-runtime-smoke.py`, `scripts/check-image-publishing-boundary.sh`,
  `scripts/check-platform-control-redesign.sh`, `scripts/check-pump-automation-disabled.sh`,
  `scripts/check-generic-control-domain.sh`
- `.project-memory/pr/0010-.../PLAN.md` (locked)
- `.project-memory/pr/0010-.../PLAN_REVIEW.yaml` (locked)
- All `.project-memory/ADR/*`, `.project-memory/AGENT_STANDARD.txt`,
  `.project-memory/ORCHESTRATOR_STANDARD.txt`

### 4.7 Artifact Layout

```
.project-memory/pr/0010-map-relay-channel-to-switchable-load/
    PLAN.md                    ← This file (planning agent, LOCKED)
    PLAN_REVIEW.yaml           ← Plan-review agent (LOCKED after approval)
    CODER_REPORT.txt           ← Coder agent implementation report
    PRECOMMIT_REVIEW.yaml      ← Precommit-review agent
```

### 4.8 Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are LOCKED after approval |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |

## 5. Detailed Implementation Specification

### 5.1 `app/control/relay_mapping.py`

```python
"""Adapter mapping from relay-channel-shaped objects to SwitchableLoad.

This module provides pure data transformation functions. It does NOT import
RelayChannelDevice, Tuya adapters, or any runtime service code. It relies on
a duck-typed Protocol to define the expected relay object shape.

Must NOT:
- Execute hardware calls
- Read environment variables
- Read config files
- Open network connections
- Mutate files
"""

from typing import Any, Iterable, Optional, Protocol

from app.control.domain import SwitchableLoad


class RelayChannelLike(Protocol):
    """Duck-typed protocol for objects that can be mapped to SwitchableLoad.

    This is NOT RelayChannelDevice. It describes the minimal attribute surface
    expected by the mapping functions.
    """
    id: str
    name: str
    desc: str
    device_type: str
    control_key: Optional[str] = None
    state_key: Optional[str] = None
    extra: Optional[dict] = None


_ANALOG_HINTS = {
    "thermo", "thermometer", "termo", "temp_sensor",
    "watertemp", "water_thermo",
}


def _control_kind(device_type: Optional[str]) -> str:
    """Return 'discrete' for ON/OFF devices, 'analog' for numeric sensors."""
    if device_type and device_type.lower() in _ANALOG_HINTS:
        return "analog"
    return "discrete"


def relay_channel_to_switchable_load(relay: object) -> SwitchableLoad:
    """Map a single relay-channel-shaped object to SwitchableLoad.

    Args:
        relay: An object with id, name, desc, device_type attributes
               (duck-typed — does not require RelayChannelDevice).

    Returns:
        A SwitchableLoad instance with safe metadata extracted from the relay.

    Raises:
        ValueError: If the relay has neither id nor name.
    """
    relay_id: Optional[str] = getattr(relay, "id", None)
    relay_name: Optional[str] = getattr(relay, "name", None)

    if not relay_id and not relay_name:
        raise ValueError(
            "Cannot map relay to SwitchableLoad: "
            "relay object has neither 'id' nor 'name' attribute"
        )

    load_id: str = str(relay_id) if relay_id else str(relay_name)
    display_name: str = str(relay_name) if relay_name else load_id
    device_type: Optional[str] = getattr(relay, "device_type", None)
    device_type_str: Optional[str] = str(device_type) if device_type is not None else None

    # Build metadata with safe, non-secret hints
    metadata: dict[str, Any] = {}
    metadata["legacy_device_type"] = device_type_str
    metadata["source_class"] = "RelayChannelDevice"

    control_key = getattr(relay, "control_key", None)
    state_key = getattr(relay, "state_key", None)
    metadata["has_control_key"] = control_key is not None
    metadata["has_state_key"] = state_key is not None
    metadata["control_kind"] = _control_kind(device_type_str)

    desc: Optional[str] = getattr(relay, "desc", None)
    if desc:
        metadata["legacy_desc"] = str(desc)

    extra: Optional[dict] = getattr(relay, "extra", None)
    if extra and isinstance(extra, dict):
        # Copy only a few safe keys — never the full extra dict
        safe_extra_keys = {"switch_time", "min_trashhold"}
        for key in safe_extra_keys:
            if key in extra:
                metadata[key] = extra[key]

    return SwitchableLoad(
        id=load_id,
        name=display_name,
        device_type=device_type_str,
        metadata=metadata,
    )


def relay_channels_to_switchable_loads(
    relays: Iterable[object],
) -> list[SwitchableLoad]:
    """Map multiple relay-channel-shaped objects to SwitchableLoad instances.

    Args:
        relays: Iterable of objects with id, name, desc, device_type attributes.

    Returns:
        List of SwitchableLoad instances in input order.

    Raises:
        ValueError: If any entry is invalid (no id and no name).
    """
    return [relay_channel_to_switchable_load(r) for r in relays]
```

### 5.2 `app/control/__init__.py` Update

Append the two mapping functions to the existing imports and `__all__`:

```python
from app.control.relay_mapping import (
    relay_channel_to_switchable_load,
    relay_channels_to_switchable_loads,
)

__all__ = [
    # ... existing types ...
    "relay_channel_to_switchable_load",
    "relay_channels_to_switchable_loads",
]
```

### 5.3 Static Validation Script (`scripts/check-relay-switchable-load-mapping.sh`)

Checks:
1. `app/control/relay_mapping.py` exists.
2. Function `relay_channel_to_switchable_load` exists in the file.
3. Function `relay_channels_to_switchable_loads` exists in the file.
4. `SwitchableLoad` is imported/used in the file.
5. Forbidden imports absent from `app/control/relay_mapping.py`:
   `app.devices`, `app.tuya`, `app.service`, `app.monitoring`, `app.ml`, `app.weather`,
   `smart_home_controller`, `relay_tuya_controller`, `device_initializer`,
   `relay_channel_device`, `device_status_logger`, `pond_pump_controller`.
6. Runtime files unchanged (simple git diff check).

Read-only, no network, no secrets, no mutations. Exit 0 on pass, 1 on failure.

### 5.4 validate.yml Update

Add one step:

```yaml
      - name: 🔍 Relay-to-SwitchableLoad mapping check
        run: bash scripts/check-relay-switchable-load-mapping.sh
```

### 5.5 CURRENT_STATE.md Update

Add after the PR 0009 section:
```
## PR 0010 — Relay-to-SwitchableLoad Mapping Added
PR 0010 adds a passive adapter mapping relay-channel-shaped objects to SwitchableLoad
instances. The mapping is not wired into runtime behavior yet. Manual relay/switch ON/OFF
remains unchanged. Pump automation remains disabled by default from PR 0008.
```

### 5.6 ROADMAP.md Update

Mark PR 0010 as in-progress:
```
- [ ] PR 0010 — Backward-compatible adapter from old config to generic load
```

## 6. Validation Commands

Run these after implementation to verify correctness:

```bash
# 1. Verify mapping module exists
test -f app/control/relay_mapping.py && echo "relay_mapping.py: EXISTS" || echo "relay_mapping.py: MISSING"

# 2. Verify mapping module is importable
python -c "from app.control.relay_mapping import relay_channel_to_switchable_load, relay_channels_to_switchable_loads; print('Functions imported successfully')"

# 3. Verify mapping produces correct output
python -c "
from app.control.relay_mapping import relay_channel_to_switchable_load

# Simulate a relay-channel-like object
class FakeRelay:
    id = 'switch_1'
    name = 'Pond Pump'
    desc = 'Main circulation pump'
    device_type = 'pump'
    control_key = 'switch_1'
    state_key = 'switch_1'

load = relay_channel_to_switchable_load(FakeRelay())
print(f'id={load.id}')
print(f'name={load.name}')
print(f'device_type={load.device_type}')
print(f'metadata keys: {list(load.metadata.keys())}')
assert load.id == 'switch_1'
assert load.name == 'Pond Pump'
assert load.device_type == 'pump'
assert load.metadata.get('source_class') == 'RelayChannelDevice'
assert 'has_control_key' in load.metadata
assert 'has_state_key' in load.metadata
print('Mapping test PASSED')
"

# 4. Verify forbidden imports absent
python -c "
import ast, sys
with open('app/control/relay_mapping.py') as f:
    tree = ast.parse(f.read())
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            print(f'IMPORT: {alias.name}')
    elif isinstance(node, ast.ImportFrom):
        print(f'FROM {node.module} IMPORT: {[a.name for a in node.names]}')
"

# 5. Run the static validation script
bash scripts/check-relay-switchable-load-mapping.sh
echo "Exit code: $?"

# 6. Run all existing validations
python -m compileall -q . -x '/(ml_data|\.project-memory|\.git|venv|\.venv)/'
echo "compileall: $?"

bash scripts/check-repo-safety.sh; echo "safety: $?"
bash scripts/check-project-memory.sh; echo "project-memory: $?"
python scripts/validate-yaml.py; echo "yaml: $?"
bash scripts/check-image-publishing-boundary.sh; echo "image-boundary: $?"
bash scripts/check-platform-control-redesign.sh; echo "control-redesign: $?"
bash scripts/check-pump-automation-disabled.sh; echo "pump-automation: $?"
bash scripts/check-generic-control-domain.sh; echo "generic-control-domain: $?"

# 7. Verify locked artifacts unchanged
git diff --name-only HEAD -- .project-memory/pr/0010-map-relay-channel-to-switchable-load/PLAN.md
# Should produce no output

git diff --name-only HEAD -- .project-memory/pr/0010-map-relay-channel-to-switchable-load/PLAN_REVIEW.yaml
# Should produce no output

# 8. Verify runtime files NOT modified
NOT_MODIFIED=$(git diff --name-only HEAD -- run.py app/service/ app/devices/ app/tuya/ app/monitoring/ app/ml/ Dockerfile docker-compose.yml .github/workflows/build-and-deploy.yml)
if [ -z "$NOT_MODIFIED" ]; then echo "RUNTIME FILES: NOT MODIFIED"; else echo "ERROR: $NOT_MODIFIED"; fi

# 9. Verify build-and-deploy workflow unchanged
git diff --name-only HEAD -- .github/workflows/build-and-deploy.yml
# Should produce no output

# 10. Verify only allowed files changed
git diff --name-only HEAD

# 11. Verify coder artifact exists
test -f .project-memory/pr/0010-map-relay-channel-to-switchable-load/CODER_REPORT.txt && echo "CODER_REPORT: OK"
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
| Wire mapping into `SmartHomeController` (replace pump loop with generic control loop) | PR 0011 |
| Wire mapping into `RelayTuyaController` (command dispatch via ControlCommand) | PR 0011 |
| Wire mapping into `DeviceInitializer` (produce SwitchableLoad alongside RelayChannelDevice) | PR 0011 |
| Manual ON/OFF API | PR 0014 |
| Monitoring label migration from PUMP to generic load | PR 0012 |
| Telemetry schema migration (MLDataPoint → TelemetryPoint) | PR 0013 |
| UI/UX control panel | PR 0015 |
| Policy layer | Later |
| ML advisory (shadow mode) | Later |
| ML control (after safety-reviewed gates per ADR-0003) | Much later |
| Config migration (devices.yaml → SwitchableLoad config) | Later |

## 9. Boundary Confirmations

- **Passive adapter only**: The mapping module transforms objects to `SwitchableLoad` instances.
  It does NOT wire into `SmartHomeController`, `RelayTuyaController`, `DeviceInitializer`,
  API, monitoring, ML, config, or data.
- **No `RelayChannelDevice` import**: The module uses a duck-typed `Protocol` and `hasattr` guards.
- **No hardware calls**: The mapping functions never call `switch_on_device`, `switch_off_device`,
  `is_device_on`, `can_switch`, `set_on`, `set_off`, `tick`, or any other method with side effects.
- **No secrets in metadata**: `tuya_device_id`, `api_key`, `status`, `min_volt`, `max_volt`,
  `load_in_wt` are explicitly excluded. Only safe informational hints are stored.
- **No pump code changed**: Pump-related code remains untouched (already gated by PR 0008).
- **Manual switch control preserved**: All 4 methods on `RelayTuyaController` +
  `RelayDeviceManager.toggle_device` + `SmartHomeController._switch_loop` unchanged.
- **No ML control enabled**: Per ADR-0003. Deferred.
- **No Docker image publishing change**: `build-and-deploy.yml` untouched.
- **No external GitOps/ArgoCD change**: Image publishing boundary respected.
- **No dependencies added**: Standard library only.
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval.
