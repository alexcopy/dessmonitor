# PR 0008 — Disable Pump Automation and Preserve Manual Switch Control

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `07f1a1b7614cbbf1be61a91acaa4daaeb4e3e9db` |
| Branch | `git branch --show-current` | `0008-disable-pump-automation-and-preserve-manual-switch-control` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0008-disable-pump-automation-and-preserve-manual-switch-control` and working tree is clean.

## 2. Why PR 0008 Exists

PR 0007 (strategy) documented that the physical pump / water pump no longer exists and that pump-specific automation is obsolete. However, the current application runtime **unconditionally starts** the pump automation loop (`_pump_loop`) whenever the application runs. This is wasteful and risks confusing behavior if old pump Tuya config is absent or stale.

PR 0008 is the first implementation step of the staged platform control redesign. It makes a single, safe, reversible change: **disable active pump-specific automation by default**, while keeping all manual relay/switch ON/OFF capability fully functional.

This is NOT the full platform redesign. No code is deleted. No broad renaming occurs. No data schema changes are made. No ML code is touched.

## 3. Active Pump Automation Audit

### 3.1 The Active Runtime Path

The only active pump automation path runs through these components in sequence:

```
run.py line ~68: SmartHomeController(dev_mgr, tuya_ctrl, switch_int=180, pump_int=120)
    ↓
SmartHomeController.start() line 61: self._tasks.append(loop.create_task(self._pump_loop()))
    ↓ (unconditionally, every application startup)
_pump_loop() iterates devices with device_type == "pump",
    calls PondPumpController.deside_speed(),
    calls tuya_ctrl.set_numeric(pump, target) to set P value.
