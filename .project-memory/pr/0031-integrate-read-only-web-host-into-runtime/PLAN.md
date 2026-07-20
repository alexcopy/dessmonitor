# PR 0031 — Integrate Read-Only Web Host Into Runtime

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `52320f9fdbf09b91a37fe64578645d2f7df8f89e` |
| Branch | `git branch --show-current` | `0031-integrate-read-only-web-host-into-runtime` |
| Working tree | `git status --short` | clean (no local changes) |
| `test -f app/web_host.py` | Prerequisite | PASS |
| `test -f app/web_host_startup.py` | Prerequisite | PASS |
| `test -f app/web_control_state_provider.py` | Prerequisite | PASS |
| `test -f app/control/web_ui_read_endpoint.py` | Prerequisite | PASS |
| `test -f run.py` | Prerequisite | PASS |
| `test -f requirements.txt` | Prerequisite | PASS |
| `test -f Dockerfile` | Prerequisite | PASS |

The precondition passes. Branch is `0031-integrate-read-only-web-host-into-runtime`, working tree is clean, and all required prerequisite files exist.

**Dependency discovery:**

| Dependency | In requirements.txt | Locally installed |
|---|---|---|
| `fastapi` | **ABSENT** | **NOT_INSTALLED** |
| `uvicorn` | **ABSENT** | **NOT_INSTALLED** |

Neither `fastapi` nor `uvicorn` is present in `requirements.txt` or installed locally.
The coder must determine appropriate version pins. Since neither is installed locally,
the plan suggests using a recent compatible combination — see Section 4 for resolution.

## 2. Purpose

PR 0031 integrates the existing read-only FastAPI web host into `run.py` as an optional
embedded asyncio task. When enabled via the `WEB_HOST_ENABLED` environment variable,
a Uvicorn server runs inside the same process, exposing the existing `GET /control/state`
endpoint with real read-only load state from the device manager.

When disabled (default), Uvicorn is never imported, no HTTP socket is opened, and existing
runtime behavior remains completely unchanged.

There is no second image, no second container, no Dockerfile CMD change.

## 3. Product Context

1. PR 0028b created `app/web_host.py` (FastAPI app factory).
2. PR 0029 created `app/web_control_state_provider.py` (injectable snapshot provider).
3. PR 0030 created `app/web_host_startup.py` (standalone startup for manual use).
4. PR 0031 integrates the web host into `run.py` as an optional embedded task.
5. The existing container startup (`CMD ["python", "run.py"]`) remains unchanged.

## 4. Dependency Resolution

Both `fastapi` and `uvicorn` are absent from `requirements.txt` and not installed
locally. The coder must:

1. Determine compatible versions for Python 3.12 (CI) / 3.14 (dev). Recommended approach:
   - Pin `fastapi>=0.115.0` (or a recent stable version)
   - Pin `uvicorn[standard]>=0.34.0` (or a recent stable version)
2. Add both to `requirements.txt` following the repository's exact-pin style.
3. If the Docker image building in CI fails due to missing pins, the coder should
   test with a local pip install of fastapi+uvicorn and record the resolved versions
   in the CODER_REPORT.txt.
4. Do NOT edit Dockerfile — it already runs `pip install -r requirements.txt`.

## 5. Required Implementation Files

| File | Action |
|---|---|
| `app/web_runtime_integration.py` | **Create** — runtime integration module |
| `run.py` | **Edit** — add optional web host startup/shutdown |
| `requirements.txt` | **Edit** — add `fastapi` and `uvicorn` pins |
| `scripts/check-runtime-read-only-web-host.sh` | **Create** — comprehensive validation script |
| `.github/workflows/validate.yml` | **Edit** — add one validation step |
| `.project-memory/pr/0031-integrate-read-only-web-host-into-runtime/CODER_REPORT.txt` | **Create** |

## 6. Required Module: `app/web_runtime_integration.py`

### Constants

```python
WEB_HOST_ENABLED_ENV = "WEB_HOST_ENABLED"
WEB_HOST_BIND_ENV = "WEB_HOST_BIND"
WEB_HOST_PORT_ENV = "WEB_HOST_PORT"

DEFAULT_WEB_HOST_BIND = "0.0.0.0"
DEFAULT_WEB_HOST_PORT = 8000
```

