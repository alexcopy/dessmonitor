"""Web UI read-only control state endpoint implementation plan for dessmonitor.

Defines the future implementation plan for a `GET /control/state` read-only
endpoint. Provides types and a pure function that document what the future
endpoint will look like, its data sources, its boundaries, and the
implementation steps required — without adding a real API endpoint now.

This is contract/model/docs only. It does NOT add a real API endpoint,
edit route files, write to hardware, or wire runtime.

This module uses only:
  - Python standard library (dataclasses, enum, field)
  - app.control.web_ui_read_contract types: WebUiReadContract, WebUiReadContractStatus
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.control.web_ui_read_contract import (
    WebUiReadContract,
    WebUiReadContractStatus,
)


# ---------------------------------------------------------------------------
# WebUiReadEndpointPlanStatus — plan health
# ---------------------------------------------------------------------------


class WebUiReadEndpointPlanStatus(Enum):
    """Overall status of the future endpoint implementation plan.

    DRAFT: plan is still being defined.
    READY_FOR_FUTURE_IMPLEMENTATION: plan is complete, awaiting future PR.
    BLOCKED: plan is blocked due to contract or safety issues.
    """
    DRAFT = "draft"
    READY_FOR_FUTURE_IMPLEMENTATION = "ready_for_future_implementation"
    BLOCKED = "blocked"


# ===================================================================
# Type 1: WebUiReadEndpointDataSource
# ===================================================================


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


# ===================================================================
# Type 2: WebUiReadEndpointBoundary
# ===================================================================


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
    allowed_actions: tuple[str, ...] = ("read-control-state",)
    forbidden_actions: tuple[str, ...] = (
        "direct-hardware-write",
        "direct-tuya-command",
        "execute-command",
        "mutate-shared-state",
        "bypass-control-layer",
        "bypass-safety-gates",
        "write-api",
        "route-write-methods",
    )
    response_model: str = "WebUiControlStateResponse"
    notes: tuple[str, ...] = (
        "endpoint-plan-only",
        "no-real-api-endpoint",
        "read-only-endpoint-future",
        "future-separate-pr-required",
        "no-execution",
        "no-runtime-wiring",
        "no-write-api",
        "control-layer-only",
        "safety-gates-required",
        "operator-writes-through-control-layer",
    )


# ===================================================================
# Type 3: WebUiReadEndpointImplementationStep
# ===================================================================


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


# ===================================================================
# Type 4: WebUiReadEndpointPlan
# ===================================================================


@dataclass(frozen=True)
class WebUiReadEndpointPlan:
    """Complete endpoint implementation plan.

    Pure data — no route registrations, no runtime wiring.
    """
    status: WebUiReadEndpointPlanStatus = WebUiReadEndpointPlanStatus.DRAFT
    contract: WebUiReadContract | None = None
    endpoint: WebUiReadEndpointBoundary = field(
        default_factory=WebUiReadEndpointBoundary,
    )
    data_sources: tuple[WebUiReadEndpointDataSource, ...] = field(default_factory=tuple)
    implementation_steps: tuple[
        WebUiReadEndpointImplementationStep, ...
    ] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Pre-defined data source for the runtime snapshot adapter path
# ---------------------------------------------------------------------------

_RUNTIME_ADAPTER_SOURCE = WebUiReadEndpointDataSource(
    name="runtime-control-snapshot-adapter",
    description=(
        "Uses the runtime snapshot adapter (PR 0024) to transform caller-provided "
        "runtime state into a ControlStateSnapshot, then packages it via "
        "the web UI read contract (PR 0025) for the web UI."
    ),
    read_only=True,
    caller_provided_state_only=True,
    live_shared_state_reads_allowed=False,
    direct_device_reads_allowed=False,
    notes=(
        "all-state-is-caller-provided",
        "no-live-shared-state-reads",
        "no-direct-device-reads",
    ),
)


# ---------------------------------------------------------------------------
# Pre-defined implementation steps
# ---------------------------------------------------------------------------

_IMPLEMENTATION_STEPS = (
    WebUiReadEndpointImplementationStep(
        step_id="add-read-only-route",
        description="Register GET /control/state as a read-only route.",
        requires_separate_pr=True,
        safety_review_required=True,
        allowed_in_this_pr=False,
    ),
    WebUiReadEndpointImplementationStep(
        step_id="connect-read-only-adapter",
        description="Wire the runtime snapshot adapter (PR 0024) to populate ControlStateSnapshot.",
        requires_separate_pr=True,
        safety_review_required=True,
        allowed_in_this_pr=False,
    ),
    WebUiReadEndpointImplementationStep(
        step_id="return-web-ui-control-state-response",
        description="Use the web UI read contract (PR 0025) to produce the response.",
        requires_separate_pr=True,
        safety_review_required=True,
        allowed_in_this_pr=False,
    ),
    WebUiReadEndpointImplementationStep(
        step_id="add-endpoint-validation",
        description="Add a validation script verifying the endpoint returns read-only responses.",
        requires_separate_pr=True,
        safety_review_required=True,
        allowed_in_this_pr=False,
    ),
)


# ===================================================================
# build_web_ui_read_endpoint_plan — pure plan builder
# ===================================================================


def build_web_ui_read_endpoint_plan(
    contract: WebUiReadContract | None = None,
) -> WebUiReadEndpointPlan:
    """Build the future endpoint implementation plan.

    Pure, deterministic, side-effect-free. Does NOT add API endpoints,
    Does NOT add API endpoints, import web frameworks, wire into runtime,

    Args:
        contract: WebUiReadContract from PR 0025 (None treated as default).

    Returns:
        WebUiReadEndpointPlan with status, endpoint boundary, data sources,
        implementation steps, warnings, and notes.
    """
    warnings: list[str] = []

    # Use default contract if none provided
    if contract is None:
        contract = WebUiReadContract()
        warnings.append("default-contract-used")

    # Check contract validity
    if not contract.read_only or contract.write_actions_allowed:
        endpoint = WebUiReadEndpointBoundary()
        return WebUiReadEndpointPlan(
            status=WebUiReadEndpointPlanStatus.BLOCKED,
            contract=contract,
            endpoint=endpoint,
            data_sources=(_RUNTIME_ADAPTER_SOURCE,),
            implementation_steps=_IMPLEMENTATION_STEPS,
            warnings=tuple(warnings + ["contract-not-read-only"]),
            notes=(
                "endpoint-plan-only",
                "no-real-api-endpoint",
                "no-execution",
                "no-runtime-wiring",
                "operator-writes-through-control-layer",
            ),
        )

    # Plan is ready
    endpoint = WebUiReadEndpointBoundary()
    return WebUiReadEndpointPlan(
        status=WebUiReadEndpointPlanStatus.READY_FOR_FUTURE_IMPLEMENTATION,
        contract=contract,
        endpoint=endpoint,
        data_sources=(_RUNTIME_ADAPTER_SOURCE,),
        implementation_steps=_IMPLEMENTATION_STEPS,
        warnings=tuple(warnings),
        notes=(
            "endpoint-plan-only",
            "no-real-api-endpoint",
            "read-only-endpoint-future",
            "future-separate-pr-required",
            "no-execution",
            "no-runtime-wiring",
            "no-write-api",
            "control-layer-only",
            "safety-gates-required",
            "operator-writes-through-control-layer",
        ),
    )
