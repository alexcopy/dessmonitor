# PR 0030 — Runtime Read-Only Web Host Startup

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `1b92a325637719f4f0800d97a0d96b42d589c48b` |
| Branch | `git branch --show-current` | `0030-runtime-read-only-web-host-startup` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0030-runtime-read-only-web-host-startup` and working tree is clean.

## 2. Purpose

PR 0030 adds a **standalone read-only web host startup module** (`app/web_host_startup.py`)
that can be invoked manually via `python -m app/web_host_startup`. It wraps the existing
`app.web_host.create_app()` with a startup function that optionally accepts a runtime state
provider, and provides a uvicorn-based entry point.

This PR does NOT modify `run.py`, `api.py`, Docker, deployment, or container startup files.
The startup module is a manual/diagnostic entry point only — production deployment wiring
requires a separate safety-reviewed PR.

## 3. Product Context

1. PR 0028b created `app/web_host.py` with `create_app()`.
2. PR 0029 added the injectable runtime state provider.
3. PR 0030 adds `app/web_host_startup.py` for manual/diagnostic server startup.
4. The existing container startup and automation behavior (`run.py` → asyncio main loop)
   remains completely unchanged.

## 4. Required Module Design

### Module Location

**Create:** `app/web_host_startup.py`

### Public API

```python
WEB_HOST_STARTUP_READ_ONLY_MODE: bool = True
DEFAULT_WEB_HOST: str = "0.0.0.0"
DEFAULT_WEB_PORT: int = 8000

def create_startup_app(
    runtime_state_provider: Callable[[], dict[str, object] | None] | None = None,
) -> "FastAPI":
    ...

def run_read_only_web_host(
    host: str = DEFAULT_WEB_HOST,
    port: int = DEFAULT_WEB_PORT,
    runtime_state_provider: Callable[[], dict[str, object] | None] | None = None,
) -> None:
    ...
```

### Module-level entry point

```python
if __name__ == "__main__":
    run_read_only_web_host()
```

### Behavior

1. `create_startup_app(runtime_state_provider=None)` calls `app.web_host.create_app()` with the
   given `runtime_state_provider`.
2. `run_read_only_web_host()`:
   - Imports `uvicorn` lazily inside the function.
   - If `uvicorn` is unavailable, raises `RuntimeError("uvicorn-unavailable")`.
   - Creates the app via `create_startup_app(runtime_state_provider)`.
   - Calls `uvicorn.run(app, host=host, port=port)`.
3. No `run.py`/`api.py` wiring.
4. No Dockerfile, docker-compose.yml, or container entrypoint changes.
5. No write routes.
6. No hardware execution.
7. No direct `shared_state`/device reads.
8. No reload or multiple workers.
9. No startup hardware actions.
10. Existing autonomous container behavior (`run.py` → asyncio main loop) remains unchanged.

### Dependencies

- `app.web_host` — `create_app`.
- `uvicorn` — lazy import inside `run_read_only_web_host`.
- Python standard library.

Must NOT import: `app.service`, `app.devices`, `app.tuya`, `app.monitoring`,
`app.ml`, `app.weather`, `shared_state`.

## 5. Required Markers

- `"read-only-web-host-startup"`
- `"manual-startup-only"`
- `"existing-container-startup-unchanged"`
- `"no-run-py-wiring"`
- `"no-container-entrypoint-change"`
- `"no-deployment-wiring"`
- `"no-write-api"`
- `"no-execution"`
- `"no-tuya-hardware"`
- `"uvicorn-lazy-import"`
- `"uvicorn-unavailable"`
- `"ml-control-deferred"`

## 6. Allowed Implementation Files

| File | Action |
|---|---|
| `app/web_host_startup.py` | **Create** |
| `.project-memory/WEB_HOST_STARTUP.md` | **Create** |
| `scripts/check-web-host-startup.sh` | **Create** |
| `.github/workflows/validate.yml` | **Edit** |
| `.project-memory/CURRENT_STATE.md` | **Edit** |
| `.project-memory/ROADMAP.md` | **Edit** |
| `.project-memory/pr/0030-runtime-read-only-web-host-startup/CODER_REPORT.txt` | **Create** |

## 7. Forbidden Implementation Files

Standard frozen list. Plus: `run.py`, `api.py` must NOT be modified.

## 8. Static Validation Script

`scripts/check-web-host-startup.sh` must:
1. Check `app/web_host_startup.py` exists.
2. Check `create_startup_app` exists.
3. Check `run_read_only_web_host` exists.
4. Check `DEFAULT_WEB_HOST` and `DEFAULT_WEB_PORT` exist.
5. Check `WEB_HOST_STARTUP_READ_ONLY_MODE` exists and is `True`.
6. Check `uvicorn` lazy import exists inside `run_read_only_web_host`.
7. Check `uvicorn.run` call exists.
8. Check `uvicorn-unavailable` error exists.
9. Check `if __name__ == "__main__"` entry point exists.
10. Check no `run.py` or `api.py` modification.
11. Check required markers present.
12. Print clear output.
13. Exit 0 only on pass.

## 9. CURRENT_STATE.md Update

```
## PR 0030 — Runtime Read-Only Web Host Startup
PR 0030 adds a standalone read-only web host startup module in
`app/web_host_startup.py` with `create_startup_app()`,
`run_read_only_web_host()`, and a `python -m` entry point.
The startup module is manual/diagnostic only. No run.py/api.py wiring.
No Dockerfile, docker-compose.yml, or container entrypoint changes.
Existing container startup and automation behavior unchanged.
No write API. No execution.
```

## 10. ROADMAP.md Update

```
- [x] PR 0030 — Runtime read-only web host startup
```

## 11. Validation

| Check | Result |
|---|---|
| `python3 -m compileall -q .` | exit 0 |
| `scripts/check-repo-safety.sh` | exit 0 |
| `scripts/check-project-memory.sh` | exit 0 |
| `scripts/validate-yaml.py` | exit 0 |
| `scripts/check-web-host-bootstrap.sh` | exit 0 |
| `scripts/check-web-control-state-provider.sh` | exit 0 (28/28) |

## 12. Validation Checklist

| Check | Result |
|---|---|
| `test -f PLAN.md` | PASS |
| `grep -q "app/web_host_startup.py" PLAN.md` | PASS |
| `grep -q "run_read_only_web_host" PLAN.md` | PASS |
| `grep -q "uvicorn-lazy-import" PLAN.md` | PASS |
| `grep -q "no-run-py-wiring" PLAN.md` | PASS |
| `grep -q "no-write-api" PLAN.md` | PASS |

## 13. Boundary Confirmations

- **Manual startup only**: `python -m app.web_host_startup`, not wired into run.py
- **No runtime wiring**: Does not modify `run.py` or `api.py`
- **No deployment changes**: No Docker or deployment file edits
- **No write API**: No POST/PUT/PATCH/DELETE
- **No hardware execution**: Does not call any switch/device/Tuya method
- **No ML control**: ML control deferred per ADR-0003
- **All existing modules unchanged**: PR 0014–0029 frozen
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval
