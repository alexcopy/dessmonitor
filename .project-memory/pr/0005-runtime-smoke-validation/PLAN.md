# PR 0005 — Runtime Smoke Validation

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `1157f178ecc3a068fe46f2833394ef618d6c4e00` |
| Branch | `git branch --show-current` | `0005-runtime-smoke-validation` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0005-runtime-smoke-validation` and working tree is clean.

## 2. Current Validation Baseline

### 2.1 Current CI/CD Deployment Pipeline

The current production deployment pipeline is:

```
[Git push to master]
       ↓
[GitHub Actions: build-and-deploy.yml]
       ↓  builds Docker image
[Docker Hub: redcopy/dessmonitor:{latest,sha}]
       ↓  ArgoCD syncs
[Kubernetes cluster: namespace dess]
       ↓  applies
[app/docker/dessmonitor-deploy.yaml + other manifests]
```

PR 0005 does NOT change this pipeline. The `validate.yml` workflow is additive and validation only. It never publishes images, connects to Docker Hub, or modifies ArgoCD.

### 2.2 What PR 0003 and PR 0004 Established

PR 0003 added four validation checks, all running in CI (`validate.yml`) and locally:

| Check | Tool | What It Catches |
|---|---|---|
| Python syntax | `python -m compileall -q .` | Syntax errors, missing imports in __init__.py resolution |
| YAML syntax | `scripts/validate-yaml.py` | Invalid YAML in 22 YAML files |
| Repository safety | `scripts/check-repo-safety.sh` | Tracked secrets, runtime data, .dockerignore gaps |
| Project-memory structure | `scripts/check-project-memory.sh` | Missing governance files |

PR 0004 fixed the single pre-existing YAML syntax error so that `validate-yaml.py` now exits with code 0.

### 2.2 Remaining Gap

`compileall` checks *syntax* only — it imports nothing. It will not catch:
- `ModuleNotFoundError` at runtime due to missing transitive dependencies.
- `ImportError` from modules that execute unsafe code at import time.
- Broken module-level initialization that assumes files exist, configs are present, or external services are available.

A **runtime smoke validation** step is needed to verify that the project's Python modules can be safely imported without starting the application server or requiring external services.

## 3. Module Import Safety Analysis

Each module was inspected for import-time side effects. The analysis determines whether it can be imported safely in a smoke script without requiring config files, device definitions, network access, or external services.

### 3.1 Safe to Import (no side effects)

| Module | Justification |
|---|---|
| `run.py` | All code in function/class definitions + `if __name__ == "__main__"` guard. Safe. |
| `app/api.py` | Module-level code: `Path(os.getenv("MONITOR_TOKEN_PATH", "app/cache/dess_token.json"))` — `getenv` returns `None` without side effects. `Path()` construction is safe. No instantiation. Safe. |
| `app/ml/timescale_data_collector.py` | Module-level code: `try: import asyncpg` with `except ImportError` — graceful. `logging.getLogger(__name__)` — safe. Class + enum definitions only. Safe. |
| `app/ml/ml_data_analyzer.py` | Standard library + pandas/matplotlib imports. No module-level code execution. Safe. |
| `app/weather/openweather_service.py` | `aiohttp` import, `shared_state` import, class definition. Safe. |
| `app/tuya/relay_tuya_controller.py` | Imports `RelayChannelDevice` and `shared_state`. No module-level side effects. Safe. |
| `app/monitoring/device_status_logger.py` | Imports `colorama.init()` which is safe. No file I/O at import time. Safe. |

### 3.2 Safe Only with Environment Guard

| Module | Justification | Guard Strategy |
|---|---|---|
| `app/ml/timescale_data_collector.py` | The module-level `try: import asyncpg` is safe, and class definition has no side effects. The `__init__` reads `os.getenv("DATABASE_URL")` but this is not called at import time. However, `asyncpg` will not be installed in CI without `pip install -r requirements.txt`. The `Asyncpg` optional import (`ASYNCPG_AVAILABLE = False`) handles this gracefully. | Ensure `pip install -r requirements.txt` runs before smoke validation. Set `TS_ENABLED=false` in environment to prevent any construction-time path being exercised. |

### 3.3 Unsafe to Import in Smoke Validation

| Module | Justification |
|---|---|
| `app/tuya/tuya_authorisation.py` | **Module-level side effects.** Lines 10-15 execute at import time: `config = Config()` (reads `config.json`, calls `sys.exit(1)` if missing), `DeviceInitializer().get_tuya_config()` (reads `devices.yaml`), `ACCESS_ID = tuya_config.get("ACCESS_ID")`. This module will crash on import if `config.json` or `devices.yaml` are absent. Cannot be imported safely without real config files. |

### 3.4 Defer to Later PR

| Module | Justification |
|---|---|
| None | All other modules are covered by the safe or guarded categories. |

## 4. Smoke Validation Strategy

### 4.1 Approach

Create a single Python script `scripts/check-runtime-smoke.py` that:

1. Sets safe environment defaults (no secrets, no config paths, disabled optional services).
2. Iterates over a curated list of module import paths.
3. Attempts `importlib.import_module()` for each.
4. Skips modules known to be unsafe (`app.tuya.tuya_authorisation`).
5. Reports success/failure for each import.
6. Exits 0 only if all safe imports succeed.

### 4.2 What It Does NOT Do

- Does NOT start the application server or any background task.
- Does NOT call Tuya, DESS, OpenWeather, TimescaleDB, Docker, or Kubernetes.
- Does NOT require secrets or device config files.
- Does NOT require network access.
- Does NOT mutate repository files or write runtime cache files.
- Does NOT create logs, SQLite, CSV, JSONL, or ml_data.
- Does NOT import `app.tuya.tuya_authorisation` (unsafe at module level).

### 4.3 What It Checks

The script attempts to import the following modules (all verified safe in section 3):

```
app.api
app.config
app.ml.timescale_data_collector
app.ml.ml_data_analyzer
app.ml.ml_data_collector
app.tuya.relay_tuya_controller
app.weather.openweather_service
app.monitoring.device_status_logger
app.device_initializer
app.logger
shared_state.shared_state
service.inverter_monitor
```

If all import successfully, the script reports success and exits 0. If any import fails, it prints an actionable error and exits 1.

### 4.4 Script Design

**File:** `scripts/check-runtime-smoke.py`

```python
#!/usr/bin/env python3
"""
Runtime smoke validation for dessmonitor.
Verifies that all safe Python modules can be imported without requiring
config files, device definitions, network access, or external services.
Does NOT start the application server.
"""

