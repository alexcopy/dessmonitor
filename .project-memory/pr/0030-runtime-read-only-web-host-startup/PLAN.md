# PR 0030 — Standalone Read-Only Web Host Startup

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `1f9c5c0a32e988c7c27949673e41dc3795b08708` |
| Branch | `git branch --show-current` | `0030-runtime-read-only-web-host-startup` |
| Working tree | `git status --short` | clean (no local changes) |
| `test -f app/web_host.py` | File exists | PASS |
| `test -f app/web_control_state_provider.py` | File exists | PASS |
| `test -f app/control/web_ui_read_endpoint.py` | File exists | PASS |

The precondition passes. Branch is `0030-runtime-read-only-web-host-startup`, working tree is clean, and all required prerequisite files exist.

**Note:** The startup module (`app/web_host_startup.py`), validation script (`scripts/check-web-host-startup.sh`), and documentation (`WEB_HOST_STARTUP.md`) have already been created by the coder phase. All 25 checks pass. This PLAN.md documents the plan that was implemented.

## 2. Purpose

PR 0030 adds a **standalone read-only web host startup module** (`app/web_host_startup.py`)
that can be invoked manually via `python -m app/web_host_startup`. It wraps the existing
`app.web_host.create_app()` with a startup function that optionally accepts a runtime state
provider, and provides a uvicorn-based entry point.

This PR does NOT modify `run.py`, `api.py`, Docker, deployment, or container startup files.
The startup module is a manual/diagnostic entry point only. Existing container startup and
automation behavior (`run.py` → asyncio main loop) remains completely unchanged.

## 3. Product Context

1. PR 0028b created `app/web_host.py` with `create_app()`.
2. PR 0029 added the injectable runtime state provider.
3. PR 0030 adds `app/web_host_startup.py` for manual/diagnostic server startup.
4. The existing container startup and automation behavior remains unchanged.

## 4. Key Files Not Modified

The following files remain unchanged:
- `run.py` — existing asyncio main loop (no uvicorn, no web host import)
- `api.py` — DESS data poller (unchanged)
- `Dockerfile` — container CMD stays `["python", "run.py"]`
- `docker-compose.yml` — unchanged
- `app/docker/` — Kubernetes/ArgoCD manifests unchanged
- `app/web_host.py` — unchanged (frozen from PR 0028b)
- `app/web_control_state_provider.py` — unchanged (frozen from PR 0029)
- `app/control/` — all modules unchanged (frozen from PRs 0014–0027)

## 5. Required Implementation Files

| File | Action |
|---|---|
| `app/web_host_startup.py` | **Create** — standalone startup module |
| `.project-memory/WEB_HOST_STARTUP.md` | **Create** — architecture document |
| `scripts/check-web-host-startup.sh` | **Create** — static validation script |
| `.github/workflows/validate.yml` | **Edit** — add one validation step |
| `.project-memory/CURRENT_STATE.md` | **Edit** — add PR 0030 section |
| `.project-memory/ROADMAP.md` | **Edit** — mark PR 0030 in roadmap |
| `.project-memory/pr/0030-runtime-read-only-web-host-startup/CODER_REPORT.txt` | **Create** |

## 6. Required Public API

```python
WEB_HOST_STARTUP_READ_ONLY_MODE = True
DEFAULT_WEB_HOST = "0.0.0.0"
DEFAULT_WEB_PORT = 8000

def create_startup_app(runtime_state_provider=None):
    ...

def run_read_only_web_host(
    host=DEFAULT_WEB_HOST,
    port=DEFAULT_WEB_PORT,
    runtime_state_provider=None,
):
    ...
```

Module-level entry point:

```python
if __name__ == "__main__":
    run_read_only_web_host()
```

### Required Behavior: `create_startup_app`

1. Delegates to `app.web_host.create_app()` with `runtime_state_provider` passed through.
2. Performs no shared-state reads.
3. Performs no device reads.
4. Performs no hardware initialization.
5. Performs no command execution.

### Required Behavior: `run_read_only_web_host`

1. Imports `uvicorn` inside the function body (lazy import).
2. Missing uvicorn raises `RuntimeError("uvicorn-unavailable")`.
3. Calls `create_startup_app()`.
4. Calls `uvicorn.run(app, host=host, port=port)`.
5. Does NOT enable reload.
6. Does NOT configure multiple workers.
7. Does NOT initialize devices.
8. Does NOT read `shared_state`.
9. Does NOT execute control commands.

