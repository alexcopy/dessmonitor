# PR 0002 — Runtime Critical Fixes

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `3f3aaad5401033546cc949eb35fd5c1776530165` |
| Branch | `git branch --show-current` | `0002-runtime-critical-fixes` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0002-runtime-critical-fixes` and working tree is clean.

## 2. Current Repository State (relevant to runtime stability)

- **Deployment pipeline**: GitHub Actions (`build-and-deploy.yml`) → Docker Hub (`redcopy/dessmonitor`) → ArgoCD → Kubernetes (namespace `dess`).
- **PR 0001 completed**: `.project-memory/` bootstrapped, `.gitignore`/`.dockerignore` strengthened, safety check script added.
- **Startup entry point**: `run.py` is the asyncio-based main entry point.
- **Key runtime components**: `DessAPI` (inverter data polling), `TimescaleDataCollector` (DB metrics), `MLDataCollector` (CSV fallback), `RelayTuyaController` (relay control), `OpenWeatherService` (weather), `SmartHomeController` (business logic).
- **Database**: TimescaleDB via `asyncpg`, optional CSV/SQLite fallback.

## 3. Verified Runtime-Critical Issues

Nine issues were investigated per the specification. The table below reports each finding.

### 3.1 Issue Verification Results

| # | Issue | Status | Evidence | In PR 0002? |
|---|---|---|---|---|
| 1 | `fetch_device_data` return path | **Not applicable** — always returns `DeviceData` from either primary API or fallback path. Both paths construct and return a valid `DeviceData` object. | `app/api.py` lines 171-187 (primary): always returns `dd`. Lines 189-251 (fallback): always returns `DeviceData`. | No |
| 2 | Credential logging in `app/api.py` | **Not a blocker for this PR** — email and `company_key` appear in the logged request URL (`_do_api_request` line 108: `self.logger.info(f"[API] Выполняем запрос: {url}")`). The raw password is NOT logged (only SHA-1 hash for signing). The project-manifest comment `# ⚠️ Credentials не логируются` refers to the password specifically. Need careful scoping for a credential-handling PR. | `app/api.py` line 108: URL is logged. Line 139-144: params include `usr` (email) and `company-key`. | No — requires a dedicated credential-hardening PR |
| 3 | TimescaleDB startup crash on missing `DATABASE_URL` | **Confirmed** — `run.py` constructs `TimescaleDataCollector(...)` on line 113-120 **without** passing `database_url`. The constructor (line 74-79 of `timescale_data_collector.py`) reads `os.getenv("DATABASE_URL")` and raises `ValueError` if absent. This will crash the entire application at startup even if TimescaleDB is not intended for use. No `TS_ENABLED` flag gates construction or the task creation. | `run.py` lines 113-120, line 178. `app/ml/timescale_data_collector.py` lines 74-79. | **Yes** |
| 4 | TimescaleDB startup guarded by env flag | **No such flag exists** — no `TS_ENABLED`, `TIMESCALE_ENABLED`, or equivalent environment variable gates the collector. The import, construction, and `timescale_collection_loop` task creation are all unconditional in `run.py`. | `run.py` lines 15, 113-120, 178. | **Yes** (part of fix for issue 3) |
| 5 | Malformed SQL, duplicated hypertable, schema mismatch in `timescale_data_collector.py` | **Partially confirmed** — SQL is syntactically valid but uses odd multi-line formatting. `create_hypertable` calls are wrapped in try/except and use `if_not_exists => TRUE`, so duplicates are safe. **Schema mismatch:** The code creates `weather_data` table with columns `clouds_pct` and `ambient_temp` — the `init-db.sql` materialized view references `forecast_3h_mean_clouds` and `forecast_6h_mean_clouds` which are NOT in the weather_data column list. This is a production schema error but only affects the ML training view (not production). | `init-db.sql` lines 152, 155 vs. weather_data columns (lines 39-67). `timescale_data_collector.py` `_create_tables()` method. | No — deferred to ML phase |
| 6 | `init-db.sql` references non-existent columns | **Confirmed** — materialized view `ml_solar_training_data` references `w.forecast_3h_mean_clouds` and `w.forecast_6h_mean_clouds` which do not exist in the `weather_data` table. Additionally, `tigo_system_metrics` table is referenced in the `FROM` clause but is never created anywhere in the schema. This view will fail on creation. | `init-db.sql` lines 94, 152, 155, 170. | No — deferred to ML phase |
| 7 | RelayTuyaController local status update after switching | **Not confirmed** — The `switch_on_device` and `switch_off_device` methods correctly update local status via `device.update_status({device.api_key: True/False})` and `device.mark_switched()` after the Tuya command succeeds. The `_send_switch_cmd` method correctly uses `device.tuya_device_id` for the devId. No bug found. | `app/tuya/relay_tuya_controller.py` lines 28-45 (switch_on/off), lines 8-21 (_send_switch_cmd). | No |
| 8 | Pandas boolean-precedence bug in `ml_data_analyzer.py` | **Confirmed** — Line 141 reads: `surplus > 100 & not_charging & battery_not_full`. In Python, `&` has higher operator precedence than `>`, so this evaluates as `surplus > (100 & not_charging & battery_not_full)` instead of the intended `(surplus > 100) & not_charging & battery_not_full`. This produces incorrect boolean indexing for optimality labeling. | `app/ml/ml_data_analyzer.py` line 141. | **Yes** |
| 9 | Tests or validation needed for fixes | **Repository has no test infrastructure** — no `tests/` directory, no test files, no CI test step. Adding full test framework is out of scope for PR 0002. The plan requires manual validation commands listed in section 8. | No `tests/` directory found. | No — deferred |