### Type: `RuntimeWebHostHandle`

A frozen/passive dataclass or simple object holding:
- `server_task: asyncio.Task`
- `server: uvicorn.Server`

### Function: `is_runtime_web_host_enabled(environ=None)`

1. Read `WEB_HOST_ENABLED` from `environ` (defaults to `os.environ`).
2. Missing or absent → `False`.
3. Case-insensitive comparison against `{"1", "true", "yes", "on"}` → `True`.
4. Every other value → `False`.

### Function: `build_runtime_read_model(devices, created_at=None)`

Build a `dict` suitable for `create_runtime_control_state_snapshot_provider`.

Mapping from `RelayChannelDevice` attributes:

| RuntimeLoadState field | Source | Notes |
|---|---|---|
| `load_id` | `device.id` | Local device id only |
| `display_name` | `device.name` | Device name |
| `configured_load_watts` | `device.load_in_wt` | Numeric, zero when invalid |
| `currently_on` | `device.status.get(device.state_key)` | Normalize bool/0/1/true strings |
| `controllable` | `False` when unavailable, `state_key` missing, or sensor-only type; else `True` | |
| `is_life_support` | Only from explicit `extra` flag or roles | Do NOT infer from name text |
| `roles` | `(normalized device_type,)` + any explicit safe roles | Do NOT expose full `extra` dict |
| `status` | `"healthy"` if `device.is_healthy`, else `"unhealthy"`, or `"unavailable"` | |
| `notes` | Empty string | |

**Never expose:**
- `tuya_device_id`, `control_key`, `api_key`, `email`, `password`, `token`, `secret`
- Raw `status` dict, raw `extra` dict, or device configuration dictionaries

**Never call:**
- `switch_on_device`, `switch_off_device`, `set_on`, `set_off`, `toggle_device`, `can_switch`
- Any hardware or Tuya method

### Function: `create_runtime_state_provider(devices_provider)`

1. Returns a callable `() -> dict | None`.
2. On call, obtains devices from `devices_provider()`.
3. If `devices_provider` raises → returns `None`.
4. Builds runtime state dict via `build_runtime_read_model()`.
5. Returns the dict.
6. Individual malformed devices are silently skipped.

### Function: `start_runtime_read_only_web_host(devices_provider, environ=None, logger=None)`

1. If `is_runtime_web_host_enabled(environ)` is `False` → return `None`.
2. Import `uvicorn` inside the function. Missing → `RuntimeError("uvicorn-unavailable")`.
3. Parse `WEB_HOST_BIND` (default `0.0.0.0`).
4. Parse `WEB_HOST_PORT` as `int` (1-65535). Invalid → `RuntimeError("invalid-web-host-port")`.
5. Create `provider = create_runtime_state_provider(devices_provider)`.
6. Create app via `app.web_host.create_app(runtime_state_provider=provider)`.
7. Build `uvicorn.Config(app, host=host, port=port, ...)` with:
   - `log_level="info"` or as appropriate
   - No reload, no workers
   - **Signal ownership disabled**: `install_signal_handlers=False` (or `capture_signals=False` depending on Uvicorn version)
8. Create `uvicorn.Server(config)`.
9. Run `await server.serve()` as an asyncio task.
10. Wait for startup with a bounded timeout.
11. Detect if task exits before startup — report failure without starting commands.
12. Return `RuntimeWebHostHandle` after success.

### Function: `stop_runtime_read_only_web_host(handle, logger=None)`

1. If `handle` is `None`, return immediately.
2. Set `handle.server.should_exit = True`.
3. Await `handle.server_task` with bounded timeout.
4. Cancel task only after timeout.
5. `gather` with `return_exceptions=True` — never suppress cancellation of other tasks.
6. Never stop or mutate devices.

## 7. Required run.py Integration

1. Add import:
   ```python
   from app.web_runtime_integration import (
       RuntimeWebHostHandle,
       start_runtime_read_only_web_host,
       stop_runtime_read_only_web_host,
   )
   ```
2. Before the main try/except, add:
   ```python
   web_host_handle: RuntimeWebHostHandle | None = None
   ```
3. After signal handlers are set up and `dev_mgr` exists, and after all other initialization,
   add:
   ```python
   try:
       web_host_handle = await start_runtime_read_only_web_host(
           devices_provider=dev_mgr.get_devices,
           logger=important_log,
       )
   except Exception as exc:
       important_log.warning(f"[WEB] Read-only host not started: {exc}")
   ```
4. In the `finally` block, add before the device-related shutdown:
   ```python
   if web_host_handle is not None:
       await stop_runtime_read_only_web_host(web_host_handle, logger=important_log)
   ```
5. Preserve all existing initialization, automation paths, and signal handling.
6. Do NOT alter switch, pump, inverter, updater, ML, or logging intervals.
7. SIGINT/SIGTERM ownership stays with `run.py` (loop.add_signal_handler).
8. The `if __name__ == "__main__":` block remains unchanged.

## 8. No New Architecture Document

Per spec: "No additional project-memory documentation file is required."

## 9. Static Validation Script

`scripts/check-runtime-read-only-web-host.sh` must implement 29 checks covering:
1. Shell/file existence checks.
2. Python compilation.
3. AST checks for uvicorn module-level import (forbidden), uvicorn.run usage (forbidden),
   uvicorn.Config/Server usage (required), signal-handler disablement.
4. Disabled-env behavior returns None without importing uvicorn.
5. Env true/false parsing across all accepted values.
6. Invalid port raises RuntimeError.
7. Build a fake `RelayChannelDevice`-like test object.
8. Build runtime mapping from the fake device.
9. Verify safe fields present, forbidden fields absent.
10. Verify failing `devices_provider` returns None.
11. Start real embedded Uvicorn on 127.0.0.1:free_port using fake devices.
12. HTTP GET /control/state → 200, fake load in JSON, no cloud id/secret.
13. Stop server, confirm task finishes.
14. Check `run.py` calls start/stop exactly once, passes `dev_mgr.get_devices`.
15. Check `run.py` catches startup failure and continues.
16. Check `run.py` owns SIGINT/SIGTERM.
17. Check Dockerfile and compose unchanged.
18. Clear PASS/FAIL per-check output.

## 10. GitHub Actions Integration

Add one step after the existing `check-web-host-startup.sh`:

```yaml
      - name: 🔍 Runtime read-only web host check
        run: bash scripts/check-runtime-read-only-web-host.sh
```

## 11. CURRENT_STATE.md Update

```
## PR 0031 — Integrate Read-Only Web Host Into Runtime
PR 0031 integrates the existing read-only FastAPI web host into `run.py`
as an optional embedded asyncio task via `app/web_runtime_integration.py`.
Disabled by default (WEB_HOST_ENABLED=false). Uses uvicorn.Config/Server
with signal ownership disabled. Exposes GET /control/state with real
read-only load state from the device manager. No write API. No hardware
execution. No Dockerfile change. No container CMD change.
```

## 12. ROADMAP.md Update

```
- [ ] PR 0031 — Integrate read-only web host into runtime
```

## 13. Boundary Confirmations

- **No second image or container**: Same process, same CMD
- **Disabled by default**: `WEB_HOST_ENABLED=false`, no Uvicorn import when disabled
- **No runtime wiring when disabled**: No socket, no FastAPI import
- **No Dockerfile change**: Container CMD stays `["python", "run.py"]`
- **No write API**: `GET /control/state` remains read-only
- **No hardware execution**: HTTP code does not call Tuya or switching methods
- **No signal-handler ownership**: Uvicorn signal handlers disabled; `run.py` owns SIGINT/SIGTERM
- **No device mutation**: Read-model is read-only snapshot
- **No secrets exposed**: `tuya_device_id`, `control_key`, `api_key`, raw status, raw extra never serialized
- **All existing automation unchanged**: Switch, pump, inverter, updater, ML, and logging intervals preserved
- **No ML control**: ML control deferred per ADR-0003
- **No new architecture document**: Spec explicitly forbids one
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval

## 14. Blocker

**Dependency version pins.** Neither `fastapi` nor `uvicorn` is present in `requirements.txt`
or installed in the local environment. The coder must determine appropriate version pins.
Recommended approach: install the latest stable versions compatible with Python 3.12,
test, and record the resolved versions in the CODER_REPORT. The plan suggests
`fastapi>=0.115.0` and `uvicorn[standard]>=0.34.0` as starting points, but actual pins
must be determined during implementation. If the coder cannot determine versions,
this PR is blocked.