import importlib
import os
import sys

# Modules known to be unsafe for smoke validation (import-time side effects)
UNSAFE_MODULES = {
    "app.tuya.tuya_authorisation",  # Config() and DeviceInitializer() at module level
}

# All other project modules to verify
MODULES_TO_CHECK = [
    "app.api",
    "app.config",
    "app.ml.timescale_data_collector",
    "app.ml.ml_data_analyzer",
    "app.ml.ml_data_collector",
    "app.tuya.relay_tuya_controller",
    "app.weather.openweather_service",
    "app.monitoring.device_status_logger",
    "app.device_initializer",
    "app.logger",
    "shared_state.shared_state",
    "service.inverter_monitor",
]


def safe_env() -> None:
    """Set safe environment defaults to prevent accidental initialization."""
    os.environ.setdefault("TS_ENABLED", "false")
    # Other env vars are left unset — the code must handle missing values gracefully
    # MONITOR_CONFIG_JSON, MONITOR_CONFIG_PATH, etc. are deliberately not set


def main() -> int:
    safe_env()

    errors: list[str] = []

    for mod_name in MODULES_TO_CHECK:
        try:
            importlib.import_module(mod_name)
            print(f"  ✅ {mod_name}")
        except Exception as exc:
            errors.append(f"  ❌ {mod_name}: {exc}")
            print(f"  ❌ {mod_name}: {exc}")

    # Report unsafe modules (informational only — not checked)
    for mod_name in sorted(UNSAFE_MODULES):
        print(f"  ⏭️  {mod_name} (skipped — import-time side effects)")

    print()
    if errors:
        print(f"❌ {len(errors)} module(s) failed to import.")
        return 1

    print(f"✅ All {len(MODULES_TO_CHECK)} module(s) imported successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### 4.5 Safe Environment Defaults

The script sets only `TS_ENABLED=false` to prevent any TimescaleDB-related constructor logic from being exercised. It does NOT set:
- `MONITOR_CONFIG_JSON` — intentionally absent, tests that `Config()` handles missing config gracefully in isolated import.
- `MONITOR_CONFIG_PATH` — same.
- `MONITOR_TOKEN_PATH` — defaults to `app/cache/dess_token.json`, which doesn't exist during import — safe because `Path()` construction doesn't read the file.
- `DATABASE_URL` — intentionally absent; `TimescaleDataCollector.__init__()` is not called at import time.
- `OPENWEATHER_API_KEY` — intentionally absent; import doesn't instantiate.

## 5. PR 0005 Implementation Scope

### 5.1 Must Create

| File | Description |
|---|---|
| `scripts/check-runtime-smoke.py` | Runtime smoke validation script (described in section 4.4) |

### 5.2 Must Edit

| File | Change |
|---|---|
| `.github/workflows/validate.yml` | Add a step to run `python scripts/check-runtime-smoke.py` after the existing validation steps. This step runs after `pip install -r requirements.txt` so all dependencies are available. The workflow remains validation-only, does not build Docker images, and does not require Docker Hub credentials. |

### 5.3 Must Not Edit

All other files, **including but not limited to**:
- `run.py` and all files under `app/`, `service/`, `shared_state/`
- `.github/workflows/build-and-deploy.yml`
- `scripts/validate-yaml.py`, `scripts/check-repo-safety.sh`, `scripts/check-project-memory.sh`
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.gitignore`
- `init-db.sql`, `requirements.txt`
- `.project-memory/` standards, ADRs, ROADMAP.md, CURRENT_STATE.md
- `.project-memory/pr/0005-runtime-smoke-validation/PLAN.md` (locked)
- `.project-memory/pr/0005-runtime-smoke-validation/PLAN_REVIEW.yaml` (locked)

### 5.4 Artifact Layout

```
.project-memory/pr/0005-runtime-smoke-validation/
    PLAN.md                    ← This file (planning agent, LOCKED)
    PLAN_REVIEW.yaml           ← Plan-review agent (LOCKED after approval)
    CODER_REPORT.txt           ← Coder agent implementation report
    PRECOMMIT_REVIEW.yaml      ← Precommit-review agent
```

### 5.5 Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are LOCKED after approval |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |

## 6. Ultimate Validate Workflow

After PR 0005, the `.github/workflows/validate.yml` workflow will be:

```yaml
name: Validate

on:
  pull_request:
    branches: [ master ]
  push:
    branches: [ master ]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - name: ⬇️  Checkout
        uses: actions/checkout@v4

      - name: 🔧 Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: 📦 Install dependencies
        run: pip install -r requirements.txt

      - name: 🔍 Python syntax validation
        run: python -m compileall -q . -x '/(ml_data|\.project-memory|\.git|venv|\.venv)/'

      - name: 🔍 YAML syntax validation
        run: python scripts/validate-yaml.py

      - name: 🔍 Repository safety check
        run: bash scripts/check-repo-safety.sh

      - name: 🔍 Project-memory structure check
        run: bash scripts/check-project-memory.sh

      - name: 🔍 Runtime smoke validation
        run: python scripts/check-runtime-smoke.py
```

## 7. Validation Commands

Run these after implementation to verify correctness:

```bash
# 1. Verify the smoke script exists
test -f scripts/check-runtime-smoke.py && echo "EXISTS" || echo "MISSING"

# 2. Run the smoke validation (must pass)
python scripts/check-runtime-smoke.py
echo "Exit code: $?"

# 3. Verify compileall still passes
python -m compileall -q . -x '/(ml_data|\.project-memory|\.git|venv|\.venv)/'
echo "Exit code: $?"

# 4. Verify YAML validation still passes
python scripts/validate-yaml.py
echo "Exit code: $?"

# 5. Verify repository safety still passes
bash scripts/check-repo-safety.sh
echo "Exit code: $?"

# 6. Verify project-memory structure
bash scripts/check-project-memory.sh
echo "Exit code: $?"

# 7. Verify only the allowed files changed
git diff --name-only
# Should show: scripts/check-runtime-smoke.py and .github/workflows/validate.yml

# 8. Verify locked artifacts unchanged
git diff --name-only HEAD -- .project-memory/pr/0005-runtime-smoke-validation/PLAN.md
# Should produce no output

git diff --name-only HEAD -- .project-memory/pr/0005-runtime-smoke-validation/PLAN_REVIEW.yaml
# Should produce no output

# 9. Verify build-and-deploy workflow is NOT modified
git diff --name-only HEAD -- .github/workflows/build-and-deploy.yml
# Should produce no output

# 10. Verify coder artifact exists
test -f .project-memory/pr/0005-runtime-smoke-validation/CODER_REPORT.txt && echo "CODER_REPORT: OK"

# 11. Verify validate.yml has no docker steps
grep -c "docker" .github/workflows/validate.yml
# Should output: 0
```

## 8. CODER_REPORT.txt Requirements

The coder must produce `CODER_REPORT.txt` with these sections:
- `TASK COMPLETE`
- `BLOCKERS`
- `WARNINGS`
- `FILES CHANGED` (list of files created/modified)
- `VALIDATION RUN` (output of all validation commands from section 7)
- `DEVIATIONS FROM PLAN`
- `BOUNDARY CONFIRMATIONS`
- `NEXT REQUIRED ACTION`

## 9. Out of Scope — Later Work

| Work | Reason | Target |
|---|---|---|
| `app/tuya/tuya_authorisation.py` refactoring | Module-level side effects need careful redesign | PR 0006+ |
| Unit / integration tests | Requires test framework decision, too broad | PR 0006+ |
| Linting (flake8, pylint, mypy) | Requires config decisions, more invasive | PR 0006+ |
| K8s manifest consolidation | ADR-0002, deferred | PR 0007+ |
| ML control enablement | Blocked by ADR-0003 | PR 0008+ |

## 10. Boundary Confirmations

- **No runtime behavior changes**: No edits to `run.py`, `app/`, `service/`, `shared_state/`.
- **Deployment pipeline preserved**: `build-and-deploy.yml` unchanged. `validate.yml` gets one additive step. The workflow remains validation-only with no Docker steps.
- **No Docker image publishing changes**: No edits to Docker configuration or build workflows.
- **No ArgoCD or K8s changes**: No edits to `app/docker/`.
- **No ML control enabled**: ML code is untouched.
- **No dependency changes**: `requirements.txt` unchanged. All needed imports (pandas, aiohttp, etc.) are already listed.
- **No secrets or credentials**: The smoke script deliberately does not read config files, device YAML, or environment secrets. It sets only `TS_ENABLED=false`.
- **No file mutation**: The script is read-only. It does not write cache files, logs, SQLite, CSV, JSONL, or ml_data.
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml are locked after approval.
- **Unsafe module excluded**: `app.tuya.tuya_authorisation` is explicitly excluded from import checking due to module-level side effects. This is documented for a future PR.
