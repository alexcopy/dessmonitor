"""Web UI read-only control state API contract for dessmonitor.

Defines the future web UI read-only API contract — pure types and a pure
function that packages a ControlStateSnapshot into a WebUiControlStateResponse
with read-only guarantees, allowed/forbidden action lists, and architecture notes.

This is contract/model/docs only. It does NOT add real API endpoints,
edit route files, wire into runtime, read live shared_state, or execute
commands. A separate future PR will implement the actual read-only API endpoint.

This module uses only:
  - Python standard library (dataclasses, enum, field)
  - app.control.control_state_snapshot types: ControlStateSnapshot, ControlStateSnapshotStatus
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.control.control_state_snapshot import (
    ControlStateSnapshot,
    ControlStateSnapshotStatus,
)


# ---------------------------------------------------------------------------
# WebUiReadContractStatus — response health
# ---------------------------------------------------------------------------


class WebUiReadContractStatus(Enum):
    """Overall status of a future web UI read-only API response.

    OK: snapshot is available and healthy.
    DEGRADED: snapshot is degraded or blocked.
    UNAVAILABLE: no snapshot available.
    """
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


# ===================================================================
# Type 1: WebUiReadEndpointContract
# ===================================================================


@dataclass(frozen=True)
class WebUiReadEndpointContract:
    """Contract for a future read-only endpoint.

    path: expected URL path (e.g. "/control/state").
    method: HTTP method (always "GET").
    read_only: always true.
    description: human-readable description.
    allowed_actions: list of allowed read actions.
    forbidden_actions: list of explicitly forbidden write actions.
    response_model: description of the response shape.
    notes: additional contract notes.
    """
    path: str = "/control/state"
    method: str = "GET"
    read_only: bool = True
    description: str = "Read-only control state snapshot for future web UI."
    allowed_actions: tuple[str, ...] = ("read-control-state",)
    forbidden_actions: tuple[str, ...] = (
        "direct-hardware-write",
        "direct-tuya-command",
        "execute-command",
        "mutate-shared-state",
        "bypass-control-layer",
        "bypass-safety-gates",
        "write-api",
    )
    response_model: str = "WebUiControlStateResponse"
    notes: tuple[str, ...] = (
        "read-only-api-contract",
        "future-web-ui-read-model",
        "operator-writes-through-control-layer",
        "no-execution",
    )


# ===================================================================
# Type 2: WebUiControlStateResponse
# ===================================================================


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
    api_version: str = "v1"
    allowed_actions: tuple[str, ...] = ("read-control-state",)
    forbidden_actions: tuple[str, ...] = (
        "direct-hardware-write",
        "direct-tuya-command",
        "execute-command",
        "mutate-shared-state",
        "bypass-control-layer",
        "bypass-safety-gates",
        "write-api",
    )
    warnings: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = (
        "read-only-api-contract",
        "future-web-ui-read-model",
        "operator-writes-through-control-layer",
        "no-execution",
    )


# ===================================================================
# Type 3: WebUiReadContract
# ===================================================================


@dataclass(frozen=True)
class WebUiReadContract:
    """Complete read-only API contract definition.

    Pure data — no FastAPI imports, no routes, no runtime wiring.
    """
    api_version: str = "v1"
    endpoints: tuple[WebUiReadEndpointContract, ...] = (WebUiReadEndpointContract(),)
    read_only: bool = True
    write_actions_allowed: bool = False
    notes: tuple[str, ...] = (
        "contract-only",
        "no-real-api-endpoint",
        "read-only-api-contract",
        "future-web-ui-read-model",
        "operator-writes-through-control-layer",
        "no-execution",
    )


# ===================================================================
# build_web_ui_control_state_response — pure response builder
# ===================================================================


def build_web_ui_control_state_response(
    snapshot: ControlStateSnapshot | None,
) -> WebUiControlStateResponse:
    """Build a future web UI read-only API response from a snapshot.

    Pure, deterministic, side-effect-free. Does NOT create API endpoints,
    import web frameworks, wire into runtime, or execute commands.

    Args:
        snapshot: ControlStateSnapshot from the snapshot module
                  (None treated as unavailable).

    Returns:
        WebUiControlStateResponse with status, snapshot, read_only guarantees,
        allowed/forbidden actions, and architecture notes.
    """
    # Determine status from snapshot
    if snapshot is None:
        return WebUiControlStateResponse(
            status=WebUiReadContractStatus.UNAVAILABLE,
            snapshot=None,
            read_only=True,
            api_version="v1",
            allowed_actions=("read-control-state",),
            forbidden_actions=(
                "direct-hardware-write",
                "direct-tuya-command",
                "execute-command",
                "mutate-shared-state",
                "bypass-control-layer",
                "bypass-safety-gates",
                "write-api",
            ),
            warnings=("control-state-snapshot-unavailable",),
            notes=(
                "read-only-api-contract",
                "future-web-ui-read-model",
                "operator-writes-through-control-layer",
                "no-execution",
            ),
        )

    # Map snapshot status to response status
    snap_status = snapshot.status
    warnings: list[str] = []

    if snap_status == ControlStateSnapshotStatus.OK:
        response_status = WebUiReadContractStatus.OK
    elif snap_status in (ControlStateSnapshotStatus.DEGRADED, ControlStateSnapshotStatus.BLOCKED):
        response_status = WebUiReadContractStatus.DEGRADED
        warnings.append("control-state-snapshot-degraded")
    else:  # UNKNOWN
        response_status = WebUiReadContractStatus.UNAVAILABLE
        warnings.append("control-state-snapshot-unknown")

    return WebUiControlStateResponse(
        status=response_status,
        snapshot=snapshot,
        read_only=True,
        api_version="v1",
        allowed_actions=("read-control-state",),
        forbidden_actions=(
            "direct-hardware-write",
            "direct-tuya-command",
            "execute-command",
            "mutate-shared-state",
            "bypass-control-layer",
            "bypass-safety-gates",
            "write-api",
        ),
        warnings=tuple(warnings),
        notes=(
            "read-only-api-contract",
            "future-web-ui-read-model",
            "operator-writes-through-control-layer",
            "no-execution",
        ),
    )
