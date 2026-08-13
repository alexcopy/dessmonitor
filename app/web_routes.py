"""Route handlers for the authenticated web operator surface.

Provides request handlers for:
- GET /healthz (public, minimal)
- GET /login (public, renders login form)
- POST /login (public + CSRF, authenticates)
- POST /logout (auth + CSRF, clears session)
- GET / (auth, renders landing shell)

Does NOT implement dashboard, polling, device writes, or hardware access.
No imports of hardware, Tuya, relay, device, monitoring, ML, or weather modules.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from app.web_auth import (
    LoginThrottle,
    WebAuthConfig,
    create_session_data,
    generate_csrf_token,
    validate_csrf_token,
    validate_session,
    verify_password,
)
from app.device_initializer import DeviceInitializer
from shared_state.shared_state import shared_state

# ---------------------------------------------------------------------------
# Templates — use absolute path so working-directory changes are safe
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "web", "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

# ---------------------------------------------------------------------------
# Route factory
# ---------------------------------------------------------------------------


def create_auth_router(
    config: WebAuthConfig,
    throttler: LoginThrottle,
) -> APIRouter:
    """Create a FastAPI APIRouter with auth routes.

    Injects the authentication configuration and throttler into all
    handlers via closure.

    Args:
        config: The loaded authentication configuration.
        throttler: The login throttler instance.

    Returns:
        An ``APIRouter`` with auth-related routes.
    """
    router = APIRouter()

    # -- /healthz -------------------------------------------------------

    @router.get("/healthz")
    async def healthz() -> JSONResponse:
        """Public health endpoint — minimal, non-sensitive."""
        return JSONResponse(
            content={"status": "ok", "web_api": "available"},
            status_code=200,
        )

    # -- /login GET -----------------------------------------------------

    @router.get("/login")
    async def login_get(request: Request) -> Any:
        """Render the login form.

        Authenticated users are redirected to ``/``.
        Unauthenticated users receive a CSRF token in the form.
        """
        valid, _user = _check_auth(request)
        if valid:
            return RedirectResponse("/", status_code=303)

        csrf = generate_csrf_token(request.session)
        response = templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"csrf_token": csrf},
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    # -- /login POST ----------------------------------------------------

    @router.post("/login")
    async def login_post(request: Request) -> Any:
        """Authenticate the operator.

        CSRF token must match.  Throttling applies.
        Generic error on any failure (no credential leakage).
        Successful login creates a fresh session and redirects to ``/``.
        """
        # CSRF check
        form = await request.form()
        form_csrf = form.get("csrf_token")
        if isinstance(form_csrf, str):
            form_csrf = form_csrf.strip()
        else:
            form_csrf = None

        if not validate_csrf_token(request.session, form_csrf):
            return JSONResponse(
                content={"detail": "Invalid request."},
                status_code=403,
            )

        # Extract credentials (never log them)
        username_raw = form.get("username")
        username = username_raw.strip() if isinstance(username_raw, str) else ""
        password_raw = form.get("password")
        password = password_raw if isinstance(password_raw, str) else ""

        # Source IP from request
        source_ip = request.client.host if request.client else "127.0.0.1"

        # Throttle check
        if not throttler.check(username if username else "__empty__", source_ip):
            throttler.record_failure(username if username else "__empty__", source_ip)
            return _login_error(request, "Invalid credentials.")

        # Username check (case-sensitive)
        if username != config.username:
            throttler.record_failure(username, source_ip)
            return _login_error(request, "Invalid credentials.")

        # Password check
        try:
            pw_ok = verify_password(config.password_hash, password)
        except Exception:
            # Unexpected Argon2 error — config problem, not credential failure
            throttler.record_failure(username, source_ip)
            return _login_error(request, "Invalid credentials.")

        if not pw_ok:
            throttler.record_failure(username, source_ip)
            return _login_error(request, "Invalid credentials.")

        # Success — clear throttle, create session
        throttler.clear(username, source_ip)
        request.session.clear()
        session_data = create_session_data(username)
        request.session.update(session_data)

        response = RedirectResponse("/", status_code=303)
        return response

    # -- /logout POST ---------------------------------------------------

    @router.post("/logout")
    async def logout_post(request: Request) -> Any:
        """Log out the current operator.

        Requires authentication and CSRF token.
        Clears the session and deletes the cookie.
        """
        # Authentication required
        valid, _user = _check_auth(request)
        if not valid:
            return RedirectResponse("/login", status_code=303)

        # CSRF check
        form = await request.form()
        form_csrf = form.get("csrf_token")
        if isinstance(form_csrf, str):
            form_csrf = form_csrf.strip()
        else:
            form_csrf = None

        if not validate_csrf_token(request.session, form_csrf):
            return JSONResponse(
                content={"detail": "Invalid request."},
                status_code=403,
            )

        # Clear session
        request.session.clear()

        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("dessmonitor_session")
        return response

    # -- / GET ----------------------------------------------------------

    @router.get("/")
    async def index_get(request: Request) -> Any:
        """Render the authenticated landing shell.

        Unauthenticated users are redirected to ``/login``.
        """
        valid, user = _check_auth(request)
        if not valid:
            return RedirectResponse("/login", status_code=303)

        csrf = generate_csrf_token(request.session)
        response = templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"username": user, "csrf_token": csrf},
        )
        response.headers["Cache-Control"] = "no-store"
        return response


    # -- /alarms GET --------------------------------------------------------
    @router.get("/alarms")
    async def alarms_get(request: Request) -> Any:
        auth_ok, username = _check_auth(request)
        if not auth_ok:
            return RedirectResponse("/login", status_code=302)
        return templates.TemplateResponse(
            request=request,
            name="alarms.html",
            context={"username": username},
        )

    # -- /analytics GET --------------------------------------------------

    @router.get("/analytics")
    async def analytics_get(request: Request) -> Any:
        """Render the authenticated analytics page.

        Unauthenticated users are redirected to ``/login``.
        """
        valid, user = _check_auth(request)
        if not valid:
            return RedirectResponse("/login", status_code=303)

        csrf = generate_csrf_token(request.session)
        response = templates.TemplateResponse(
            request=request,
            name="analytics.html",
            context={"username": user, "csrf_token": csrf},
        )
        response.headers["Cache-Control"] = "no-store"
        return response


    # -- /controls GET --------------------------------------------------

    @router.get("/controls")
    async def controls_get(request: Request) -> Any:
        """Render the authenticated controls page."""
        valid, user = _check_auth(request)
        if not valid:
            return RedirectResponse("/login", status_code=303)
        csrf = generate_csrf_token(request.session)
        response = templates.TemplateResponse(
            request=request,
            name="controls.html",
            context={"username": user, "csrf_token": csrf},
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    # -- /settings GET --------------------------------------------------

    @router.get("/settings")
    async def settings_get(request: Request) -> Any:
        """Render the authenticated settings page."""
        valid, user = _check_auth(request)
        if not valid:
            return RedirectResponse("/login", status_code=303)
        csrf = generate_csrf_token(request.session)
        response = templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={"username": user, "csrf_token": csrf},
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    # -- /settings/devices GET ------------------------------------------

    @router.get("/settings/devices")
    async def devices_editor_get(request: Request) -> Any:
        """Render the device editor page."""
        valid, user = _check_auth(request)
        if not valid:
            return RedirectResponse("/login", status_code=303)
        csrf = generate_csrf_token(request.session)
        response = templates.TemplateResponse(
            request=request,
            name="devices_editor.html",
            context={"username": user, "csrf_token": csrf},
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    # -- /api/devices GET -----------------------------------------------

    @router.get("/api/devices")
    async def api_devices_get(request: Request) -> Any:
        """Return only devices block from config — no credentials."""
        valid, _ = _check_auth(request)
        if not valid:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        try:
            import os
            config_path = os.environ.get("DEVICE_CONFIG_PATH", "devices.yaml")
            devices = DeviceInitializer.read_devices_config(config_path)
            return JSONResponse({"devices": devices})
        except Exception as exc:
            return JSONResponse({"detail": str(exc)}, status_code=500)

    # -- /api/devices POST ----------------------------------------------

    @router.post("/api/devices")
    async def api_devices_post(request: Request) -> Any:
        """Save devices block and hot-reload dev_mgr."""
        valid, _ = _check_auth(request)
        if not valid:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        # CSRF check
        body = await request.json()
        form_csrf = body.get("csrf_token", "")
        if not validate_csrf_token(request.session, form_csrf):
            return JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)

        devices = body.get("devices")
        if not isinstance(devices, list):
            return JSONResponse({"detail": "devices must be a list"}, status_code=400)

        try:
            import os
            config_path = os.environ.get("DEVICE_CONFIG_PATH", "devices.yaml")
            DeviceInitializer.write_devices_config(config_path, devices)
            # Hot-reload
            initializer = DeviceInitializer(config_path)
            result = initializer.reload()
            return JSONResponse({"ok": True, "added": result["added"], "errors": result["errors"]})
        except Exception as exc:
            return JSONResponse({"detail": str(exc)}, status_code=500)

    @router.post("/api/device/{device_id}/control")
    async def api_device_control(request: Request, device_id: str) -> Any:
        valid, _ = _check_auth(request)
        if not valid:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"detail": "Invalid JSON body"}, status_code=400)

        action = str(body.get("action", "")).strip().lower()
        mode = str(body.get("mode", "once")).strip().lower()
        duration_raw = body.get("duration_hours", 0)

        if action not in {"on", "off", "auto"}:
            return JSONResponse({"detail": "action must be on, off, or auto"}, status_code=400)
        if mode not in {"once", "timed", "force"}:
            return JSONResponse({"detail": "mode must be once, timed, or force"}, status_code=400)
        try:
            duration_hours = float(duration_raw or 0)
        except (TypeError, ValueError):
            return JSONResponse({"detail": "duration_hours must be numeric"}, status_code=400)
        if duration_hours < 0 or duration_hours > 24:
            return JSONResponse({"detail": "duration_hours must be between 0 and 24"}, status_code=400)

        dev_mgr = DeviceInitializer().device_manager
        dev = next((d for d in dev_mgr.get_devices() if str(getattr(d, "id", "")) == str(device_id)), None)
        if dev is None:
            return JSONResponse({"detail": "Device not found"}, status_code=404)

        override_key = f"device_override_{dev.id}"
        if action == "auto":
            if override_key in shared_state:
                del shared_state[override_key]
            return JSONResponse({
                "success": True,
                "device_name": dev.name,
                "action": "auto",
                "mode": "auto",
            })

        tuya_ctrl = shared_state.get("_tuya_ctrl")
        if tuya_ctrl is None:
            return JSONResponse({"detail": "Tuya controller unavailable"}, status_code=503)

        if action == "on":
            ok = await asyncio.to_thread(tuya_ctrl.switch_on_device, dev)
        else:
            ok = await asyncio.to_thread(tuya_ctrl.switch_off_device, dev)
        if not ok:
            return JSONResponse({"detail": "Device command failed"}, status_code=502)

        if mode == "once":
            if override_key in shared_state:
                del shared_state[override_key]
        else:
            expires_at = None
            if mode == "timed":
                expires_at = time.time() + duration_hours * 3600.0
            shared_state[override_key] = {
                "action": action,
                "mode": mode,
                "expires_at": expires_at,
            }

        return JSONResponse({
            "success": True,
            "device_name": dev.name,
            "action": action,
            "mode": mode,
        })

    @router.get("/api/device/{device_id}/override")
    async def api_device_override(request: Request, device_id: str) -> Any:
        auth_ok, _ = _check_auth(request)
        if not auth_ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        override = shared_state.get(f"device_override_{device_id}")
        if not isinstance(override, dict):
            return JSONResponse({"mode": "auto"})

        expires_at = override.get("expires_at")
        if override.get("mode") == "timed" and isinstance(expires_at, (int, float)) and time.time() >= float(expires_at):
            del shared_state[f"device_override_{device_id}"]
            return JSONResponse({"mode": "auto"})

        return JSONResponse({
            "action": override.get("action"),
            "mode": override.get("mode"),
            "expires_at": override.get("expires_at"),
        })


    # -- /api/energy/daily GET ------------------------------------------
    @router.get("/api/energy/daily")
    async def api_energy_daily(request: Request, days: int = 7) -> Any:
        """Return daily energy totals for the last 7 days from SQLite."""
        import sqlite3, json as _json, os
        from pathlib import Path
        auth_ok, _ = _check_auth(request)
        if not auth_ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        try:
            _sqlite_candidates = [
                os.getenv("ML_SQLITE_PATH"),
                "/app/ml_data/data.sqlite",
                "ml_data/data.sqlite",
                "/srv/dessmonitor/ml/data.sqlite",
            ]
            db_path = next((p for p in _sqlite_candidates if p and Path(p).exists()), None)
            if db_path is None:
                return JSONResponse({"detail": "SQLite DB not found"}, status_code=503)
            con = sqlite3.connect(db_path)
            cur = con.cursor()
            cur.execute("""
                WITH intervals AS (
                    SELECT
                        substr(timestamp, 1, 10) as day,
                        CAST(json_extract(data_json, '$.energy_from_pv_wh') AS REAL) as pv_wh,
                        CAST(json_extract(data_json, '$.energy_from_battery_wh') AS REAL) as batt_wh,
                        CAST(json_extract(data_json, '$.energy_from_grid_wh') AS REAL) as grid_wh,
                        CAST(json_extract(data_json, '$.energy_to_battery_wh') AS REAL) as chg_wh,
                        CAST(json_extract(data_json, '$.total_load_watt') AS REAL) as load_w,
                        CAST(json_extract(data_json, '$.unix_ts') AS INTEGER) as ts,
                        LAG(CAST(json_extract(data_json, '$.unix_ts') AS INTEGER))
                            OVER (ORDER BY unix_ts) as prev_ts
                    FROM ml_points
                    WHERE timestamp >= date('now', '-' || ? || ' days')
                )
                SELECT
                    day,
                    ROUND(SUM(pv_wh), 1),
                    ROUND(SUM(batt_wh) - SUM(chg_wh), 1),
                    ROUND(SUM(grid_wh), 1),
                    ROUND(SUM(chg_wh), 1),
                    ROUND(SUM(
                        CASE
                            WHEN prev_ts IS NOT NULL AND (ts - prev_ts) BETWEEN 60 AND 7200
                            THEN load_w * (ts - prev_ts) / 3600.0
                            ELSE load_w * 5.0 / 60.0
                        END
                    ), 1)
                FROM intervals
                GROUP BY day
                ORDER BY day
            """, (days,))
            rows = []
            for day, pv, batt, grid, chg, load in cur.fetchall():
                pv_val = pv or 0
                batt_val = batt or 0
                rows.append({
                    "day": day,
                    "pv_wh": pv_val,
                    "battery_wh": batt_val,
                    "solar_total_wh": round(pv_val + batt_val, 1),
                    "grid_wh": grid or 0,
                    "charge_wh": chg or 0,
                    "load_wh": load or 0,
                })
            con.close()
            # today summary
            today = rows[-1] if rows else {}
            return JSONResponse({
                "today": today,
                "week": rows,
            })
        except Exception as exc:
            return JSONResponse({"detail": str(exc)}, status_code=500)


    # -- /api/inverter/metrics GET ------------------------------------------
    @router.get("/api/inverter/metrics")
    async def api_inverter_metrics(request: Request) -> Any:
        """Return last 50 inverter_metrics rows from TimescaleDB."""
        import os
        auth_ok, _ = _check_auth(request)
        if not auth_ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        try:
            import asyncpg
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return JSONResponse({"detail": "DATABASE_URL not set"}, status_code=503)
            conn = await asyncpg.connect(db_url)
            rows = await conn.fetch("""
                SELECT time, working_mode, pv_power_w, battery_voltage,
                       battery_soc, battery_current_dis, output_power_w,
                       total_load_w, ac_output_load_pct
                FROM inverter_metrics
                ORDER BY time DESC
                LIMIT 50
            """)
            await conn.close()
            data = []
            for r in rows:
                data.append({
                    "time": r["time"].isoformat(),
                    "mode": r["working_mode"],
                    "pv_w": r["pv_power_w"],
                    "batt_v": r["battery_voltage"],
                    "batt_soc": r["battery_soc"],
                    "batt_dis": r["battery_current_dis"],
                    "output_w": r["output_power_w"],
                    "load_w": r["total_load_w"],
                    "load_pct": r["ac_output_load_pct"],
                })
            return JSONResponse({"rows": data})
        except Exception as exc:
            return JSONResponse({"detail": str(exc)}, status_code=500)


    # -- /api/overload/alert GET ----------------------------------------
    @router.get("/api/overload/alert")
    async def api_overload_alert(request: Request) -> Any:
        """Return current overload alert level from shared_state."""
        from shared_state.shared_state import shared_state as _ss
        auth_ok, _ = _check_auth(request)
        if not auth_ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        alert = _ss.get("overload_alert") or {"level": "ok"}
        return JSONResponse(alert)


    # -- /api/energy/devices/today GET ------------------------------------
    @router.get("/api/energy/devices/today")
    async def api_energy_devices_today(request: Request) -> Any:
        """Return per-device energy consumed today from TimescaleDB device_metrics."""
        import os
        auth_ok, _ = _check_auth(request)
        if not auth_ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        try:
            import asyncpg
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return JSONResponse({"detail": "DATABASE_URL not set"}, status_code=503)
            conn = await asyncpg.connect(db_url)
            rows = await conn.fetch("""
                WITH power_stats AS (
                    SELECT
                        device_name,
                        COUNT(*) FILTER (WHERE is_on = true) as on_count,
                        ROUND((COUNT(*) FILTER (WHERE is_on = true) * 2.0 / 60.0)::numeric, 2) as on_hours,
                        ROUND(AVG(power_watts) FILTER (WHERE is_on = true AND power_watts IS NOT NULL)::numeric, 1) as avg_power_w,
                        ROUND((SUM(power_watts) FILTER (WHERE is_on = true AND power_watts IS NOT NULL) * 2.0 / 60.0)::numeric, 1) as real_wh
                    FROM device_metrics
                    WHERE time >= DATE_TRUNC('day', NOW())
                    GROUP BY device_name
                ),
                energy_counters AS (
                    SELECT
                        device_name,
                        ROUND((MAX(add_ele_kwh) - MIN(add_ele_kwh))::numeric, 3) as delta_kwh,
                        COUNT(*) as counter_records
                    FROM device_energy_counters
                    WHERE time >= DATE_TRUNC('day', NOW())
                    GROUP BY device_name
                )
                SELECT
                    p.device_name,
                    p.on_count,
                    p.on_hours,
                    p.avg_power_w,
                    p.real_wh,
                    e.delta_kwh,
                    e.counter_records > 0 as has_energy_meter
                FROM power_stats p
                LEFT JOIN energy_counters e USING (device_name)
                ORDER BY p.device_name
            """)
            await conn.close()
            data = []
            for r in rows:
                # Prefer add_ele delta from dedicated counter table (reliable across pod restarts)
                # Fallback to calculated real_wh from power_watts
                delta_kwh = float(r["delta_kwh"]) if r["delta_kwh"] else None
                real_wh = float(r["real_wh"]) if r["real_wh"] else None
                best_wh = (delta_kwh * 1000) if delta_kwh and delta_kwh > 0 else real_wh
                data.append({
                    "device_name": r["device_name"],
                    "on_hours": float(r["on_hours"] or 0),
                    "on_count": int(r["on_count"] or 0),
                    "avg_power_w": float(r["avg_power_w"]) if r["avg_power_w"] else None,
                    "real_wh": best_wh,
                    "has_energy_meter": int(r["has_energy_meter"] or 0) > 0,
                })
            return JSONResponse({"devices": data, "interval_minutes": 2})
        except Exception as exc:
            return JSONResponse({"detail": str(exc)}, status_code=500)


    # -- /api/overload/events GET ----------------------------------------
    @router.get("/api/overload/events")
    async def api_overload_events(request: Request) -> Any:
        """Return today overload events from important.log."""
        import os
        from datetime import date
        auth_ok, _ = _check_auth(request)
        if not auth_ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        try:
            log_path = os.environ.get("IMPORTANT_LOG_PATH", "/app/logs/important.log")
            today = date.today().isoformat()
            events = []
            with open(log_path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if today not in line or "OVERLOAD" not in line:
                        continue
                    line = line.strip()
                    # parse: 2026-08-05 12:35:41 [IMPORTANT] [OVERLOAD] ...
                    parts = line.split(" ", 3)
                    if len(parts) < 4:
                        continue
                    ts = parts[0] + " " + parts[1]
                    msg = parts[3].replace("[IMPORTANT] ", "").replace("[OVERLOAD] ", "")
                    events.append({"ts": ts, "msg": msg})
            events.reverse()  # newest first
            return JSONResponse({"events": events[:50], "total": len(events)})
        except Exception as exc:
            return JSONResponse({"detail": str(exc)}, status_code=500)


    # -- /api/energy/meter/daily GET ------------------------------------
    @router.get("/api/energy/meter/daily")
    async def api_energy_meter_daily(request: Request, days: int = 30) -> Any:
        """Return daily energy consumption from device_daily_energy aggregate."""
        import os
        auth_ok, _ = _check_auth(request)
        if not auth_ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        try:
            import asyncpg
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return JSONResponse({"detail": "DATABASE_URL not set"}, status_code=503)
            conn = await asyncpg.connect(db_url)
            rows = await conn.fetch("""
                SELECT
                    day::date as day,
                    device_name,
                    ROUND(consumed_kwh::numeric, 3) as consumed_kwh,
                    ROUND(end_kwh::numeric, 3) as end_kwh,
                    samples
                FROM device_daily_energy
                WHERE day >= NOW() - ($1 || ' days')::interval
                ORDER BY day DESC, device_name
            """, str(days))
            await conn.close()
            data = []
            for r in rows:
                data.append({
                    "day": str(r["day"]),
                    "device_name": r["device_name"],
                    "consumed_kwh": float(r["consumed_kwh"] or 0),
                    "end_kwh": float(r["end_kwh"] or 0),
                    "samples": int(r["samples"] or 0),
                })
            return JSONResponse({"days": data})
        except Exception as exc:
            return JSONResponse({"detail": str(exc)}, status_code=500)


    # -- /api/charts/inverter GET ------------------------------------------
    @router.get("/api/charts/inverter")
    async def api_charts_inverter(request: Request, hours: int = 24) -> Any:
        """Return inverter_metrics series for the last N hours from TimescaleDB."""
        import os
        auth_ok, _ = _check_auth(request)
        if not auth_ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        try:
            import asyncpg
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return JSONResponse({"detail": "DATABASE_URL not set"}, status_code=503)
            conn = await asyncpg.connect(db_url)
            rows = await conn.fetch("""
                SELECT time, pv_power_w, battery_voltage, output_power_w
                FROM inverter_metrics
                WHERE time > NOW() - ($1 || ' hours')::interval
                ORDER BY time
            """, str(hours))
            await conn.close()
            data = []
            for r in rows:
                data.append({
                    "time": r["time"].isoformat(),
                    "pv_power_w": r["pv_power_w"],
                    "battery_voltage": r["battery_voltage"],
                    "output_power_w": r["output_power_w"],
                })
            return JSONResponse({"data": data})
        except Exception as exc:
            return JSONResponse({"detail": str(exc)}, status_code=500)


    # -- /api/charts/hourly-load GET ---------------------------------------
    @router.get("/api/charts/hourly-load")
    async def api_charts_hourly_load(request: Request, hours: int = 24) -> Any:
        """Return hourly average load profile from device_metrics (TimescaleDB)."""
        import os
        auth_ok, _ = _check_auth(request)
        if not auth_ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        try:
            import asyncpg
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return JSONResponse({"detail": "DATABASE_URL not set"}, status_code=503)
            conn = await asyncpg.connect(db_url)
            rows = await conn.fetch("""
                SELECT time_bucket('1 hour', time) as hour,
                       ROUND(AVG(power_watts)::numeric, 1) as avg_power_w,
                       COUNT(DISTINCT device_name) as device_count
                FROM device_metrics
                WHERE time > NOW() - ($1 || ' hours')::interval
                  AND power_watts IS NOT NULL
                GROUP BY hour ORDER BY hour
            """, str(hours))
            await conn.close()
            data = []
            for r in rows:
                data.append({
                    "hour": r["hour"].isoformat(),
                    "avg_power_w": float(r["avg_power_w"]) if r["avg_power_w"] is not None else None,
                    "device_count": int(r["device_count"] or 0),
                })
            return JSONResponse({"data": data})
        except Exception as exc:
            return JSONResponse({"detail": str(exc)}, status_code=500)


    # -- /api/thresholds GET -----------------------------------------------
    @router.get("/api/thresholds")
    async def api_thresholds(request: Request) -> Any:
        """Return per-device voltage thresholds with solar-adjusted values."""
        auth_ok, _ = _check_auth(request)
        if not auth_ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        try:
            from app.device_initializer import DeviceInitializer
            from app.devices.relay_channel_device import classify_projection_kind, DeviceProjectionKind
            from shared_state.shared_state import shared_state
            HARD_FLOOR = 25.0
            dev_mgr = DeviceInitializer().device_manager
            battery_v = shared_state.get("battery_voltage")
            clouds = shared_state.get("forecast_clouds_pct")
            sunrise = shared_state.get("sunrise_hour") or 6
            sunset = shared_state.get("sunset_hour") or 20
            from datetime import datetime
            now_h = datetime.now().hour
            solar_period = (sunrise <= now_h < max(sunrise, sunset - 3)) and clouds is not None and float(clouds) < 50
            devices = []
            for dev in dev_mgr.get_devices():
                proj = classify_projection_kind(dev.device_type)
                if proj != DeviceProjectionKind.LOAD:
                    continue
                if not getattr(dev, "enabled", True):
                    continue
                min_v = getattr(dev, "min_volt", None)
                max_v = getattr(dev, "max_volt", None)
                coef  = getattr(dev, "coefficient", 0.0) or 0.0
                if min_v is None or max_v is None:
                    continue
                solar_min = max(HARD_FLOOR, float(min_v) - float(coef))
                solar_max = max(HARD_FLOOR, float(max_v) - float(coef))  # also lower ON threshold
                devices.append({
                    "name": dev.name,
                    "desc": getattr(dev, "desc", "") or "",
                    "min_volt": float(min_v),
                    "max_volt": float(max_v),
                    "coefficient": float(coef),
                    "solar_min_volt": solar_min,
                    "solar_max_volt": solar_max,
                    "priority": getattr(dev, "priority", 0),
                    "is_on": dev.is_device_on(),
                })
            devices.sort(key=lambda d: d["max_volt"], reverse=True)
            return JSONResponse({
                "devices": devices,
                "battery_voltage": battery_v,
                "solar_period": solar_period,
                "clouds_pct": clouds,
                "sunrise": sunrise,
                "sunset": sunset,
            })
        except Exception as exc:
            return JSONResponse({"detail": str(exc)}, status_code=500)


    # -- /api/charts/weather GET ------------------------------------------
    @router.get("/api/charts/weather")
    async def api_charts_weather(request: Request, hours: int = 24) -> Any:
        """Return temperature and humidity from weather_data."""
        import os
        auth_ok, _ = _check_auth(request)
        if not auth_ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        try:
            import asyncpg
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return JSONResponse({"detail": "DATABASE_URL not set"}, status_code=503)
            conn = await asyncpg.connect(db_url)
            rows = await conn.fetch("""
                SELECT time, ambient_temp, humidity
                FROM weather_data
                WHERE time > NOW() - ($1 || ' hours')::interval
                ORDER BY time
            """, str(hours))
            await conn.close()
            data = [{"time": r["time"].isoformat(),
                     "temp": float(r["ambient_temp"]) if r["ambient_temp"] else None,
                     "humidity": float(r["humidity"]) if r["humidity"] else None}
                    for r in rows]
            return JSONResponse({"data": data})
        except Exception as exc:
            return JSONResponse({"detail": str(exc)}, status_code=500)

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_auth(request: Request) -> tuple[bool, str | None]:
    """Check if the current request has a valid authenticated session.

    Args:
        request: The incoming request.

    Returns:
        A ``(valid, username_or_None)`` tuple.
    """
    session = getattr(request, "session", None)
    if session is None:
        return False, None
    return validate_session(session)


def _login_error(request: Request, message: str) -> Any:
    """Render the login form with a generic error message.

    The error message is deliberately generic — no indication of whether
    the username, password, or throttling was the cause.
    """
    csrf = generate_csrf_token(request.session)
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"csrf_token": csrf, "error": message},
    )
    response.headers["Cache-Control"] = "no-store"
    return response
