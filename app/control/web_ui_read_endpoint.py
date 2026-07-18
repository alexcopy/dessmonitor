"""Web UI read-only control state endpoint module for dessmonitor.

Provides an isolated read-only endpoint module for a future ``GET /control/state``
endpoint. Includes:
- Constants: CONTROL_STATE_READ_PATH, CONTROL_STATE_READ_METHOD
- Types: WebUiReadEndpointStatus, WebUiReadEndpointConfig,
  WebUiReadEndpointProviderResult, WebUiReadEndpointRuntime
- Functions: build_control_state_endpoint_response (no FastAPI dependency),
  create_control_state_read_router (lazy FastAPI import)

This module does NOT wire the router into runtime, does NOT edit api.py or
run.py, does NOT add write API, does NOT execute commands, and does NOT read
live shared_state or devices directly. All behavior is pure, deterministic,
and side-effect-free outside the lazy FastAPI import in the router factory.

FastAPI/APIRouter is imported lazily inside create_control_state_read_router()
only — the app.control package remains import-safe even if FastAPI is not
installed.

This module uses only:
  - Python standard library (dataclasses, enum, typing)
  - app.control.control_state_snapshot: ControlStateSnapshot
  - app.control.web_ui_read_contract: WebUiControlStateResponse,
    WebUiReadContractStatus, build_web_ui_control_state_response
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from app.control.control_state_snapshot import ControlStateSnapshot
from app.control.web_ui_read_contract import (
    WebUiControlStateResponse,
    WebUiReadContractStatus,
    build_web_ui_control_state_response,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTROL_STATE_READ_PATH: str = "/control/state"
"""Future GET endpoint path for the read-only control state endpoint."""

CONTROL_STATE_READ_METHOD: str = "GET"
"""HTTP method for the read-only control state endpoint."""

# ---------------------------------------------------------------------------
# WebUiReadEndpointStatus
# ---------------------------------------------------------------------------


class WebUiReadEndpointStatus(Enum):
    """Overall status of the web UI read-only endpoint provider result.

    OK: endpoint response built successfully with available snapshot.
    DEGRADED: snapshot available but degraded or blocked.
    UNAVAILABLE: no snapshot provider available or snapshot unavailable.
    FASTAPI_UNAVAILABLE: FastAPI could not be imported for router creation.
    """
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    FASTAPI_UNAVAILABLE = "fastapi_unavailable"


# ===================================================================
# WebUiReadEndpointConfig
# ===================================================================


@dataclass(frozen=True)
class WebUiReadEndpointConfig:
    """Configuration for the web UI read-only endpoint.

    Pure data — immutable, no runtime wiring.
    """
    path: str = CONTROL_STATE_READ_PATH
    method: str = CONTROL_STATE_READ_METHOD
    read_only: bool = True
    route_wired_now: bool = False
    writes_allowed: bool = False
    execution_allowed: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


# ===================================================================
# WebUiReadEndpointProviderResult
# ===================================================================


@dataclass(frozen=True)
class WebUiReadEndpointProviderResult:
    """Result of building an endpoint response from a snapshot provider.

    Pure data — immutable, no side effects.
    """
    status: WebUiReadEndpointStatus = WebUiReadEndpointStatus.UNAVAILABLE
    response: WebUiControlStateResponse | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)


# ===================================================================
# WebUiReadEndpointRuntime
# ===================================================================


@dataclass(frozen=True)
class WebUiReadEndpointRuntime:
    """Runtime metadata for the web UI read-only endpoint.

    Pure data — immutable, no runtime wiring.
    """
    config: WebUiReadEndpointConfig = field(default_factory=WebUiReadEndpointConfig)
    provider_available: bool = False
    fastapi_required_for_router: bool = True
    route_wired_now: bool = False
    read_only: bool = True
    writes_allowed: bool = False
    execution_allowed: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Shared note/warning strings
# ---------------------------------------------------------------------------

_BASE_RESPONSE_NOTES: tuple[str, ...] = (
    "read-only-endpoint",
    "get-control-state",
    "no-write-api",
    "no-execution",
    "caller-provided-snapshot-provider",
    "operator-writes-through-control-layer",
    "safety-gates-required",
)

_ENDPOINT_RUNTIME_NOTES: tuple[str, ...] = (
    "route-not-wired",
    "no-runtime-wiring",
    "no-execution",
    "no-write-api",
    "fastapi-lazy-import",
)

# Forbidden actions — explicitly disallowed by this endpoint module.
# These strings serve as documentation/contract, not enforcement logic.
_FORBIDDEN_ACTIONS: tuple[str, ...] = (
    "direct-hardware-write",
    "direct-tuya-command",
    "execute-command",
    "mutate-shared-state",
    "bypass-control-layer",
    "bypass-safety-gates",
    "write-api",
    "route-write-methods",
)

# ===================================================================
# build_control_state_endpoint_response
# ===================================================================


def build_control_state_endpoint_response(
    snapshot_provider: Callable[[], ControlStateSnapshot | None] | None,
) -> WebUiReadEndpointProviderResult:
    """Build a web UI read-only endpoint response from a snapshot provider.

    Pure, deterministic, side-effect-free. Does NOT require FastAPI.
    Does NOT call build_control_state_snapshot or build_runtime_control_snapshot.
    Does NOT read runtime state, mutate input, execute commands, or call hardware.

    Args:
        snapshot_provider: Callable that returns ControlStateSnapshot or None
                           (treated as unavailable). None means no provider.

    Returns:
        WebUiReadEndpointProviderResult with status, response, warnings, and notes.
    """
    # Case 1: no provider
    if snapshot_provider is None:
        return WebUiReadEndpointProviderResult(
            status=WebUiReadEndpointStatus.UNAVAILABLE,
            response=build_web_ui_control_state_response(None),
            warnings=("snapshot-provider-missing",),
            notes=_BASE_RESPONSE_NOTES,
        )

    # Cases 2-4: call the provider
    try:
        snapshot = snapshot_provider()
    except Exception:
        # Case 2: provider raises — do not leak exception text
        return WebUiReadEndpointProviderResult(
            status=WebUiReadEndpointStatus.UNAVAILABLE,
            response=build_web_ui_control_state_response(None),
            warnings=("snapshot-provider-error",),
            notes=_BASE_RESPONSE_NOTES,
        )

    # Case 3: provider returns None
    if snapshot is None:
        return WebUiReadEndpointProviderResult(
            status=WebUiReadEndpointStatus.UNAVAILABLE,
            response=build_web_ui_control_state_response(None),
            warnings=("snapshot-unavailable",),
            notes=_BASE_RESPONSE_NOTES,
        )

    # Case 4: provider returns ControlStateSnapshot
    response = build_web_ui_control_state_response(snapshot)

    if response.status == WebUiReadContractStatus.OK:
        result_status = WebUiReadEndpointStatus.OK
    elif response.status == WebUiReadContractStatus.DEGRADED:
        result_status = WebUiReadEndpointStatus.DEGRADED
    else:
        result_status = WebUiReadEndpointStatus.UNAVAILABLE

    return WebUiReadEndpointProviderResult(
        status=result_status,
        response=response,
        warnings=response.warnings,
        notes=_BASE_RESPONSE_NOTES,
    )


# ===================================================================
# create_control_state_read_router
# ===================================================================


def create_control_state_read_router(
    snapshot_provider: Callable[[], ControlStateSnapshot | None],
):
    """Create a FastAPI APIRouter with a single GET /control/state route.

    FastAPI is imported lazily — this function raises RuntimeError if
    FastAPI is unavailable. The returned router is NOT included in any
    application; route_wired_now remains False.

    The route handler is read-only:
    - No POST/PUT/PATCH/DELETE routes
    - No hardware/runtime calls
    - No write API
    - No execution

    Args:
        snapshot_provider: Callable returning ControlStateSnapshot or None.

    Returns:
        A FastAPI APIRouter instance with exactly one GET route.

    Raises:
        RuntimeError: If FastAPI cannot be imported.
    """
    # Lazy import — FastAPI is optional at module-level
    try:
        from fastapi import APIRouter  # noqa: PLC0415  (lazy import)
    except ImportError:
        raise RuntimeError("fastapi-unavailable") from None

    router = APIRouter()

    @router.get(CONTROL_STATE_READ_PATH)
    def get_control_state():
        """Read-only endpoint returning the current control state snapshot."""
        result = build_control_state_endpoint_response(snapshot_provider)
        return result.response

    return router