### 3.2 Summary of Issues in PR 0002 Scope

| Priority | Issue | File(s) to fix | Risk |
|---|---|---|---|
| **HIGH** | TimescaleDB startup crash (unconditional construction, missing `DATABASE_URL`) | `run.py`, `app/ml/timescale_data_collector.py` | Production startup failure |
| **LOW** | ML boolean-precedence bug | `app/ml/ml_data_analyzer.py` | Incorrect ML labeling (not production) |

## 4. PR 0002 Implementation Scope

### 4.1 Must Fix

1. **Gate TimescaleDB startup** — Add `TS_ENABLED` environment variable check in `run.py`. When `TS_ENABLED` is not set (or set to `false`/`0`), the TimescaleDB collector must not be constructed and its collection loop must not be scheduled.
2. **Graceful TimescaleDB initialization** — In `timescale_data_collector.py`, the `__init__` must not raise `ValueError` when `DATABASE_URL` is missing. Instead, defer the check to `initialize()` and return a clean `not_initialized` state.
3. **Fix boolean-precedence bug** — In `ml_data_analyzer.py` line 141, add parentheses around the `surplus > 100` comparison.

### 4.2 Must Not Fix

- `app/api.py` credential logging — requires a dedicated credential-hardening PR with careful scoping.
- `init-db.sql` schema mismatches — deferred to ML phase.
- Tuya controller changes — no confirmed bug.
- Test infrastructure — too broad for this PR.
- K8s/ArgoCD changes — out of scope by design.
- ML control enablement — blocked by ADR-0003.

### 4.3 Files Coder May Edit

| File | Change Description |
|---|---|
| `run.py` | Add `TS_ENABLED` env var check. Conditional construction and task creation for `TimescaleDataCollector` and `timescale_collection_loop`. |
| `app/ml/timescale_data_collector.py` | Move `DATABASE_URL` check from `__init__` to `initialize()`. Simplify `__init__` to accept optional `database_url` and store it without raising. |
| `app/ml/ml_data_analyzer.py` | Fix line 141: `surplus > 100 & not_charging & battery_not_full` → `(surplus > 100) & not_charging & battery_not_full`. |

### 4.4 Files Coder Must Not Edit

