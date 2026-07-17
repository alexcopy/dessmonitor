# PR 0026 — Web UI Read-Only Control State Endpoint Implementation Plan

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `9349db0fccedd60bf528ba88f020fa318eee9dc0` |
| Branch | `git branch --show-current` | `0026-web-ui-read-only-control-state-endpoint-plan` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0026-web-ui-read-only-control-state-endpoint-plan` and working tree is clean.

## 2. Purpose

PR 0026 defines a **passive future endpoint implementation plan** for the real `GET /control/state` read-only endpoint. It provides types and a pure function that document what the future endpoint will look like, its data sources, its boundaries, and the implementation steps required — without adding a real API endpoint now.

This PR is **contract/model/docs/validation only**. It does NOT add a real API endpoint, edit FastAPI route files, write to hardware, or wire runtime.

## 3. Product Context

1. PRs 0014–0023 built the full read-only control state snapshot pipeline.
2. PR 0024 added the runtime read-only control snapshot adapter.
3. PR 0025 defined the web UI read-only API contract (`WebUiControlStateResponse`).
4. PR 0026 defines the **implementation plan** for the future GET /control/state endpoint.
5. A separate future PR will implement the actual FastAPI/Flask endpoint.
6. This PR documents boundaries, data sources, and implementation steps.

## 4. Current Repository State

**No GET /control/state endpoint exists.** The `api.py` file handles Dess/inverter data endpoints but has no control state endpoint. The run.py imports are all runtime services (Tuya, ML, weather, inverter). No FastAPI route for control state exists.

## 5. Required Architecture Document

### File: `.project-memory/WEB_UI_READ_ONLY_ENDPOINT_PLAN.md`

Must state:
1. This PR defines a future endpoint implementation plan only.
2. No FastAPI route is added.
3. No real API endpoint is added.
4. Future endpoint should be `GET /control/state`.
5. Future endpoint must be read-only.
6. Future endpoint must use control-layer read model (snapshot/adapter).
7. Future endpoint must not call Tuya/hardware.
8. Future endpoint must not bypass safety gates.
9. Future endpoint must not mutate shared_state.
10. Future write actions must go through control-layer intent/queue.
11. Adding the actual endpoint requires a separate PR.
12. `execution_allowed_now` remains false until separate safety-reviewed execution PR.
13. ML control remains deferred per ADR-0003.

## 6. Required Module Design

### Module Location

**Create:** `app/control/web_ui_read_endpoint_plan.py`

**May update:** `app/control/__init__.py`

### Dependencies

Use only stdlib + `app.control.web_ui_read_contract` types (`WebUiReadContract`, `WebUiReadContractStatus`). Must NOT import FastAPI, Flask, runtime/service/Tuya/device modules, snapshot builders, or adapter functions.

## 7. Required Types (5 Types)

### Type 1: `WebUiReadEndpointPlanStatus`

```python
class WebUiReadEndpointPlanStatus(Enum):
    DRAFT = "draft"
    READY_FOR_FUTURE_IMPLEMENTATION = "ready_for_future_implementation"
    BLOCKED = "blocked"
```

### Type 2: `WebUiReadEndpointDataSource`

```python
@dataclass(frozen=True)
class WebUiReadEndpointDataSource:
    """Describes a data source for the future endpoint.

    Pure data — no runtime reads, no side effects.
    """
    name: str = ""
    description: str = ""
    read_only: bool = True
    caller_provided_state_only: bool = True
    live_shared_state_reads_allowed: bool = False
    direct_device_reads_allowed: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)
```

### Type 3: `WebUiReadEndpointBoundary`

```python
@dataclass(frozen=True)
class WebUiReadEndpointBoundary:
    """Boundaries and contract for the future endpoint.

    Pure data — no runtime reads, no side effects.
    """
    path: str = "/control/state"
    method: str = "GET"
    read_only: bool = True
    writes_allowed: bool = False
    execution_allowed: bool = False
    route_added_now: bool = False
    allowed_actions: tuple[str, ...] = field(default_factory=tuple)
    forbidden_actions: tuple[str, ...] = field(default_factory=tuple)
    response_model: str = "WebUiControlStateResponse"
    notes: tuple[str, ...] = field(default_factory=tuple)
```

### Type 4: `WebUiReadEndpointImplementationStep`

```python
@dataclass(frozen=True)
class WebUiReadEndpointImplementationStep:
    """A step required to implement the future endpoint.

    Pure data — no side effects.
    """
    step_id: str = ""
    description: str = ""
    requires_separate_pr: bool = True
    safety_review_required: bool = True
    allowed_in_this_pr: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)
```

### Type 5: `WebUiReadEndpointPlan`

```python
@dataclass(frozen=True)
class WebUiReadEndpointPlan:
    """Complete endpoint implementation plan.

    Pure data — no FastAPI imports, no routes, no runtime wiring.
    """
    status: WebUiReadEndpointPlanStatus = WebUiReadEndpointPlanStatus.DRAFT
    contract: WebUiReadContract | None = None
    endpoint: WebUiReadEndpointBoundary = field(default_factory=WebUiReadEndpointBoundary)
    data_sources: tuple[WebUiReadEndpointDataSource, ...] = field(default_factory=tuple)
    implementation_steps: tuple[WebUiReadEndpointImplementationStep, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)
```

## 8. Required Pure Function

### `build_web_ui_read_endpoint_plan`

```python
def build_web_ui_read_endpoint_plan(
    contract: WebUiReadContract | None = None,
) -> WebUiReadEndpointPlan
```

Behavior:
1. If `contract` is None → use default `WebUiReadContract()`, add warning `"default-contract-used"`.
2. If `contract.read_only` is `false` or `contract.write_actions_allowed` is `true` → return `BLOCKED` with warning `"contract-not-read-only"`.
3. Otherwise → return `READY_FOR_FUTURE_IMPLEMENTATION`.
4. Endpoint path must be `/control/state`.
5. Endpoint method must be `GET`.
6. `route_added_now` must be `false`.
7. `writes_allowed` must be `false`.
8. `execution_allowed` must be `false`.
9. `allowed_actions` must include `"read-control-state"`.
10. `forbidden_actions` must include:
    `"direct-hardware-write"`, `"direct-tuya-command"`, `"execute-command"`,
    `"mutate-shared-state"`, `"bypass-control-layer"`, `"bypass-safety-gates"`,
    `"write-api"`, `"route-write-methods"`.
11. Data sources must describe future use of the read-only adapter/snapshot path.
12. Data sources must have `live_shared_state_reads_allowed=False` and `direct_device_reads_allowed=False`.
13. Implementation steps must require separate PR.
14. Implementation steps must not be allowed in this PR.
15. Notes must include:
    `"endpoint-plan-only"`, `"no-real-api-endpoint"`, `"read-only-endpoint-future"`,
    `"future-separate-pr-required"`, `"no-execution"`, `"no-runtime-wiring"`,
    `"no-write-api"`, `"control-layer-only"`, `"safety-gates-required"`,
    `"operator-writes-through-control-layer"`.
16. Must not import FastAPI/Flask/routing.
17. Must not call adapter/snapshot builders.
18. Must not read runtime state.
19. Must not execute, queue, persist, fetch, log, or call runtime.

## 9. Allowed Implementation Files

Standard list: `app/control/web_ui_read_endpoint_plan.py`, `app/control/__init__.py`, `.project-memory/WEB_UI_READ_ONLY_ENDPOINT_PLAN.md`, `scripts/check-web-ui-read-only-endpoint-plan.sh`, `.github/workflows/validate.yml`, `.project-memory/CURRENT_STATE.md`, `.project-memory/ROADMAP.md`, CODER_REPORT.txt.

## 10. CURRENT_STATE.md Update

```
## PR 0026 — Web UI Read-Only Control State Endpoint Implementation Plan
PR 0026 defines the future endpoint implementation plan in
`app/control/web_ui_read_endpoint_plan.py` with 5 types
(WebUiReadEndpointPlanStatus, WebUiReadEndpointDataSource,
WebUiReadEndpointBoundary, WebUiReadEndpointImplementationStep,
WebUiReadEndpointPlan) and the pure function
`build_web_ui_read_endpoint_plan()`. Documents future GET /control/state
endpoint boundaries and implementation steps. No real API endpoint added.
No FastAPI routes. No runtime wiring. No execution.
```

## 11. ROADMAP.md Update

```
- [x] PR 0026 — Web UI read-only control state endpoint implementation plan
```

## 12. Static Validation Script

24 checks as specified: module exists, 5 types, function exists, 3 status values, `route_added_now`, `writes_allowed`, `execution_allowed`, `live_shared_state_reads_allowed`, `direct_device_reads_allowed`, 8 forbidden action strings, 10 note strings, `__init__.py` exports, plan doc exists, plan-only language, no FastAPI/APIRouter/route, no POST/PUT/PATCH/DELETE, no runtime imports, no snapshot/adapter builder calls, no executor, no hardware calls, no impurities, runtime files unchanged.

## 13. Boundary Confirmations

- **Plan/model only**: No real API endpoint, no FastAPI route
- **No write API**: `writes_allowed` is `false`
- **No runtime wiring**: Not connected to any runtime component
- **No snapshot/adapter calls**: Does not build or read snapshots
- **No executor**: `CommandExecutor` must NOT exist
- **No hardware execution**: Does not call any switch/device/Tuya method
- **No ML control**: ML control deferred per ADR-0003
- **All existing modules unchanged**: PR 0014–0025 frozen
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval
