"""Runtime integration for the read-only web host.

read-only-web-host-integration optional-embedded-task
disabled-by-default no-second-container no-dockerfile-change
no-uvicorn-import-when-disabled no-write-api no-execution
no-device-mutation no-hardware-calls uvicorn-Config-Server
no-uvicorn-run signal-handlers-disabled run-py-owns-signals
no-ml-control

Integrates the existing read-only FastAPI web host from
``app.web_host`` into ``run.py`` as an optional embedded asyncio
task. When ``WEB_HOST_ENABLED`` is falsy (default), Uvicorn is
never imported, no HTTP socket is opened, and existing runtime
behavior is unchanged.

When enabled, a Uvicorn server runs inside the same process using
``uvicorn.Config`` and ``uvicorn.Server`` (never the ``run()`` entry-point),
with signal ownership disabled. Real read-only load state is
provided from the caller-supplied ``devices_provider``.

No second image, no second container, no Dockerfile CMD change.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

WEB_HOST_ENABLED_ENV: str = "WEB_HOST_ENABLED"
WEB_HOST_BIND_ENV: str = "WEB_HOST_BIND"
WEB_HOST_PORT_ENV: str = "WEB_HOST_PORT"

DEFAULT_WEB_HOST_BIND: str = "0.0.0.0"
DEFAULT_WEB_HOST_PORT: int = 8000

# ---------------------------------------------------------------------------
# Environment parsing
# ---------------------------------------------------------------------------

_ENABLED_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def is_runtime_web_host_enabled(
    environ: dict[str, str] | None = None,
) -> bool:
    """Return True only when ``WEB_HOST_ENABLED`` is explicitly enabled."""
    env = environ if environ is not None else os.environ
    raw = env.get(WEB_HOST_ENABLED_ENV, "")
    return raw.strip().lower() in _ENABLED_VALUES


# ---------------------------------------------------------------------------
# RuntimeWebHostHandle
# ---------------------------------------------------------------------------

@dataclass(frozen=False)
class RuntimeWebHostHandle:
    """Handle to a running embedded web host."""
    server: Any
    task: asyncio.Task[Any]
    host: str
    port: int


# ---------------------------------------------------------------------------
# build_runtime_read_model
# ---------------------------------------------------------------------------

_FORBIDDEN_DEVICE_ATTRS: frozenset[str] = frozenset({
    "tuya_device_id", "control_key", "api_key",
    "email", "password", "token", "secret",
})

_FORBIDDEN_RAW_ATTRS: frozenset[str] = frozenset({"status", "extra"})


def _safe_bool_value(value: object) -> bool:
    if isinstance(value, bool): return value
    if isinstance(value, int): return value == 1
    if isinstance(value, str): return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def _normalize_device_type(raw: object) -> str:
    if isinstance(raw, str): return raw.strip().lower()
    return "unknown"


def _safe_float_or_zero(value: object) -> float:
    if isinstance(value, (int, float)): return float(value)
    if isinstance(value, str):
        try: return float(value.strip())
        except (ValueError, TypeError): return 0.0
    return 0.0


def _safe_str(value: object, default: str = "") -> str:
    if isinstance(value, str): return value
    if value is None: return default
    return str(value)


def _safe_mapping_status(device: Any) -> str | None:
    """Return safe mapping status for a device."""
    mapping = getattr(device, "property_mapping", None)
    if mapping is None: return None
    if hasattr(mapping, "mapping_validity"):
        return mapping.mapping_validity.value
    return None


def _safe_communication_status(device: Any) -> str | None:
    """Return safe communication status for a device."""
    return getattr(device, "communication_status", None) or None


def _safe_enabled(device: Any) -> bool:
    """Return enabled status for a device."""
    return bool(getattr(device, "enabled", True))


def _safe_startup_reset_result(device: Any) -> str | None:
    """Return startup reset result from shared coordinator state."""
    return None  # populated at runtime by the coordinator


def _safe_startup_reset_result_from_map(
    device: Any, per_device_results: dict[str, str] | None
) -> str | None:
    """Return startup reset result from a per-device results dict."""
    if per_device_results is None:
        return None
    dev_id = _safe_str(getattr(device, "id", ""))
    return per_device_results.get(dev_id)


def build_runtime_read_model(
    devices: list[Any] | None,
    created_at: str | None = None,
    startup_reset_status: str | None = None,
    startup_reset_gate_open: bool | None = None,
    per_device_results: dict[str, str] | None = None,
    sensors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a runtime state mapping from a list of device objects.

    Each configured device produces exactly one primary projection:
    - LOAD-classified devices appear in loads.
    - SENSOR-classified devices appear in sensors (via the sensors param).
    - No device appears in both collections.
    """
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    snapshot_id = _safe_str(uuid.uuid4())
    loads: list[dict[str, Any]] = []
    if devices:
        for device in devices:
            try:
                from app.devices.relay_channel_device import (
                    classify_projection_kind, DeviceProjectionKind,
                )
                proj = classify_projection_kind(
                    getattr(device, "device_type", ""),
                )
                if proj != DeviceProjectionKind.LOAD:
                    continue
                load_dict = _device_to_load_dict(device, per_device_results)
                if load_dict is not None:
                    loads.append(load_dict)
            except Exception:
                continue
    result: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "created_at": created_at,
        "loads": loads,
    }
    if sensors is not None:
        result["sensors"] = sensors
    if startup_reset_status is not None:
        result["startup_reset_status"] = startup_reset_status
    if startup_reset_gate_open is not None:
        result["startup_reset_gate_open"] = startup_reset_gate_open
    return result


def _device_to_load_dict(
    device: Any,
    per_device_results: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Convert a single device into a safe load dict."""
    load_id = _safe_str(getattr(device, "id", ""))
    display_name = _safe_str(getattr(device, "name", ""))
    configured_load_watts = _safe_float_or_zero(getattr(device, "load_in_wt", 0))

    # --- canonical observation ---
    state_key = getattr(device, "state_key", None)
    obs = getattr(device, "observation", None)
    if obs is not None and hasattr(obs, "observed_state"):
        obs_state = obs.observed_state
        from app.devices.device_observation import ObservationValue
        if obs_state == ObservationValue.ON:
            currently_on = True
        elif obs_state == ObservationValue.OFF:
            currently_on = False
        else:
            currently_on = None
        observed_state_str = obs_state.value
        obs_at = obs.observed_at
        observed_at_str = obs_at.isoformat() if (obs_at is not None and hasattr(obs_at, "isoformat")) else None
        observation_source_str = obs.observation_source
        from app.devices.device_observation import compute_freshness
        freshness_str = compute_freshness(obs).value
    else:
        raw_status = getattr(device, "status", None)
        if raw_status is not None and isinstance(raw_status, dict) and state_key is not None:
            currently_on = _safe_bool_value(raw_status.get(state_key, False))
        else:
            currently_on = False
        observed_state_str = None
        observed_at_str = None
        observation_source_str = None
        freshness_str = "unavailable"

    # --- controllable ---
    available = getattr(device, "available", True)
    enabled = _safe_enabled(device)
    device_type = _normalize_device_type(getattr(device, "device_type", ""))
    if not enabled or not available or state_key is None:
        controllable = False
    else:
        controllable = True

    # --- is_life_support ---
    extra = getattr(device, "extra", None)
    is_life_support = False
    if isinstance(extra, dict):
        is_life_support = bool(extra.get("is_life_support", False))

    # --- roles ---
    roles: list[str] = [device_type] if device_type else []
    if isinstance(extra, dict):
        explicit_roles = extra.get("roles")
        if isinstance(explicit_roles, (list, tuple)):
            for r in explicit_roles:
                if isinstance(r, str) and r.strip():
                    roles.append(r.strip().lower())

    # --- status ---
    is_healthy = getattr(device, "is_healthy", True)
    if not available:
        status_str = "unavailable"
    elif is_healthy:
        status_str = "healthy"
    else:
        status_str = "unhealthy"

    description = _safe_str(getattr(device, "desc", ""))
    from app.devices.relay_channel_device import normalize_device_type
    canonical_dt = normalize_device_type(getattr(device, "device_type", ""))
    return {
        "load_id": load_id,
        "display_name": display_name,
        "description": description,
        "device_type": canonical_dt,
        "configured_load_watts": configured_load_watts,
        "currently_on": currently_on,
        "controllable": controllable,
        "is_life_support": is_life_support,
        "roles": tuple(roles),
        "status": status_str,
        "notes": "",
        "observed_state": observed_state_str,
        "observed_at": observed_at_str,
        "observation_source": observation_source_str,
        "freshness": freshness_str,
        "mapping_status": _safe_mapping_status(device),
        "startup_reset_result": _safe_startup_reset_result_from_map(device, per_device_results),
        "enabled": _safe_enabled(device),
        "communication_status": _safe_communication_status(device),
    }


# ---------------------------------------------------------------------------
# create_runtime_state_provider
# ---------------------------------------------------------------------------

def create_runtime_state_provider(
    devices_provider: Callable[[], list[Any] | None],
    startup_reset_status_provider: Callable[[], str | None] | None = None,
    startup_reset_gate_open_provider: Callable[[], bool | None] | None = None,
    per_device_results_provider: Callable[[], dict[str, str] | None] | None = None,
    sensors_provider: Callable[[], list[dict[str, Any]] | None] | None = None,
) -> Callable[[], dict[str, Any] | None]:
    def _provider() -> dict[str, Any] | None:
        try:
            devs = devices_provider()
        except Exception:
            return None
        if devs is None:
            return None
        srs = startup_reset_status_provider() if startup_reset_status_provider else None
        srg = startup_reset_gate_open_provider() if startup_reset_gate_open_provider else None
        pdr = per_device_results_provider() if per_device_results_provider else None
        sensors = sensors_provider() if sensors_provider else None
        return build_runtime_read_model(
            devs,
            startup_reset_status=srs,
            startup_reset_gate_open=srg,
            per_device_results=pdr,
            sensors=sensors,
        )
    return _provider


# ---------------------------------------------------------------------------
# start_runtime_read_only_web_host
# ---------------------------------------------------------------------------

_STARTUP_TIMEOUT: float = 5.0
_SHUTDOWN_TIMEOUT: float = 5.0


def _make_signal_safe_server(uvicorn_module: Any) -> type:
    class SignalSafeServer(uvicorn_module.Server):
        def install_signal_handlers(self) -> None:
            return

        @contextmanager
        def capture_signals(self):
            yield

    return SignalSafeServer


async def start_runtime_read_only_web_host(
    devices_provider: Callable[[], list[Any] | None],
    environ: dict[str, str] | None = None,
    logger: logging.Logger | None = None,
    startup_reset_status_provider: Callable[[], str | None] | None = None,
    startup_reset_gate_open_provider: Callable[[], bool | None] | None = None,
    per_device_results_provider: Callable[[], dict[str, str] | None] | None = None,
    sensors_provider: Callable[[], list[dict[str, Any]] | None] | None = None,
) -> RuntimeWebHostHandle | None:
    env = environ if environ is not None else os.environ
    if not is_runtime_web_host_enabled(env):
        return None
    try:
        import uvicorn
    except ImportError:
        raise RuntimeError("uvicorn-unavailable") from None
    host = env.get(WEB_HOST_BIND_ENV, DEFAULT_WEB_HOST_BIND).strip() or DEFAULT_WEB_HOST_BIND
    raw_port = env.get(WEB_HOST_PORT_ENV, str(DEFAULT_WEB_HOST_PORT)).strip()
    try:
        port = int(raw_port)
    except (ValueError, TypeError):
        raise RuntimeError("invalid-web-host-port") from None
    if port < 1 or port > 65535:
        raise RuntimeError("invalid-web-host-port")
    provider = create_runtime_state_provider(
        devices_provider,
        startup_reset_status_provider=startup_reset_status_provider,
        startup_reset_gate_open_provider=startup_reset_gate_open_provider,
        per_device_results_provider=per_device_results_provider,
        sensors_provider=sensors_provider,
    )
    from app.web_host import create_app
    app = create_app(runtime_state_provider=provider)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    config.reload = False
    config.workers = 1
    SignalSafeServer = _make_signal_safe_server(uvicorn)
    server = SignalSafeServer(config=config)
    if logger is not None:
        logger.info(f"[WEB] Starting read-only web host on {host}:{port}")
    server_task = asyncio.create_task(server.serve())
    try:
        await asyncio.wait_for(_wait_for_startup(server, server_task), timeout=_STARTUP_TIMEOUT)
    except asyncio.TimeoutError:
        if server_task.done():
            exc = server_task.exception()
            _log_startup_failure(logger, exc)
            server.should_exit = True
            raise RuntimeError("web-host-task-exited-early") from exc
        if logger is not None:
            logger.warning("[WEB] Web host startup timed out after %.0fs", _STARTUP_TIMEOUT)
        server.should_exit = True
        server_task.cancel()
        try:
            await asyncio.wait_for(server_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        raise RuntimeError("web-host-startup-timeout")
    if logger is not None:
        logger.info(f"[WEB] Read-only web host ready on {host}:{port}")
    return RuntimeWebHostHandle(server=server, task=server_task, host=host, port=port)


async def _wait_for_startup(server: Any, server_task: asyncio.Task[Any]) -> None:
    while not getattr(server, "started", False):
        if server_task.done():
            exc = server_task.exception()
            if exc is not None:
                raise exc
            raise RuntimeError("web-host-task-exited-early")
        await asyncio.sleep(0.1)


def _log_startup_failure(logger: logging.Logger | None, exc: BaseException | None) -> None:
    msg = f"[WEB] Web host task exited before startup: {exc}"
    if logger is not None:
        logger.warning(msg)
    print(msg, file=sys.stderr)
    if exc is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)


async def stop_runtime_read_only_web_host(
    handle: RuntimeWebHostHandle | None,
    logger: logging.Logger | None = None,
) -> None:
    if handle is None:
        return
    if logger is not None:
        logger.info("[WEB] Stopping read-only web host...")
    handle.server.should_exit = True
    try:
        await asyncio.wait_for(handle.task, timeout=_SHUTDOWN_TIMEOUT)
    except asyncio.TimeoutError:
        if logger is not None:
            logger.warning("[WEB] Web host shutdown timed out, cancelling task")
        handle.task.cancel()
        try:
            await asyncio.gather(handle.task, return_exceptions=True)
        except Exception:
            pass
    if logger is not None:
        logger.info("[WEB] Read-only web host stopped")