All files not listed in 4.3 above, **including but not limited to**:
- `app/api.py`, `app/tuya/relay_tuya_controller.py`, `app/weather/openweather_service.py`
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.gitignore`
- `init-db.sql`, `.github/workflows/build-and-deploy.yml`
- `app/docker/*`, `app/monitoring/*`, `service/*`, `shared_state/*`
- `.project-memory/ADR/*`, `.project-memory/AGENT_STANDARD.txt`, `.project-memory/ORCHESTRATOR_STANDARD.txt`
- `.project-memory/pr/0002-runtime-critical-fixes/PLAN.md` (locked)
- `.project-memory/pr/0002-runtime-critical-fixes/PLAN_REVIEW.yaml` (locked)

### 4.5 Artifact Layout

```
.project-memory/pr/0002-runtime-critical-fixes/
    PLAN.md                    ← This file (planning agent, LOCKED)
    PLAN_REVIEW.yaml           ← Plan-review agent (LOCKED after approval)
    CODER_REPORT.txt           ← Coder agent implementation report
    PRECOMMIT_REVIEW.yaml      ← Precommit-review agent
```

*PLAN_REVIEW.yaml*, *CODER_REPORT.txt*, and *PRECOMMIT_REVIEW.yaml* are produced by their respective agents.

### 4.6 Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes plan. |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are LOCKED after approval. |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must NOT edit PLAN.md or PLAN_REVIEW.yaml. |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must NOT edit PLAN.md or PLAN_REVIEW.yaml. |

## 5. Detailed Fix Specifications

### 5.1 Fix 1: Gate TimescaleDB Startup (`run.py`)

**Current behavior (bug):**
```python
# Line 113-120 — unconditional construction
ts_collector = TimescaleDataCollector(...)

# Line 178 — unconditional task creation
ml_db_task = asyncio.create_task(
    timescale_collection_loop(ts_collector, dev_mgr, stop_event)
)
```

**Required change:**
```python
# Check TS_ENABLED flag before construction
ts_enabled = os.getenv("TS_ENABLED", "").lower() in ("true", "1", "yes")

if ts_enabled:
    ts_collector = TimescaleDataCollector(...)
    ml_db_task = asyncio.create_task(
        timescale_collection_loop(ts_collector, dev_mgr, stop_event)
    )
    important_log.info("[ML DB] TimescaleDB collection enabled")
else:
    ts_collector = None
    ml_db_task = None
    important_log.info("[ML DB] TimescaleDB collection disabled (set TS_ENABLED=true to enable)")
```

Also update the shutdown section to skip cleanup when `ts_collector is None`.

### 5.2 Fix 2: Graceful Missing DATABASE_URL (`timescale_data_collector.py`)

**Current behavior (bug):**
```python
# __init__ raises ValueError immediately if DATABASE_URL is missing (lines 74-79)
```

**Required change:**
- In `__init__`, store the `database_url` argument (or `os.getenv("DATABASE_URL")` fallback) as `self.database_url` without raising if missing.
- In `initialize()`, check `if not self.database_url` and return `False` with a warning log.
- The `collect_data()` method already handles `if not self._is_initialized` gracefully (returns `{"status": "not_initialized"}`).

### 5.3 Fix 3: Boolean Precedence (`ml_data_analyzer.py`)

**Current (bug):**
```python
optimal.loc[surplus > 100 & not_charging & battery_not_full] = 0
```

**Required:**
```python
optimal.loc[(surplus > 100) & not_charging & battery_not_full] = 0
```

## 6. Validation Commands

Run these after implementation to verify correctness:

```bash
# 1. Verify the files were edited
grep -n "TS_ENABLED" run.py
grep -n "database_url" app/ml/timescale_data_collector.py  # should NOT raise ValueError
grep -n "surplus > 100" app/ml/ml_data_analyzer.py  # should show parentheses

# 2. Python syntax check
python3 -c "import ast; ast.parse(open('run.py').read()); print('run.py: OK')"
python3 -c "import ast; ast.parse(open('app/ml/timescale_data_collector.py').read()); print('timescale_data_collector.py: OK')"
python3 -c "import ast; ast.parse(open('app/ml/ml_data_analyzer.py').read()); print('ml_data_analyzer.py: OK')"

# 3. Verify TS_ENABLED gating works
# Check that when TS_ENABLED is not set, TimescaleDB collector is not constructed
grep -c "ts_collector = TimescaleDataCollector" run.py  # should be 1 (inside if block)

# 4. Verify that injectable ValueError is gone from __init__
python3 -c "
import ast, sys
tree = ast.parse(open('app/ml/timescale_data_collector.py').read())
for node in ast.walk(tree):
    if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
        if hasattr(node.exc.func, 'id') and node.exc.func.id == 'ValueError':
            print('WARNING: raise ValueError found — check context')
            sys.exit(1)
print('No top-level ValueError raise found — OK (check it is inside initialize())')
"

# 5. Verify precommit-review artifact exists
test -f .project-memory/pr/0002-runtime-critical-fixes/PRECOMMIT_REVIEW.yaml
```

## 7. Out of Scope — Later Work

| Work | Reason | Target |
|---|---|---|
| `app/api.py` credential/URL logging exposure | Needs dedicated credential-hardening PR | PR 0003+ |
| `init-db.sql` schema mismatch (mean_clouds, tigo_system_metrics) | ML materialized view, not used in production | ML phase (PR 0005+) |
| Test infrastructure | Too broad, needs test framework decision | PR 0003+ |
| RelayTuyaController changes | No confirmed bug | N/A |
| K8s manifest consolidation | ADR-0002 identifies this for later | PR 0003+ |
| ArgoCD `:latest` tag | ADR-0002 identifies this for later | PR 0003+ |
| ML control enablement | Blocked by ADR-0003 | PR 0005+ |

## 8. Boundary Confirmations

- **This PR does not alter the deployment pipeline** (GitHub Actions → Docker Hub → ArgoCD).
- **This PR does not enable ML control** — the `ml_data_analyzer.py` fix is a data-labeling bug, not control enablement.
- **This PR does not restructure Kubernetes or ArgoCD** — no changes to `app/docker/` or K8s manifests.
- **This PR does not enable TimescaleDB in production** — the `TS_ENABLED` flag defaults to disabled. Existing behavior changes only when `TS_ENABLED=true` is explicitly set. Without the flag, TimescaleDB construction is skipped entirely.
- **This PR does not change Docker Hub publishing, image tags, or CI workflows.**
- **This PR does not rotate credentials** — credential exposure in `api.py` URL logging is documented for later work.
- **Plan.md and PLAN_REVIEW.yaml are LOCKED** — coder and precommit-review agents must not edit them.

## 9. CODER_REPORT.txt Requirements

The coder must produce `CODER_REPORT.txt` with these sections:
- `TASK COMPLETE`
- `BLOCKERS`
- `WARNINGS`
- `FILES CHANGED` (list of files modified)
- `VALIDATION RUN` (output of all validation commands)
- `DEVIATIONS FROM PLAN`
- `BOUNDARY CONFIRMATIONS`
- `NEXT REQUIRED ACTION`
