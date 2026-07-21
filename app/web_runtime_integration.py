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
import uuid
from contextlib import asynccontextmanager
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
    """Return True only when ``WEB_HOST_ENABLED`` is explicitly enabled.

    Args:
        environ: Optional environment mapping (defaults to ``os.environ``).

    Returns:
        ``True`` when the env value (case-insensitive) is one of
        ``{"1", "true", "yes", "on"}``. ``False`` for every other
        value or when the variable is absent.
    """
    env = environ if environ is not None else os.environ
    raw = env.get(WEB_HOST_ENABLED_ENV, "")
    return raw.strip().lower() in _ENABLED_VALUES


# ---------------------------------------------------------------------------
# RuntimeWebHostHandle
# ---------------------------------------------------------------------------


@dataclass(frozen=False)
class RuntimeWebHostHandle:
    """Handle to a running embedded web host.

    Attributes:
        server: The ``uvicorn.Server`` instance.
        task: The asyncio ``Task`` running ``server.serve()``.
        host: The bind address.
        port: The bound port.
    """
    server: Any  # uvicorn.Server (lazy import)
    task: asyncio.Task[Any]
    host: str
    port: int


# ---------------------------------------------------------------------------
# build_runtime_read_model
# ---------------------------------------------------------------------------

# Attributes we explicitly refuse to expose in the read model.
_FORBIDDEN_DEVICE_ATTRS: frozenset[str] = frozenset({
    "tuya_device_id",
    "control_key",
    "api_key",
    "email",
    "password",
    "token",
    "secret",
})

# Attributes we refuse to serialise as-is (raw data structures).
_FORBIDDEN_RAW_ATTRS: frozenset[str] = frozenset({
    "status",
    "extra",
})


def _safe_bool_value(value: object) -> bool:
    """Convert *value* to a boolean safely.

    Accepts ``bool``, ``int`` (0/1), and truthy/falsy strings.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def _normalize_device_type(raw: object) -> str:
    """Return a safe, lower-cased device-type string."""
    if isinstance(raw, str):
        return raw.strip().lower()
    return "unknown"


def _safe_float_or_zero(value: object) -> float:
    """Return *value* as float, or 0.0 on failure."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def _safe_str(value: object, default: str = "") -> str:
    """Safe string conversion."""
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def build_runtime_read_model(
    devices: list[Any] | None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a runtime state mapping from a list of device objects.

    Reads only safe, non-sensitive attributes from each device.
    Never exposes ``tuya_device_id``, ``control_key``, ``api_key``,
    raw ``status`` dict, raw ``extra`` dict, credentials, or tokens.
    Never mutates input devices or calls any hardware method.

    Args:
        devices: List of device objects (e.g. ``RelayChannelDevice``).
        created_at: Optional ISO-8601 timestamp (generated if ``None``).

    Returns:
        A ``dict`` suitable for passing to
        :func:`app.web_control_state_provider.build_control_state_snapshot_from_runtime_state`.
    """
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()

    snapshot_id = _safe_str(uuid.uuid4())

    loads: list[dict[str, Any]] = []

    if devices:
        for device in devices:
            try:
                load_dict = _device_to_load_dict(device)
                if load_dict is not None:
                    loads.append(load_dict)
            except Exception:
                # Skip malformed devices silently
                continue

    return {
        "snapshot_id": snapshot_id,
        "created_at": created_at,
        "loads": loads,
    }


def _device_to_load_dict(device: Any) -> dict[str, Any] | None:
    """Convert a single device into a safe load dict.

    Returns ``None`` for unreadable devices.
    """
    # --- load_id -----------------------------------------------------------
    load_id = _safe_str(getattr(device, "id", ""))

    # --- display_name ------------------------------------------------------
    display_name = _safe_str(getattr(device, "name", ""))

    # --- configured_load_watts ---------------------------------------------
    configured_load_watts = _safe_float_or_zero(getattr(device, "load_in_wt", 0))

    # --- currently_on ------------------------------------------------------
    state_key = getattr(device, "state_key", None)
    raw_status = getattr(device, "status", None)
    if raw_status is not None and isinstance(raw_status, dict) and state_key is not None:
        currently_on = _safe_bool_value(raw_status.get(state_key, False))
    else:
        currently_on = False

    # --- controllable ------------------------------------------------------
    available = getattr(device, "available", True)
    device_type = _normalize_device_type(getattr(device, "device_type", ""))
    if not available or state_key is None or device_type in ("sensor", "thermo", "thermometer", "watertemp", "water_thermo", "termo", "termo_sensor", "temp_sensor"):
        controllable = False
    else:
        controllable = True

    # --- is_life_support ---------------------------------------------------
    extra = getattr(device, "extra", None)
    is_life_support = False
    if isinstance(extra, dict):
        is_life_support = bool(extra.get("is_life_support", False))

    # --- roles -------------------------------------------------------------
    roles: list[str] = [device_type] if device_type else []
    if isinstance(extra, dict):
        explicit_roles = extra.get("roles")
        if isinstance(explicit_roles, (list, tuple)):
            for r in explicit_roles:
                if isinstance(r, str) and r.strip():
                    roles.append(r.strip().lower())

    # --- status ------------------------------------------------------------
    is_healthy = getattr(device, "is_healthy", True)
    if not available:
        status_str = "unavailable"
    elif is_healthy:
        status_str = "healthy"
    else:
        status_str = "unhealthy"

    return {
        "load_id": load_id,
        "display_name": display_name,
        "configured_load_watts": configured_load_watts,
        "currently_on": currently_on,
        "controllable": controllable,
        "is_life_support": is_life_support,
        "roles": tuple(roles),
        "status": status_str,
        "notes": "",
    }


# ---------------------------------------------------------------------------
# create_runtime_state_provider
# ---------------------------------------------------------------------------


def create_runtime_state_provider(
    devices_provider: Callable[[], list[Any] | None],
) -> Callable[[], dict[str, Any] | None]:
    """Create a synchronous runtime-state provider callable.

    The returned callable calls *devices_provider* on every
    invocation, converts the device list via
    :func:`build_runtime_read_model`, and returns the mapping.

    If *devices_provider* raises, ``None`` is returned silently
    (no exception text leaked).

    Args:
        devices_provider: A callable that returns a list of device
            objects (or ``None``).

    Returns:
        A zero-argument callable that returns ``dict | None``.
    """

    def _provider() -> dict[str, Any] | None:
        try:
            devs = devices_provider()
        except Exception:
            return None
        if devs is None:
            return None
        return build_runtime_read_model(devs)

    return _provider


# ---------------------------------------------------------------------------
# start_runtime_read_only_web_host
# ---------------------------------------------------------------------------


_STARTUP_TIMEOUT: float = 5.0
_SHUTDOWN_TIMEOUT: float = 5.0


class _SignalSafeServer:
    """Mixin / wrapper to disable Uvicorn signal handling.

    When Uvicorn's ``Server`` is created, this class overrides
    ``install_signal_handlers`` and ``capture_signals`` so that
    ``run.py`` remains the sole signal owner.
    """


def _make_signal_safe_server(uvicorn_module: Any) -> type:
    """Create a Uvicorn Server subclass with signal handling disabled."""

    class SignalSafeServer(uvicorn_module.Server):
        def install_signal_handlers(self) -> None:
            """No-op: run.py owns all signal handlers."""
            return

        @asynccontextmanager
        async def capture_signals(self):  # type: ignore[override]
            """No-op: do not capture signals inside uvicorn."""
            yield

    return SignalSafeServer


async def start_runtime_read_only_web_host(
    devices_provider: Callable[[], list[Any] | None],
    environ: dict[str, str] | None = None,
    logger: logging.Logger | None = None,
) -> RuntimeWebHostHandle | None:
    """Start the read-only web host as an embedded asyncio task.

    When ``WEB_HOST_ENABLED`` is falsy (default) this function
    returns ``None`` immediately without importing Uvicorn or
    opening a socket.

    Args:
        devices_provider: Callable returning the current device
            list (e.g. ``dev_mgr.get_devices``).
        environ: Optional env mapping (defaults to ``os.environ``).
        logger: Optional logger for diagnostic messages.

    Returns:
        ``RuntimeWebHostHandle`` on success, ``None`` when disabled.

    Raises:
        RuntimeError: If the web host is enabled but ``uvicorn``
            cannot be imported (``"uvicorn-unavailable"``) or the
            configured port is invalid (``"invalid-web-host-port"``).
    """
    env = environ if environ is not None else os.environ

    if not is_runtime_web_host_enabled(env):
        return None

    # --- Import uvicorn lazily (NOT at module level) -----------------------
    try:
        import uvicorn  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("uvicorn-unavailable") from None

    # --- Parse host --------------------------------------------------------
    host = env.get(WEB_HOST_BIND_ENV, DEFAULT_WEB_HOST_BIND).strip() or DEFAULT_WEB_HOST_BIND

    # --- Parse port --------------------------------------------------------
    raw_port = env.get(WEB_HOST_PORT_ENV, str(DEFAULT_WEB_HOST_PORT)).strip()
    try:
        port = int(raw_port)
    except (ValueError, TypeError):
        raise RuntimeError("invalid-web-host-port") from None
    if port < 1 or port > 65535:
        raise RuntimeError("invalid-web-host-port")

    # --- Build the runtime state provider ----------------------------------
    provider = create_runtime_state_provider(devices_provider)

    # --- Create the FastAPI app --------------------------------------------
    from app.web_host import create_app  # noqa: PLC0415

    app = create_app(runtime_state_provider=provider)

    # --- Configure Uvicorn (no signal handlers, no reload, no workers) -----
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
    )
    # Ensure no reload, no workers
    config.reload = False
    config.workers = 1

    # --- Create signal-safe server -----------------------------------------
    SignalSafeServer = _make_signal_safe_server(uvicorn)
    server = SignalSafeServer(config=config)

    if logger is not None:
        logger.info(f"[WEB] Starting read-only web host on {host}:{port}")

    # --- Create and monitor the server task --------------------------------
    server_task = asyncio.create_task(server.serve())

    # Wait for startup (or detect early failure)
    try:
        await asyncio.wait_for(_wait_for_startup(server), timeout=_STARTUP_TIMEOUT)
    except asyncio.TimeoutError:
        if logger is not None:
            logger.warning("[WEB] Web host startup timed out")
        server.should_exit = True
        server_task.cancel()
        try:
            await asyncio.wait_for(server_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        raise RuntimeError("web-host-startup-timeout")

    # Check if task exited prematurely
    if server_task.done():
        exc = server_task.exception()
        if logger is not None:
            logger.warning(f"[WEB] Web host task exited before startup: {exc}")
        server.should_exit = True
        raise RuntimeError("web-host-task-exited-early")

    if logger is not None:
        logger.info(f"[WEB] Read-only web host ready on {host}:{port}")

    return RuntimeWebHostHandle(
        server=server,
        task=server_task,
        host=host,
        port=port,
    )


async def _wait_for_startup(server: Any) -> None:
    """Poll until ``server.started`` is ``True``."""
    while not getattr(server, "started", False):
        await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# stop_runtime_read_only_web_host
# ---------------------------------------------------------------------------


async def stop_runtime_read_only_web_host(
    handle: RuntimeWebHostHandle | None,
    logger: logging.Logger | None = None,
) -> None:
    """Gracefully stop a running embedded web host.

    Args:
        handle: The handle returned by
            :func:`start_runtime_read_only_web_host`, or ``None``.
        logger: Optional logger.
    """
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