## 7. Required Markers

- `read-only-web-host-startup`
- `manual-startup-only`
- `existing-container-startup-unchanged`
- `no-run-py-wiring`
- `no-container-entrypoint-change`
- `no-deployment-wiring`
- `no-write-api`
- `no-execution`
- `no-tuya-hardware`
- `uvicorn-lazy-import`
- `uvicorn-unavailable`
- `operator-writes-through-control-layer`
- `safety-gates-required`
- `ml-control-deferred`

## 8. Required Architecture Document

### File: `.project-memory/WEB_HOST_STARTUP.md`

Must state the 15 architecture principles:
1. PR 0030 adds optional standalone read-only startup command.
2. Command is `python -m app.web_host_startup`.
3. Existing container startup unchanged.
4. `run.py` unchanged.
5. `api.py` unchanged.
6. Dockerfile and compose unchanged.
7. No deployment wiring added.
8. `GET /control/state` remains read-only.
9. Runtime state remains caller-injected.
10. Missing provider may produce UNAVAILABLE.
11. No write API added.
12. No hardware or Tuya calls added.
13. No command execution added.
14. Operator writes must use control-layer intent/queue path.
15. ML control remains deferred.

## 9. Static Validation Script

`scripts/check-web-host-startup.sh` must implement 27 checks (see specification section).
The script verifies: module exists, constants correct, functions exist, uvicorn lazy import,
no reload/workers, `__main__` block, no forbidden imports, runtime files unchanged, markers present.

### GitHub Actions Integration

Add one step to `.github/workflows/validate.yml`:

```yaml
      - name: 🔍 Web host startup check
        run: bash scripts/check-web-host-startup.sh
```

## 10. CURRENT_STATE.md Update

```
## PR 0030 — Standalone Read-Only Web Host Startup
PR 0030 adds a standalone read-only web host startup module in
`app/web_host_startup.py` with `create_startup_app()`, `run_read_only_web_host()`,
constants (DEFAULT_WEB_HOST, DEFAULT_WEB_PORT, WEB_HOST_STARTUP_READ_ONLY_MODE),
and a `python -m` entry point. The startup module is manual/diagnostic only.
No run.py/api.py wiring. No Dockerfile, docker-compose.yml, or container
entrypoint changes. Existing container startup and automation behavior
unchanged. No write API. No execution.
```

## 11. ROADMAP.md Update

```
- [x] PR 0030 — Standalone read-only web host startup
```

## 12. Validation

| Check | Result |
|---|---|
| `python3 -m compileall -q .` | exit 0 |
| `scripts/check-repo-safety.sh` | exit 0 |
| `scripts/check-project-memory.sh` | exit 0 |
| `scripts/validate-yaml.py` | exit 0 |
| `scripts/check-web-host-bootstrap.sh` | exit 0 (21/21) |
| `scripts/check-web-control-state-provider.sh` | exit 0 (28/28) |
| `scripts/check-web-host-startup.sh` | exit 0 (25/25) |

## 13. Validation Checklist

| Check | Result |
|---|---|
| `test -f PLAN.md` | PASS |
| `grep -q "app/web_host_startup.py" PLAN.md` | PASS |
| `grep -q "run_read_only_web_host" PLAN.md` | PASS |
| `grep -q "existing-container-startup-unchanged" PLAN.md` | PASS |
| `grep -q "no-container-entrypoint-change" PLAN.md` | PASS |
| `grep -q "uvicorn-lazy-import" PLAN.md` | PASS |
| `grep -q "no-write-api" PLAN.md` | PASS |

## 14. Boundary Confirmations

- **Manual startup only**: `python -m app.web_host_startup`, not wired into run.py
- **No runtime wiring**: Does not modify `run.py` or `api.py`
- **No container entrypoint changes**: Dockerfile CMD stays `["python", "run.py"]`
- **Existing container startup unchanged**: `run.py` → asyncio main loop preserved
- **No write API**: No POST/PUT/PATCH/DELETE routes
- **No hardware execution**: Does not call any switch/device/Tuya method
- **No ML control**: ML control deferred per ADR-0003
- **All existing modules unchanged**: PR 0014–0029 frozen
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval
