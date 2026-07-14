"""Pure health evaluator for dessmonitor.

Assesses whether a device load's observed state matches its expected state
and whether the status data is usable, using only data from HealthInput.

The evaluator is a pure, deterministic function with no side effects:
  - Reads no files, env vars, or network resources.
  - Calls no hardware, runtime services, ML, or monitoring APIs.
  - Does not call time.time() or datetime.now().
  - Same input always produces the same output.

Health is separate from readiness (PR 0014). A device can be ready but
unhealthy, or healthy but not ready. Health does not switch devices or
retry switching — escalation limits are the decision engine's concern.

This module uses only:
  - Python standard library (typing)
  - app.control.energy_policy types
"""

from __future__ import annotations

from app.control.energy_policy import (
    HealthCheckResult,
    HealthInput,
    HealthStatus,
)

# ---------------------------------------------------------------------------
# Default threshold constants (pure values)
# ---------------------------------------------------------------------------

DEFAULT_STALE_STATUS_SECONDS: float = 300.0
DEFAULT_FAILURE_COUNT_THRESHOLD: int = 3

# ---------------------------------------------------------------------------
# Private pure helper functions
# ---------------------------------------------------------------------------


def _check_load_id(load_id: str) -> tuple[bool, str]:
    """Verify load_id is non-empty and valid."""
    if not load_id or not load_id.strip():
        return (True, "invalid-load-id")
    return (False, "")


def _check_unreachable(expected_state: object | None, observed_state: object | None) -> tuple[bool, str]:
    """Detect unreachable state: expected present but observed missing."""
    if expected_state is not None and observed_state is None:
        return (True, "unreachable")
    return (False, "")


def _check_stale(status_age_seconds: float | None) -> tuple[bool, str]:
    """Detect stale status: status age exceeds threshold."""
    if status_age_seconds is not None and status_age_seconds > DEFAULT_STALE_STATUS_SECONDS:
        return (True, "stale-status")
    return (False, "")


def _check_repeated_failures(failure_count: int) -> tuple[bool, str]:
    """Detect repeated failures: failure count at or above threshold."""
    if failure_count >= DEFAULT_FAILURE_COUNT_THRESHOLD:
        return (True, "repeated-failures")
    return (False, "")


def _check_state_mismatch(expected_state: object | None, observed_state: object | None) -> tuple[bool, str]:
    """Detect state mismatch: both present but differ."""
    if expected_state is not None and observed_state is not None:
        if expected_state != observed_state:
            return (True, "state-mismatch")
    return (False, "")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_health(health_input: HealthInput) -> HealthCheckResult:
    """Evaluate whether a device load's observed state is healthy.

    Pure, deterministic, side-effect-free. Uses only data from health_input.
    Does not switch devices, retry switching, or call hardware/runtime services.

    Args:
        health_input: HealthInput with load_id, expected_state, observed_state,
                      status_age_seconds, and failure_count.

    Returns:
        HealthCheckResult with status, reason, and recommended_follow_up.
    """
    # 1. Load ID validation
    blocked, reason = _check_load_id(health_input.load_id)
    if blocked:
        return HealthCheckResult(
            status=HealthStatus.UNKNOWN,
            reason=reason,
            recommended_follow_up="inspect-device",
        )

    # 2. Unreachable: expected present, observed missing
    blocked, reason = _check_unreachable(
        health_input.expected_state,
        health_input.observed_state,
    )
    if blocked:
        return HealthCheckResult(
            status=HealthStatus.UNREACHABLE,
            reason=reason,
            recommended_follow_up="refresh-status",
        )

    # 3. Stale status
    blocked, reason = _check_stale(health_input.status_age_seconds)
    if blocked:
        return HealthCheckResult(
            status=HealthStatus.STALE,
            reason=reason,
            recommended_follow_up="refresh-status",
        )

    # 4. Repeated failures
    blocked, reason = _check_repeated_failures(health_input.failure_count)
    if blocked:
        return HealthCheckResult(
            status=HealthStatus.UNREACHABLE,
            reason=reason,
            recommended_follow_up="investigate-failures",
        )

    # 5. State mismatch
    blocked, reason = _check_state_mismatch(
        health_input.expected_state,
        health_input.observed_state,
    )
    if blocked:
        return HealthCheckResult(
            status=HealthStatus.MISMATCH,
            reason=reason,
            recommended_follow_up="inspect-device",
        )

    # 6. Healthy: both states present and equal
    if (health_input.expected_state is not None
            and health_input.observed_state is not None
            and health_input.expected_state == health_input.observed_state):
        return HealthCheckResult(
            status=HealthStatus.HEALTHY,
            reason="healthy",
            recommended_follow_up="none",
        )

    # 7. Unknown: insufficient data
    return HealthCheckResult(
        status=HealthStatus.UNKNOWN,
        reason="unknown-state",
        recommended_follow_up="refresh-status",
    )