```

No environment variable, config flag, or conditional check controls whether `_pump_loop` starts. It always starts.

### 3.2 Grep Evidence

**`pump_int` and `_pump_loop` references:**
```
run.py:75:        pump_int=120,  # сек между коррекцией насоса
app/service/smart_home_controller.py:7:  from app.devices.pond_pump_controller import PondPumpController
app/service/smart_home_controller.py:50: self.pump_logic = PondPumpController()
app/service/smart_home_controller.py:61: self._tasks.append(loop.create_task(self._pump_loop()))
app/service/smart_home_controller.py:129-173:_pump_loop method
```

**Switch/manual control methods (must preserve):**
```
app/tuya/relay_tuya_controller.py:44:  switch_on_device()
app/tuya/relay_tuya_controller.py:51:  switch_off_device()
app/tuya/relay_tuya_controller.py:137: switch_binary()
app/tuya/relay_tuya_controller.py:146: switch_device()
app/devices/relay_device_manager.py:71: toggle_device()
```

**No existing gating flag found:**
```
grep for PUMP_AUTOMATION, DISABLE_PUMP, pump_enabled, enable_pump → no results
```

### 3.3 Classification of Pump-Coupled Areas

| Path | File | Classification for PR 0008 |
|---|---|---|
| `pump_int` parameter | `run.py` | **Gate now** — pass `pump_int` but only create `_pump_loop` if enabled |
| `SmartHomeController.__init__` | `app/service/smart_home_controller.py` | **Gate now** — accept optional `pump_automation_enabled` flag, skip `_pump_loop` creation when false |
| `SmartHomeController.start()` | `app/service/smart_home_controller.py` | **Gate now** — conditionally create `_pump_loop` task |
| `_pump_loop` | `app/service/smart_home_controller.py` | **Leave as compatibility shim** — method body unchanged, just not started |
| `PondPumpController` | `app/devices/pond_pump_controller.py` | **Leave as compatibility shim** — do not edit |
| `pump_power_map` | `app/devices/pump_power_map.py` | **Leave as legacy data** — do not edit |
| `RelayChannelDevice._pump_w_from_p` | `app/devices/relay_channel_device.py` | **Leave as legacy data** — do not edit |
| `device_initializer.py` pump special case | `app/device_initializer.py` | **Defer to later PR** — do not edit |
| `status_updater_async.py` pump_mode | `app/tuya/status_updater_async.py` | **Leave as compatibility shim** — do not edit |
| `device_status_logger.py` PUMP | `app/monitoring/device_status_logger.py` | **Leave as compatibility shim** — do not edit |
| `app/ml/ml_data_collector.py` pump fields | `app/ml/ml_data_collector.py` | **Forbidden in PR 0008** — do not edit |
| `app/ml/ml_model_training_example.py` pump controller | `app/ml/ml_model_training_example.py` | **Forbidden in PR 0008** — do not edit |
| `devices.yaml` pump config | `devices.yaml` (gitignored) | **Configuration boundary** — document only, do not commit |

## 4. PR 0008 Implementation Scope

### 4.1 Must Implement

1. **Gate `_pump_loop` creation in `SmartHomeController`** (`app/service/smart_home_controller.py`):
   - Add `pump_automation_enabled: bool = False` parameter to `__init__`.
   - Store as `self.pump_automation_enabled`.
   - In `start()`, conditionally append `_pump_loop` task only when `self.pump_automation_enabled` is `True`.
   - The `_pump_loop` method body is NOT modified — only the decision to start it.

2. **Update `run.py` to pass the gate**:
   - Read `PUMP_AUTOMATION_ENABLED` from environment: `os.getenv("PUMP_AUTOMATION_ENABLED", "").lower() in ("true", "1", "yes")`.
   - Default when absent: `False` (pump automation disabled).
   - Pass `pump_automation_enabled=...` to `SmartHomeController`.
   - The `pump_int` parameter is still passed (backward compatible) — it is simply not consumed when automation is disabled.

3. **Add static validation script** (`scripts/check-pump-automation-disabled.sh`):
   - Verifies that pump automation has safe disabled default (`PUMP_AUTOMATION_ENABLED` env var, default `False`).
   - Verifies that `_pump_loop` is not started unconditionally (grep for `create_task(self._pump_loop())` inside an `if` block).
   - Verifies that manual switch methods still exist in `app/tuya/relay_tuya_controller.py` (`switch_on_device`, `switch_off_device`).
   - Read-only, no network, no secrets, no mutations.

4. **Add script to `.github/workflows/validate.yml`** (additive step):
   - `bash scripts/check-pump-automation-disabled.sh` after the platform control redesign check.

5. **Update `.project-memory/CURRENT_STATE.md`**:
   - Add a line: "PR 0008 disables active pump automation by default. Manual relay/switch control remains available."

### 4.2 Must Preserve

All manual control APIs (confirmed present):

| Method | File | Status |
|---|---|---|
| `RelayTuyaController.switch_on_device(device)` | `app/tuya/relay_tuya_controller.py:44` | Preserved |
| `RelayTuyaController.switch_off_device(device)` | `app/tuya/relay_tuya_controller.py:51` | Preserved |
| `RelayTuyaController.switch_binary(dev, on)` | `app/tuya/relay_tuya_controller.py:137` | Preserved |
| `RelayTuyaController.switch_device(dev, value)` | `app/tuya/relay_tuya_controller.py:146` | Preserved |
| `RelayDeviceManager.toggle_device(device_id, turn_on, tuya_controller)` | `app/devices/relay_device_manager.py:71` | Preserved |
| `SmartHomeController._switch_loop` | `app/service/smart_home_controller.py:93` | Preserved (unchanged) |

### 4.3 Must Not Edit

- `app/devices/pond_pump_controller.py` — leave as compatibility shim
- `app/devices/pump_power_map.py` — leave as legacy data
- `app/devices/relay_channel_device.py` — leave as legacy data
- `app/device_initializer.py` — defer pump config migration
- `app/tuya/relay_tuya_controller.py` — must not change (manual control APIs)
- `app/tuya/status_updater_async.py` — leave as compatibility shim
- `app/tuya/status_updater.py` — leave as compatibility shim
- `app/monitoring/device_status_logger.py` — leave as compatibility shim
- `app/ml/*` — forbidden in PR 0008
- `app/service/smart_home_controller.py` methods other than `__init__` and `start` — only gate logic changes
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.gitignore`
- `.github/workflows/build-and-deploy.yml`
- `app/docker/*`
- `config.json`, `devices.yaml`, `devices_prod.yaml`, `.env`, secrets
- Locked planning artifacts

### 4.4 Files Coder May Edit

| File | Change |
|---|---|
| `run.py` | Add `PUMP_AUTOMATION_ENABLED` env var check. Pass `pump_automation_enabled=...` to `SmartHomeController`. |
| `app/service/smart_home_controller.py` | Add `pump_automation_enabled` parameter to `__init__`. Gate `_pump_loop` task creation in `start()`. No other changes. |
| `scripts/check-pump-automation-disabled.sh` | Create — static validation for the pump automation gate. |
| `.github/workflows/validate.yml` | Add step: `bash scripts/check-pump-automation-disabled.sh`. |
| `.project-memory/CURRENT_STATE.md` | Add PR 0008 note that pump automation is disabled by default. |
| `.project-memory/pr/0008-disable-pump-automation-and-preserve-manual-switch-control/CODER_REPORT.txt` | Create — coder implementation report. |

### 4.5 Files Coder Must NOT Edit (Forbidden)

All files not listed in 4.4 above, especially:
- `app/devices/*` — all device model files
- `app/tuya/*` — all Tuya adapter files
- `app/monitoring/*` — all monitoring files
- `app/ml/*` — all ML/data files
- `app/device_initializer.py`
- `app/config.py`, `app/logger.py`
- All `service/*` and `shared_state/*`
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.gitignore`
- `.github/workflows/build-and-deploy.yml`
- `app/docker/*`
- `init-db.sql`, `requirements.txt`
- `.project-memory/pr/0008-.../PLAN.md` (locked)
- `.project-memory/pr/0008-.../PLAN_REVIEW.yaml` (locked)
- All `.project-memory/ADR/*`, `.project-memory/AGENT_STANDARD.txt`, `.project-memory/ORCHESTRATOR_STANDARD.txt`
- `scripts/check-repo-safety.sh`, `scripts/check-project-memory.sh`, `scripts/validate-yaml.py`, `scripts/check-runtime-smoke.py`, `scripts/check-image-publishing-boundary.sh`, `scripts/check-platform-control-redesign.sh`

### 4.6 Artifact Layout

```
.project-memory/pr/0008-disable-pump-automation-and-preserve-manual-switch-control/
    PLAN.md                    ← This file (planning agent, LOCKED)
    PLAN_REVIEW.yaml           ← Plan-review agent (LOCKED after approval)
    CODER_REPORT.txt           ← Coder agent implementation report
    PRECOMMIT_REVIEW.yaml      ← Precommit-review agent
```

### 4.7 Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are LOCKED after approval |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |

## 5. Detailed Implementation Specification

### 5.1 `app/service/smart_home_controller.py` Changes

**`__init__` signature change:**
```python
def __init__(
        self,
        dev_mgr: RelayDeviceManager,
        tuya_ctrl: RelayTuyaController,
        switch_int: int,
        pump_int: int,
        pump_automation_enabled: bool = False,
):
```

**Added lines inside `__init__`:**
```python
        self.pump_automation_enabled = pump_automation_enabled
```

**`start()` method change:**
```python
    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._stop.clear()
        self._tasks.append(loop.create_task(self._switch_loop()))
        if self.pump_automation_enabled:
            self._tasks.append(loop.create_task(self._pump_loop()))
```

**No other changes.** `_pump_loop` body, `PondPumpController` instantiation, `PumpPreset` enum, `pump_int` parameter storage — all remain unchanged.

### 5.2 `run.py` Changes

**Near line 67-69 (SmartHomeController construction), replace:**
```python
    smart_ctrl = SmartHomeController(
        dev_mgr=dev_mgr,
        tuya_ctrl=tuya_ctrl,
        switch_int=180,  # сек между проверками свитчей
        pump_int=120,  # сек между коррекцией насоса
    )
```

**With:**
```python
    pump_automation_enabled = os.getenv("PUMP_AUTOMATION_ENABLED", "").lower() in ("true", "1", "yes")

    smart_ctrl = SmartHomeController(
        dev_mgr=dev_mgr,
        tuya_ctrl=tuya_ctrl,
        switch_int=180,  # сек между проверками свитчей
        pump_int=120,  # сек между коррекцией насоса
        pump_automation_enabled=pump_automation_enabled,
    )

    important_log.info(
        f"[PUMP] Pump automation: {'ENABLED' if pump_automation_enabled else 'DISABLED'} "
        f"(set PUMP_AUTOMATION_ENABLED=true to enable)"
    )
```

### 5.3 Validation Script (`scripts/check-pump-automation-disabled.sh`)

**Requirements:**
- Check that `app/service/smart_home_controller.py` contains `pump_automation_enabled` parameter (with default `False`).
- Check that `create_task(self._pump_loop())` appears inside an `if` block (not unconditional).
- Check that `run.py` reads `PUMP_AUTOMATION_ENABLED` from environment.
- Check that manual switch methods still exist in `app/tuya/relay_tuya_controller.py`: `switch_on_device`, `switch_off_device`, `switch_binary`, `switch_device`.
- Exit 0 if all pass, 1 if any fail.
- Read-only, no network, no secrets, no mutations.

## 6. Validation Commands

Run these after implementation to verify correctness:

```bash
# 1. Verify pump automation is gated
grep -c "pump_automation_enabled" app/service/smart_home_controller.py
# Should output >= 2 (parameter + stored + conditionally used)

# 2. Verify _pump_loop is not unconditional
grep "create_task.*_pump_loop" app/service/smart_home_controller.py
# Should show the line inside an 'if' block

# 3. Verify run.py reads PUMP_AUTOMATION_ENABLED
grep "PUMP_AUTOMATION_ENABLED" run.py
# Should show the os.getenv check

# 4. Verify manual switch methods still exist
grep -q "def switch_on_device" app/tuya/relay_tuya_controller.py && echo "switch_on_device: EXISTS"
grep -q "def switch_off_device" app/tuya/relay_tuya_controller.py && echo "switch_off_device: EXISTS"
grep -q "def switch_binary" app/tuya/relay_tuya_controller.py && echo "switch_binary: EXISTS"
grep -q "def switch_device" app/tuya/relay_tuya_controller.py && echo "switch_device: EXISTS"

# 5. Run the static validation script
bash scripts/check-pump-automation-disabled.sh
echo "Exit code: $?"

# 6. Run all existing validations
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

# 7. Verify locked artifacts unchanged
git diff --name-only HEAD -- .project-memory/pr/0008-disable-pump-automation-and-preserve-manual-switch-control/PLAN.md
# Should produce no output

git diff --name-only HEAD -- .project-memory/pr/0008-disable-pump-automation-and-preserve-manual-switch-control/PLAN_REVIEW.yaml
# Should produce no output

# 8. Verify build-and-deploy workflow is NOT modified
git diff --name-only HEAD -- .github/workflows/build-and-deploy.yml
# Should produce no output

# 9. Verify forbidden files not changed
git diff --name-only HEAD -- app/devices/ app/tuya/ app/ml/ app/monitoring/ app/device_initializer.py Dockerfile docker-compose.yml
# Should produce no output

# 10. Verify coder artifact exists
test -f .project-memory/pr/0008-disable-pump-automation-and-preserve-manual-switch-control/CODER_REPORT.txt && echo "CODER_REPORT: OK"
```

## 7. CODER_REPORT.txt Requirements

The coder must produce `CODER_REPORT.txt` with these sections:
- `TASK COMPLETE`
- `BLOCKERS`
- `WARNINGS`
- `FILES CHANGED` (list of files created/modified)
- `VALIDATION RUN` (output of all validation commands from section 6)
- `DEVIATIONS FROM PLAN`
- `BOUNDARY CONFIRMATIONS`
- `NEXT REQUIRED ACTION`

## 8. Configuration and Operator Boundary

1. **Do not commit `devices.yaml`, `devices_prod.yaml`, `.env`, or real secrets.**
2. If the operator wants to fully remove pump config from `devices.yaml`, that is an **operator action outside this repository** (manual config edit on server or in external GitOps repo).
3. PR 0008 is safe even if old pump config still exists in `devices.yaml` — the pump loop simply does not start.
4. `PUMP_AUTOMATION_ENABLED` defaults to `False` when the env var is absent. Setting `PUMP_AUTOMATION_ENABLED=true` is allowed only for compatibility/testing, not default production behavior.

## 9. Data and ML Boundary

1. Existing pump historical data in SQLite/CSV/JSONL is legacy and must NOT be deleted in PR 0008.
2. ML pump controller code (`ml_model_training_example.py`, `MLDataPoint.pump_speed`, etc.) is obsolete but must NOT be removed in PR 0008.
3. ML control remains disabled per ADR-0003.
4. Generic telemetry migration is deferred to later PRs (PR 0013 per PLATFORM_CONTROL_REDESIGN.md).

## 10. Out of Scope — Later Work

| Work | Reason | Target PR |
|---|---|---|
| Delete `PondPumpController` | Leave as compatibility shim | PR 0011 |
| Delete `pump_power_map` | Leave as legacy data | PR 0013 |
| Rename pump to SwitchableLoad | Broad rename, not safe for this PR | PR 0009-0011 |
| Remove `pump_int` from `SmartHomeController.__init__` | Breaking API change, defer | PR 0011 |
| Remove pump special case from `device_initializer.py` | Config migration needed | PR 0010 |
| Remove pump monitoring from `device_status_logger.py` | Read-only, safe to defer | PR 0012 |
| Migrate ML data schema | Data migration risk | PR 0013 |
| Manual ON/OFF API | Requires UI/API planning | PR 0014 |
| ML control enablement | Blocked by ADR-0003 | Later |

## 11. Boundary Confirmations

- **No runtime behavior change beyond gating**: The only change is that `_pump_loop` is not started by default. `_switch_loop`, telemetry collection, Tuya status updates, ML data collection, and monitoring all continue unchanged.
- **Manual switch control preserved**: All 4 manual switch methods on `RelayTuyaController` and `RelayDeviceManager.toggle_device` are untouched.
- **No code deleted**: `PondPumpController`, `pump_power_map`, `_pump_loop` body — all remain as compatibility shims.
- **No config change**: `devices.yaml` still defines pump devices if present. The pump loop simply ignores them.
- **No ML change**: ML data collector still collects `pump_speed`/`pump_mode` if pump devices exist. No change to ML behavior.
- **No data deleted**: Historical pump data in SQLite/CSV/JSONL is untouched.
- **No Docker image publishing change**: `build-and-deploy.yml` untouched.
- **No external GitOps/ArgoCD change**: Applies image publishing boundary.
- **No ML control enabled**: Per ADR-0003.
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval.
- **Safe default**: Absent env var = pump loop disabled. Explicit `PUMP_AUTOMATION_ENABLED=true` required to enable.
