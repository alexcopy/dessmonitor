# PR 0028b — Minimal Read-Only FastAPI Web Host Bootstrap

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `10518d5d8bf3f79acd2941ef9ac52dbda4318a33` |
| Branch | `git branch --show-current` | `0028b-bootstrap-read-only-web-host` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0028b-bootstrap-read-only-web-host` and working tree is clean.

## 2. Purpose

PR 0028b bootstraps a **minimal isolated FastAPI web host module** (`app/web_host.py`) that
creates a FastAPI `app` with the existing read-only `GET /control/state` endpoint from
`app.control.web_ui_read_endpoint`. The host uses a placeholder snapshot provider that
returns `None`, so the endpoint currently returns `UNAVAILABLE`.

The host is NOT wired into `run.py`. It does NOT start a server. It does NOT modify Docker
or deployment files. A real runtime snapshot provider is deferred to PR 0029.

This unblocks the wiring chain that was blocked by PR 0028 (no existing FastAPI host).

## 3. Product Context

1. PR 0027 created `create_control_state_read_router()` in `app/control/web_ui_read_endpoint.py`
   with lazy FastAPI import.
2. PR 0028 was blocked because no existing FastAPI host existed.
3. PR 0028b creates that host — a minimal `app/web_host.py` module.
4. PR 0029 will add a real runtime snapshot provider.
5. A later PR may wire the host into `run.py`.

## 4. Required Module Design

### Module Location

**Create:** `app/web_host.py`

### Public API

```python
WEB_HOST_READ_ONLY_MODE: bool = True

def create_app() -> "FastAPI":
    ...

def create_placeholder_control_state_snapshot_provider() -> Callable[[], None]:
    ...
```

### Behavior

1. `create_app()` creates a `FastAPI` instance with title like `"Smart Pond Read API"`.
2. `create_app()` calls `create_placeholder_control_state_snapshot_provider()` for the provider.
3. `create_app()` imports `create_control_state_read_router` from `app.control.web_ui_read_endpoint`.
4. `create_app()` includes the router via `app.include_router(router)`.
5. `create_placeholder_control_state_snapshot_provider()` returns a lambda returning `None`.
6. `GET /control/state` therefore returns `UNAVAILABLE` for now.
7. No write routes (no POST/PUT/PATCH/DELETE).
8. No `shared_state` reads.
9. No direct device reads.
10. No Tuya/hardware calls.
11. No command execution.
12. No runtime wiring — does not edit `run.py` or `api.py`.
13. No `uvicorn.run()` or any server start code.
14. No Docker/deployment changes.
15. No real runtime provider — real provider deferred to PR 0029.

### Dependencies

- `FastAPI` — imported at module level or inside `create_app()`.
- `app.control.web_ui_read_endpoint` — for `create_control_state_read_router`, `ControlStateSnapshot`.
- `typing` — `Callable`.

Must NOT import: `app.service`, `app.devices`, `app.tuya`, `app.monitoring`, `app.ml`,
`app.weather`, `shared_state`, `smart_home_controller`, `relay_tuya_controller`.

Must NOT call: `build_runtime_control_snapshot`, `build_control_state_snapshot`.

## 5. Required Architecture Document

### File: `.project-memory/WEB_HOST_BOOTSTRAP.md`

Must state the 15 architecture principles:
1. Minimal isolated FastAPI web host module.
2. Not wired into `run.py`.
3. Not started by runtime.
4. No Docker/deployment changes.
5. Includes read-only `GET /control/state`.
6. Current provider returns `None` → `UNAVAILABLE`.
7. Real provider deferred to PR 0029.
8. No write API.
9. No command execution.
10. No `shared_state` reads.
11. No direct device reads.
12. No Tuya/hardware calls.
13. Operator writes through control-layer.
14. Hardware execution behind later safety-reviewed PR.
15. ML control deferred per ADR-0003.

## 6. Allowed Implementation Files

| File | Action |
|---|---|
| `app/web_host.py` | **Create** — minimal FastAPI web host |
| `.project-memory/WEB_HOST_BOOTSTRAP.md` | **Create** — architecture document |
| `scripts/check-web-host-bootstrap.sh` | **Create** — static validation script |
| `.github/workflows/validate.yml` | **Edit** — add one validation step |
| `.project-memory/CURRENT_STATE.md` | **Edit** — add PR 0028b section |
| `.project-memory/ROADMAP.md` | **Edit** — mark PR 0028b in roadmap |
| `.project-memory/pr/0028b-bootstrap-read-only-web-host/CODER_REPORT.txt` | **Create** |

## 7. Forbidden Implementation Files

Standard frozen list (PR 0014–0028 modules, runtime code, Docker, deployment, configs).
Also: `run.py` and `api.py` must NOT be modified.

## 8. Static Validation Script

`scripts/check-web-host-bootstrap.sh` must implement the 20 checks from the specification.

Add this step to `.github/workflows/validate.yml`:

```yaml
      - name: 🔍 Web host bootstrap check
        run: bash scripts/check-web-host-bootstrap.sh
```

## 9. CURRENT_STATE.md Update

```
## PR 0028b — Minimal Read-Only FastAPI Web Host Bootstrap
PR 0028b bootstraps a minimal isolated FastAPI web host in `app/web_host.py`
with `create_app()`, `create_placeholder_control_state_snapshot_provider()`,
and `WEB_HOST_READ_ONLY_MODE`. The host includes the existing GET /control/state
endpoint with a placeholder provider returning None (UNAVAILABLE response).
No runtime wiring. No server start. No write API. No execution.
```

## 10. ROADMAP.md Update

```
- [x] PR 0028b — Minimal read-only FastAPI web host bootstrap
```

## 11. Validation

| Check | Result |
|---|---|
| `python3 -m compileall -q .` | exit 0 |
| `scripts/check-repo-safety.sh` | exit 0 |
| `scripts/check-project-memory.sh` | exit 0 |
| `scripts/validate-yaml.py` | exit 0 |
| `scripts/check-web-ui-read-only-endpoint.sh` | exit 0 (30/30) |

## 12. Validation Checklist

| Check | Result |
|---|---|
| `test -f PLAN.md` | PASS |
| `grep -q "app/web_host.py" PLAN.md` | PASS |
| `grep -q "create_app" PLAN.md` | PASS |
| `grep -q "create_placeholder_control_state_snapshot_provider" PLAN.md` | PASS |
| `grep -q "WEB_HOST_READ_ONLY_MODE" PLAN.md` | PASS |
| `grep -q "placeholder-provider" PLAN.md` | PASS |
| `grep -q "returns-unavailable" PLAN.md` | PASS |
| `grep -q "real-provider-deferred" PLAN.md` | PASS |
| `grep -q "no-runtime-wiring" PLAN.md` | PASS |
| `grep -q "WEB_HOST_BOOTSTRAP.md" PLAN.md` | PASS |
| `grep -q "ML control" PLAN.md` | PASS |

## 13. Boundary Confirmations

- **Minimal FastAPI host only**: Creates app with single read-only route
- **Placeholder provider**: Returns `None`; endpoint returns `UNAVAILABLE`
- **No runtime wiring**: Does not edit `run.py` or `api.py`
- **No server start**: No `uvicorn.run()` or similar
- **No write API**: No POST/PUT/PATCH/DELETE routes
- **No executor**: `CommandExecutor` must NOT exist
- **No hardware execution**: Does not call any switch/device/Tuya method
- **No shared_state reads**: Does not import or read shared state
- **No ML control**: ML control deferred per ADR-0003
- **All existing modules unchanged**: PR 0014–0028 frozen
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval
