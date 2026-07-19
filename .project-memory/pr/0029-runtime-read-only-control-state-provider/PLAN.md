# PR 0029 — Runtime Read-Only Control State Provider

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `bca6ed8062ef6e4d1e0b9cc5493b69eb5a115301` |
| Branch | `git branch --show-current` | `0029-runtime-read-only-control-state-provider` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0029-runtime-read-only-control-state-provider` and working tree is clean.

## 2. Purpose

PR 0029 replaces the **placeholder-only path** in `app/web_host.py` with an injectable
read-only runtime state provider. The provider adapts caller-provided runtime state
mappings into `ControlStateSnapshot` via the existing runtime snapshot adapter
(PR 0024), without reading `shared_state` globals, devices, Tuya, or hardware.

When no runtime state provider is injected, the endpoint continues to return
`UNAVAILABLE` (backward-compatible default). When a real runtime state provider
is wired by a future PR, the endpoint will return a meaningful snapshot.

## 3. Product Context

1. PR 0028b created `app/web_host.py` with a placeholder provider returning `None`.
2. PR 0024 created `build_runtime_control_snapshot()` which transforms caller-provided
   runtime data into `ControlStateSnapshot`.
3. PR 0029 creates a new provider module (`app/web_control_state_provider.py`) that
   wraps the adapter, and updates `app/web_host.py` to accept an optional
   `runtime_state_provider` parameter.
4. A future PR may wire a real runtime state callback.

## 4. Required Module Design

### Module Location

**Create:** `app/web_control_state_provider.py`

### Provider API

```python
def create_runtime_control_state_snapshot_provider(
    runtime_state_provider: Callable[[], dict[str, object] | None] | None = None,
) -> Callable[[], ControlStateSnapshot | None]:
    ...

def build_control_state_snapshot_from_runtime_state(
    runtime_state: dict[str, object] | None,
) -> ControlStateSnapshot | None:
    ...
```

### Update: `app/web_host.py`

```python
def create_app(
    runtime_state_provider: Callable[[], dict[str, object] | None] | None = None,
) -> "FastAPI":
    ...
```

### Behavior

1. `create_runtime_control_state_snapshot_provider(runtime_state_provider)`:
   - If `runtime_state_provider` is None, returns a provider that always returns None
     (backward-compatible with PR 0028b).
   - If `runtime_state_provider` returns None or an empty mapping, the returned provider
     returns None.
   - If `runtime_state_provider` raises an exception, the returned provider returns None
     and does not leak exception text.
   - If `runtime_state_provider` returns a mapping, calls
     `build_control_state_snapshot_from_runtime_state()` to produce the snapshot.

2. `build_control_state_snapshot_from_runtime_state(runtime_state)`:
   - If `runtime_state` is None or empty, returns None.
   - Best-effort parse `runtime_state["loads"]` or `runtime_state["load_states"]` if present.
   - Calls `build_runtime_control_snapshot()` with the parsed adapter input.
   - Returns `result.snapshot`.
   - Tolerates missing/partial mapping without crashing.

3. `create_app(runtime_state_provider=None)`:
   - Accepts optional `runtime_state_provider`.
   - Calls `create_runtime_control_state_snapshot_provider(runtime_state_provider)`.
   - Passes the result to `create_control_state_read_router()`.
   - Default `create_app()` still works and returns `UNAVAILABLE` through None provider.

### Dependencies

- `app.control.runtime_snapshot_adapter` — `build_runtime_control_snapshot`,
  `RuntimeControlSnapshotAdapterInput`, `RuntimeLoadState`, `RuntimeControlModeState`.
- `app.control.control_state_snapshot` — `ControlStateSnapshot`.
- Python standard library (`typing`).

Must NOT import: `app.service`, `app.devices`, `app.tuya`, `app.monitoring`,
`app.ml`, `app.weather`, `shared_state`, `smart_home_controller`.

Must NOT call: `build_control_state_snapshot` directly (done via adapter).

## 5. Required Markers

- `"runtime-read-only-provider"`
- `"caller-provided-runtime-state"`
- `"no-shared-state-read"`
- `"no-device-read"`
- `"no-tuya-hardware"`
- `"no-execution"`
- `"no-write-api"`
- `"real-provider-injected"`
- `"provider-errors-hidden"`
- `"operator-writes-through-control-layer"`

## 6. Allowed Implementation Files

| File | Action |
|---|---|
| `app/web_control_state_provider.py` | **Create** |
| `app/web_host.py` | **Edit** — add `runtime_state_provider` to `create_app()` |
| `.project-memory/WEB_CONTROL_STATE_PROVIDER.md` | **Create** |
| `scripts/check-web-control-state-provider.sh` | **Create** |
| `.github/workflows/validate.yml` | **Edit** |
| `.project-memory/CURRENT_STATE.md` | **Edit** |
| `.project-memory/ROADMAP.md` | **Edit** |
| `.project-memory/pr/0029-runtime-read-only-control-state-provider/CODER_REPORT.txt` | **Create** |

## 7. Forbidden Implementation Files

Standard frozen list. Plus: `run.py`, `api.py` must NOT be modified.

## 8. Static Validation Script

`scripts/check-web-control-state-provider.sh` must:
1. Check `app/web_control_state_provider.py` exists.
2. Check `create_runtime_control_state_snapshot_provider` exists.
3. Check `build_control_state_snapshot_from_runtime_state` exists.
4. Check `app/web_host.py` `create_app()` accepts optional `runtime_state_provider`.
5. Check provider returns None when `runtime_state_provider` is None.
6. Check provider returns None when `runtime_state_provider` returns None or empty dict.
7. Check provider safely handles `runtime_state_provider` raising.
8. Check `build_control_state_snapshot_from_runtime_state` returns None for None/empty input.
9. Check no `shared_state` string in `app/web_control_state_provider.py`.
10. Check no `app.service`, `app.devices`, `app.tuya` imports.
11. Check no hardware calls.
12. Check `run.py` and `api.py` not modified.
13. Check `.project-memory/WEB_CONTROL_STATE_PROVIDER.md` exists.
14. Print clear output.
15. Exit 0 only on pass.

## 9. CURRENT_STATE.md Update

```
## PR 0029 — Runtime Read-Only Control State Provider
PR 0029 replaces the placeholder-only path in `app/web_host.py` with an
injectable runtime state provider in `app/web_control_state_provider.py`.
The provider adapts caller-provided runtime state mappings into
ControlStateSnapshot via build_runtime_control_snapshot(). Default behavior
returns UNAVAILABLE when no provider is injected. No shared_state reads.
No device reads. No Tuya/hardware calls.
```

## 10. ROADMAP.md Update

```
- [x] PR 0029 — Runtime read-only control state provider
```

## 11. Validation

| Check | Result |
|---|---|
| `python3 -m compileall -q .` | exit 0 |
| `scripts/check-repo-safety.sh` | exit 0 |
| `scripts/check-project-memory.sh` | exit 0 |
| `scripts/validate-yaml.py` | exit 0 |
| `scripts/check-web-ui-read-only-endpoint.sh` | exit 0 |
| `scripts/check-web-host-bootstrap.sh` | exit 0 (21/21) |

## 12. Validation Checklist

| Check | Result |
|---|---|
| `test -f PLAN.md` | PASS |
| `grep -q "app/web_control_state_provider.py" PLAN.md` | PASS |
| `grep -q "create_runtime_control_state_snapshot_provider" PLAN.md` | PASS |
| `grep -q "build_control_state_snapshot_from_runtime_state" PLAN.md` | PASS |
| `grep -q "caller-provided-runtime-state" PLAN.md` | PASS |
| `grep -q "no-shared-state-read" PLAN.md` | PASS |
| `grep -q "no-execution" PLAN.md` | PASS |

## 13. Boundary Confirmations

- **Provider only**: Wraps existing adapter, does not add new evaluation logic
- **No shared_state reads**: All state is caller-provided
- **No device reads**: Does not query hardware
- **No Tuya/hardware calls**: Pure data transformation
- **No runtime wiring**: Does not edit `run.py` or `api.py`
- **No server start**: No `uvicorn.run()`
- **No write API**: No POST/PUT/PATCH/DELETE
- **No ML control**: ML control deferred per ADR-0003
- **All existing modules unchanged**: PR 0014–0028b frozen
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval
