# PR 0025 — Web UI Read-Only Control State API Contract

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `daf958700c52aae9b786bfab13e36235b088e5b4` |
| Branch | `git branch --show-current` | `0025-web-ui-read-only-control-state-api-contract` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0025-web-ui-read-only-control-state-api-contract` and working tree is clean.

## 2. Purpose

PR 0025 defines the **future web UI read-only API contract**. It provides pure types and
a pure function `build_web_ui_control_state_response()` that packages a `ControlStateSnapshot`
into a `WebUiControlStateResponse` with read-only guarantees, allowed/forbidden action lists,
and architecture notes.

This PR is **contract/model/docs/validation only**. It does NOT add real API endpoints,
edit FastAPI route files, wire into runtime, read live shared_state, or execute commands.
A separate future PR will implement the actual read-only API endpoint.

## 3. Product Context

1. PRs 0014–0018C built the policy decision engine.
2. PR 0019 added the manual control queue boundary.
3. PR 0020 added command intent/proposal arbitration (CommandProposal).
4. PR 0021 added the command safety gate model.
5. PR 0022 added the controlled execution eligibility model.
6. PR 0023 added the read-only control state snapshot.
7. PR 0024 added the runtime read-only control snapshot adapter.
8. PR 0025 defines the **web UI read API contract** — what a future UI may read, what it
   must not write, and how it must send operator intents through the control layer.

## 4. Required Architecture Document

### File: `.project-memory/WEB_UI_READ_ONLY_API_CONTRACT.md`

Must state:
1. This PR defines contract only — no FastAPI route is added.
2. Future web UI may read control state snapshot (loads, pipeline, mode, warnings).
3. Future web UI write actions are forbidden in this contract.
4. Future operator writes must go through control-layer intent/queue, not direct hardware.
5. Web UI must not bypass safety gates.
6. Web UI must not directly call Tuya/hardware.
7. `execution_allowed_now` remains false until separate safety-reviewed execution PR.
8. ML control remains deferred per ADR-0003.

## 5. Required Module Design

### Module Location

**Create:** `app/control/web_ui_read_contract.py`

**May update:** `app/control/__init__.py`

### Dependencies

Use only stdlib + `app.control.control_state_snapshot` types (`ControlStateSnapshot`,
`ControlStateSnapshotStatus`). Must NOT import FastAPI, APIRouter, Flask, or any
runtime/service/Tuya/device module.

## 6. Required Types (4 Types)

### Type 1: `WebUiReadContractStatus`

```python
class WebUiReadContractStatus(Enum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
```

### Type 2: `WebUiReadEndpointContract`

```python
@dataclass(frozen=True)
class WebUiReadEndpointContract:
    """Contract for a future read-only endpoint.

    path: expected URL path (e.g. "/api/v1/control/state").
    method: HTTP method (always "GET").
    read_only: always true.
    description: human-readable description.
    allowed_actions: list of allowed read actions.
    forbidden_actions: list of explicitly forbidden write actions.
    response_model: description of the response shape.
    notes: additional contract notes.
    """
    path: str = ""
    method: str = "GET"
    read_only: bool = True
    description: str = ""
    allowed_actions: tuple[str, ...] = field(default_factory=tuple)
    forbidden_actions: tuple[str, ...] = field(default_factory=tuple)
    response_model: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)
```

### Type 3: `WebUiControlStateResponse`

```python
@dataclass(frozen=True)
class WebUiControlStateResponse:
    """Response shape for a future web UI read-only API call.

    status: overall response status.
    snapshot: the ControlStateSnapshot (if available).
    read_only: always true.
    api_version: contract version string.
    allowed_actions: list of allowed read actions.
    forbidden_actions: list of explicitly forbidden write actions.
    warnings: tuple of warning strings.
    notes: tuple of note strings.
    """
    status: WebUiReadContractStatus = WebUiReadContractStatus.UNAVAILABLE
    snapshot: ControlStateSnapshot | None = None
    read_only: bool = True
    api_version: str = "0.1.0"
    allowed_actions: tuple[str, ...] = field(default_factory=tuple)
    forbidden_actions: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)
```

### Type 4: `WebUiReadContract`

```python
@dataclass(frozen=True)
class WebUiReadContract:
    """Complete read-only API contract definition.

    Pure data — no FastAPI imports, no routes, no runtime wiring.
    """
    api_version: str = "0.1.0"
    endpoints: tuple[WebUiReadEndpointContract, ...] = field(default_factory=tuple)
    read_only: bool = True
    write_actions_allowed: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)
```

## 7. Required Pure Function

### `build_web_ui_control_state_response`

```python
def build_web_ui_control_state_response(
    snapshot: ControlStateSnapshot | None,
) -> WebUiControlStateResponse
```

Behavior:
1. If `snapshot` is None → return `UNAVAILABLE`.
2. If `snapshot.status` is `OK` → response status `OK`.
3. If `snapshot.status` is `DEGRADED`, `BLOCKED`, or `UNKNOWN` → response status `DEGRADED`
   or `UNAVAILABLE` as appropriate.
4. Preserve the snapshot object.
5. `read_only` must be `true`.
6. `allowed_actions` must include `"read-control-state"`.
7. `forbidden_actions` must include:
   `"direct-hardware-write"`, `"direct-tuya-command"`, `"execute-command"`,
   `"mutate-shared-state"`, `"bypass-control-layer"`, `"bypass-safety-gates"`, `"write-api"`.
8. `notes` must include:
   `"read-only-api-contract"`, `"future-web-ui-read-model"`,
   `"operator-writes-through-control-layer"`, `"no-execution"`.
9. Must not create API endpoints.
10. Must not import FastAPI/Flask/routing.
11. Must not read runtime state.
12. Must not execute, queue, persist, fetch, log, or call runtime.

## 8. Allowed Implementation Files

| File | Action |
|---|---|
| `app/control/web_ui_read_contract.py` | Create |
| `app/control/__init__.py` | Edit |
| `.project-memory/WEB_UI_READ_ONLY_API_CONTRACT.md` | Create |
| `scripts/check-web-ui-read-only-api-contract.sh` | Create |
| `.github/workflows/validate.yml` | Edit |
| `.project-memory/CURRENT_STATE.md` | Edit |
| `.project-memory/ROADMAP.md` | Edit |
| `.project-memory/pr/0025-web-ui-read-only-control-state-api-contract/CODER_REPORT.txt` | Create |

## 9. Forbidden Implementation Files

Standard frozen files list (PR 0014–0024 modules, runtime code, Docker, deployment, configs).

## 10. Static Validation Script

22 checks as specified: module exists, 4 types, function, status values, read_only,
allowed_actions, forbidden_actions, write_actions_allowed, forbidden action strings,
note strings, __init__.py exports, contract doc exists, contract-only language,
no FastAPI/APIRouter/route, no POST/PUT/PATCH/DELETE, no runtime imports, no executor,
no hardware calls, no impurities, runtime files unchanged.

## 11. CURRENT_STATE.md Update

```
## PR 0025 — Web UI Read-Only Control State API Contract
PR 0025 defines the future web UI read-only API contract in
`app/control/web_ui_read_contract.py` with 4 types (WebUiReadContractStatus,
WebUiReadEndpointContract, WebUiControlStateResponse, WebUiReadContract)
and the pure function `build_web_ui_control_state_response()`.
No FastAPI routes. No write API. No runtime wiring. No execution.
```

## 12. ROADMAP.md Update

```
- [x] PR 0025 — Web UI read-only control state API contract
```

## 13. Future PR Boundary

Deferred: Actual read-only API endpoint, web UI pages, operator write intent API.

## 14. Agent Workflow

Standard 4-agent workflow with locked artifacts.

## 15. Boundary Confirmations

- **Contract/model only**: No FastAPI routes, no runtime wiring
- **No write API**: `write_actions_allowed` is `false`
- **No FastAPI import**: Uses only stdlib + snapshot types
- **No executor**: `CommandExecutor` must NOT exist
- **No hardware execution**: Does not call any switch/device/Tuya method
- **No ML control**: ML control deferred per ADR-0003
- **All existing modules unchanged**: PR 0014–0024 frozen
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval
