# PR 0027 — Web UI Read-Only Control State Endpoint

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `465faeb4573d682c35aeb282666debb53398cf49` |
| Branch | `git branch --show-current` | `0027-web-ui-read-only-control-state-endpoint` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0027-web-ui-read-only-control-state-endpoint` and working tree is clean.

## 2. Purpose

PR 0027 adds an **isolated read-only endpoint module** for a future `GET /control/state` endpoint. It provides a FastAPI router factory with lazy imports, a response builder function that does not require FastAPI, and supporting types/constants.

This PR does **not** wire the router into any runtime application. It does **not** edit `api.py` or `run.py`. It does **not** add write API, execute commands, read live `shared_state` directly, or call Tuya/hardware.

FastAPI is imported lazily inside the router factory only — the `app.control` package remains import-safe even if FastAPI is unavailable.

## 3. Product Context

1. PRs 0023–0025 built the read-only snapshot pipeline, adapter, and API contract.
2. PR 0026 defined the endpoint implementation plan.
3. PR 0027 adds the **actual endpoint module** that can be wired into a future runtime PR.
4. A separate future PR will wire this router into the application.

## 4. Required Module Design

### Module Location

**Create:** `app/control/web_ui_read_endpoint.py`

**May update:** `app/control/__init__.py` (export all public types, functions, constants)

### Dependencies

Top-level imports must use only:
- Python standard library (`dataclasses`, `typing`, `enum`, `functools`)
- `app.control.control_state_snapshot` types: `ControlStateSnapshot`
- `app.control.web_ui_read_contract` types and function: `WebUiControlStateResponse`, `WebUiReadContractStatus`, `build_web_ui_control_state_response`

`FastAPI`/`APIRouter` must be imported only inside `create_control_state_read_router()` via lazy import. No top-level FastAPI import.

Must NOT import: `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`, `smart_home_controller`, `relay_tuya_controller`, any runtime adapter, or live shared_state modules.

## 5. Required Constants

```python
CONTROL_STATE_READ_PATH: str = "/control/state"
CONTROL_STATE_READ_METHOD: str = "GET"
```

## 6. Required Types (4 Types)

### Type 1: `WebUiReadEndpointStatus`

```python
class WebUiReadEndpointStatus(Enum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    FASTAPI_UNAVAILABLE = "fastapi_unavailable"
```

### Type 2: `WebUiReadEndpointConfig`

```python
@dataclass(frozen=True)
class WebUiReadEndpointConfig:
    path: str = "/control/state"
    method: str = "GET"
    read_only: bool = True
    route_wired_now: bool = False
    writes_allowed: bool = False
    execution_allowed: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)
```

### Type 3: `WebUiReadEndpointProviderResult`

```python
@dataclass(frozen=True)
class WebUiReadEndpointProviderResult:
    status: WebUiReadEndpointStatus = WebUiReadEndpointStatus.UNAVAILABLE
    response: WebUiControlStateResponse | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)
```

### Type 4: `WebUiReadEndpointRuntime`

```python
@dataclass(frozen=True)
class WebUiReadEndpointRuntime:
    config: WebUiReadEndpointConfig = field(default_factory=WebUiReadEndpointConfig)
    provider_available: bool = False
    fastapi_required_for_router: bool = True
    route_wired_now: bool = False
    read_only: bool = True
    writes_allowed: bool = False
    execution_allowed: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)
```

## 7. Required Functions (2 Functions)

### Function 1: `build_control_state_endpoint_response`

```python
def build_control_state_endpoint_response(
    snapshot_provider: Callable[[], ControlStateSnapshot | None] | None = None,
) -> WebUiReadEndpointProviderResult
```

Behavior:
1. **Import-safe** — does not require FastAPI.
2. If `snapshot_provider` is None → `UNAVAILABLE`, warning `"snapshot-provider-missing"`.
3. If `snapshot_provider` raises an exception → `UNAVAILABLE`, warning `"snapshot-provider-error"` (no secret leaking).
4. If `snapshot_provider` returns None → `UNAVAILABLE`, warning `"snapshot-unavailable"`.
5. If `snapshot_provider` returns `ControlStateSnapshot` → call `build_web_ui_control_state_response(snapshot)`, derive `status` from response.

### Function 2: `create_control_state_read_router`

```python
def create_control_state_read_router(
    snapshot_provider: Callable[[], ControlStateSnapshot | None],
) -> "APIRouter"
```

Behavior:
1. **Lazy import** `APIRouter` from `fastapi` inside the function.
2. If FastAPI is unavailable, raise `RuntimeError("fastapi-unavailable")`.
3. Create an `APIRouter` with no prefix, no tags.
4. Register a single `GET /control/state` route handler.
5. Route handler calls `build_control_state_endpoint_response(snapshot_provider)`.
6. Route handler returns the response (either as a `WebUiControlStateResponse` model or a plain dict derived from it).
7. This PR does NOT include the router in any app — `route_wired_now` remains `false`.

## 8. Required Note/Warning Strings

- `"read-only-endpoint"`, `"get-control-state"`, `"no-write-api"`, `"no-execution"`,
  `"no-runtime-wiring"`, `"route-not-wired"`, `"caller-provided-snapshot-provider"`,
  `"operator-writes-through-control-layer"`, `"safety-gates-required"`,
  `"fastapi-lazy-import"`, `"fastapi-unavailable"`, `"snapshot-provider-missing"`,
  `"snapshot-provider-error"`, `"snapshot-unavailable"`, `"direct-hardware-write"`,
  `"direct-tuya-command"`, `"execute-command"`, `"mutate-shared-state"`,
  `"bypass-control-layer"`, `"bypass-safety-gates"`, `"write-api"`, `"route-write-methods"`.

## 9. Allowed Implementation Files

Standard list: `app/control/web_ui_read_endpoint.py`, `app/control/__init__.py`, `.project-memory/WEB_UI_READ_ONLY_ENDPOINT.md`, `scripts/check-web-ui-read-only-endpoint.sh`, `.github/workflows/validate.yml`, `.project-memory/CURRENT_STATE.md`, `.project-memory/ROADMAP.md`, CODER_REPORT.txt.

## 10. Architecture Document

`.project-memory/WEB_UI_READ_ONLY_ENDPOINT.md` must document the same architectural principles as the specification section.

## 11. CURRENT_STATE.md Update

```
## PR 0027 — Web UI Read-Only Control State Endpoint
PR 0027 adds an isolated read-only endpoint module in
`app/control/web_ui_read_endpoint.py` with 4 types, 2 constants,
2 functions (build_control_state_endpoint_response without FastAPI,
create_control_state_read_router with lazy FastAPI import), and
architecture document. Endpoint is NOT wired into runtime. No api.py
or run.py changes. No write API. No execution.
```

## 12. ROADMAP.md Update

```
- [x] PR 0027 — Web UI read-only control state endpoint
```

## 13. Static Validation Script

25 checks as specified in the specification.

## 14. Boundary Confirmations

- **Isolated endpoint module only**: Not wired into runtime
- **Lazy FastAPI import**: No top-level FastAPI dependency
- **No api.py/run.py changes**: Runtime wiring deferred
- **No write API**: `writes_allowed` is `false`
- **No executor**: `CommandExecutor` must NOT exist
- **No hardware execution**: Does not call any switch/device/Tuya method
- **No ML control**: ML control deferred per ADR-0003
- **All existing modules unchanged**: PR 0014–0026 frozen
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval
