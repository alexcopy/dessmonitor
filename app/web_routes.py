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
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from app import web_queries
from app.web_history_analysis import (OBSERVED_TELEMETRY_GAP_SECONDS, build_observed_output_transitions, _transition_summary, analyze_history)
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


_DECISION_EXPORT_FIELDS = frozenset((
    'evaluation_id', 'evaluated_at', 'algorithm_version', 'decision', 'threshold', 'proposed_voltage',
    'observation_time', 'observation_age_seconds', 'receipt_time', 'collection_time', 'mode', 'pv_w',
    'load_w', 'battery_v', 'charge_a', 'discharge_a', 'soc', 'soc_provenance',
    'recent_reported_mode_changes', 'transitions_provenance', 'short_cycles', 'sag_estimate_v',
    'rebound_estimate_v', 'window_seconds', 'collection_count', 'confidence', 'reason_codes',
    'safety_blockers', 'explanation', 'hardware_write_attempted',
    'time_provenance', 'evidence_level', 'exploratory_voltage_contrast', 'verified_sag', 'stability', 'candidate_voltage',
    'actionability', 'confirmation_progress', 'cooldown_remaining_seconds', 'effective_window_seconds',
    'collection_window_seconds', 'unique_observation_count', 'worker_health',
    'confidence_breakdown', 'decision_diagnostics',
))


def _decision_export(entry):
    """Export only fields produced by the diagnostics, excluding configuration."""
    if not isinstance(entry, dict):
        raise ValueError('invalid journal entry')
    result = {key: value for key, value in entry.items() if key in _DECISION_EXPORT_FIELDS}
    for name in ('current_settings', 'historical_settings'):
        settings = entry.get(name)
        if isinstance(settings, dict):
            result[name] = {key: settings[key] for key in ('utility', 'battery', 'provenance') if key in settings}
    return result


def _adaptive_worker_enabled():
    return os.environ.get('ADAPTIVE_OUTPUT_LEARNING_ENABLED', 'false').lower() in ('true', '1', 'yes', 'on')


def _worker_health_from_journal(latest, *, enabled, unavailable_reason=None):
    """Expose persisted worker timestamps and derive staleness without inventing success."""
    stored = latest.get('worker_health') if isinstance(latest, dict) else None
    stored = stored if isinstance(stored, dict) else {}
    last_evaluation = stored.get('last_evaluation_at') or (latest.get('evaluated_at') if isinstance(latest, dict) else None)
    last_success = stored.get('last_successful_evaluation_at')
    last_error = stored.get('last_error')
    blocking = stored.get('blocking_reason')
    status_source = 'PERSISTED_JOURNAL' if stored else 'NOT_PERSISTED'
    persisted_status = stored.get('status')
    status = 'disabled' if not enabled else persisted_status if persisted_status in ('healthy', 'degraded') else 'degraded'
    if unavailable_reason:
        status = 'disabled' if not enabled else 'degraded'
        blocking = unavailable_reason if enabled else 'ADAPTIVE_OUTPUT_LEARNING_DISABLED'
        last_error = None if not enabled else last_error
        status_source = 'CURRENT_RUNTIME_CHECK'
    elif enabled:
        try:
            stamp = datetime.fromisoformat(last_evaluation.replace('Z', '+00:00')) if isinstance(last_evaluation, str) else None
            if stamp is None or stamp.tzinfo is None or (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds() > 180:
                status = 'degraded'
                blocking = blocking or 'WORKER_EVALUATION_STALE'
        except (TypeError, ValueError):
            status = 'degraded'
            blocking = blocking or 'WORKER_EVALUATION_TIME_INVALID'
        if not stored:
            status = 'degraded'
            blocking = blocking or 'NO_PERSISTED_WORKER_EVALUATION'
    return {
        'enabled': enabled,
        'status': status,
        'status_source': status_source,
        'last_evaluation_at': last_evaluation,
        'last_successful_evaluation_at': last_success,
        'last_error': last_error,
        'blocking_reason': blocking,
        'observation_count': stored.get('observation_count'),
        'collection_count': stored.get('collection_count'),
        'effective_window_seconds': stored.get('effective_window_seconds'),
        'evidence_level': latest.get('evidence_level') if isinstance(latest, dict) else None,
        'proposed_voltage': latest.get('proposed_voltage') if isinstance(latest, dict) else None,
        'confirmation_progress': latest.get('confirmation_progress') if isinstance(latest, dict) else None,
        'cooldown_remaining_seconds': latest.get('cooldown_remaining_seconds') if isinstance(latest, dict) else None,
        'confidence': latest.get('confidence') if isinstance(latest, dict) else None,
        'reason_codes': latest.get('reason_codes') if isinstance(latest, dict) else [],
    }


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
        csrf = generate_csrf_token(request.session)
        return templates.TemplateResponse(
            request=request,
            name="alarms.html",
            context={"username": username, "csrf_token": csrf},
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

    @router.get("/controls/decisions")
    async def adaptive_decisions_page(request: Request) -> Any:
        valid, user = _check_auth(request)
        if not valid:
            return RedirectResponse("/login", status_code=303)
        response = templates.TemplateResponse(request=request, name="adaptive_decisions.html",
            context={"username": user, "csrf_token": generate_csrf_token(request.session)})
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.get("/api/controls/decisions")
    async def adaptive_decisions_api(request: Request, days: int = 1) -> Any:
        valid, _ = _check_auth(request)
        if not valid:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        if days not in (1, 2, 7):
            return JSONResponse({"detail": "Invalid range"}, status_code=422)
        enabled = _adaptive_worker_enabled()
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            return JSONResponse({"decisions": [], "worker_health": _worker_health_from_journal(
                None, enabled=enabled, unavailable_reason='DATABASE_URL_UNAVAILABLE')},
                headers={"Cache-Control": "no-store"})
        try:
            rows = await web_queries.decision_journal(database_url, days)
            parsed = [json.loads(row['entry']) for row in rows]
            entries = [_decision_export(entry) for entry in parsed]
            latest = parsed[0] if parsed else None
            health = _worker_health_from_journal(latest, enabled=enabled,
                unavailable_reason=None if latest else 'NO_PERSISTED_WORKER_EVALUATION')
            return JSONResponse({"decisions": entries, "worker_health": health}, headers={"Cache-Control": "no-store"})
        except Exception:
            return JSONResponse({"decisions": [], "worker_health": _worker_health_from_journal(
                None, enabled=enabled, unavailable_reason='DECISION_JOURNAL_UNAVAILABLE')},
                headers={"Cache-Control": "no-store"})

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

    @router.get("/api/settings/charge-policy")
    async def api_charge_policy_get(request: Request) -> Any:
        valid, _ = _check_auth(request)
        if not valid:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        try:
            policy = await asyncio.to_thread(
                DeviceInitializer.read_charge_policy_config,
                DeviceInitializer._resolve_config_path(None),
            )
            return JSONResponse({"policy": policy, "status": shared_state.get("charge_policy_status", {})})
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        except OSError:
            return JSONResponse({"detail": "Unable to read charging policy"}, status_code=503)

    @router.post("/api/settings/charge-policy")
    async def api_charge_policy_post(request: Request) -> Any:
        valid, _ = _check_auth(request)
        if not valid:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"detail": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"detail": "Expected a JSON object"}, status_code=400)
        token = body.get("csrf_token")
        if not isinstance(token, str) or not validate_csrf_token(request.session, token):
            return JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)
        try:
            policy = await asyncio.to_thread(
                DeviceInitializer.write_charge_policy_config,
                DeviceInitializer._resolve_config_path(None), body.get("policy"),
            )
            return JSONResponse({"policy": policy, "status": shared_state.get("charge_policy_status", {})})
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        except OSError:
            return JSONResponse({"detail": "Unable to save charging policy; check configuration permissions"}, status_code=503)

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
        auth_ok, _ = _check_auth(request)
        if not auth_ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        try:
            try:
                records = await web_queries.sqlite_energy_daily(days)
            except FileNotFoundError:
                return JSONResponse({"detail": "SQLite DB not found"}, status_code=503)
            rows = []
            for day, pv, batt, grid, chg, load in records:
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
            # today summary
            from datetime import datetime, timezone
            today_day = datetime.now(timezone.utc).date().isoformat()
            today = next((row for row in rows if row["day"] == today_day), {
                "day": today_day, "charge_wh": 0, "grid_wh": 0,
                "pv_wh": 0, "battery_wh": 0, "load_wh": 0, "solar_total_wh": 0,
            })
            return JSONResponse({
                "today": today,
                "week": rows,
            })
        except Exception as exc:
            return JSONResponse({"detail": str(exc)}, status_code=500)


    # -- /api/energy/daily/balance GET ----------------------------------
    @router.get("/api/energy/daily/balance")
    async def api_energy_daily_balance(request: Request, days: int = 30) -> Any:
        """Return mode-split daily energy in kWh from TimescaleDB."""
        import os
        auth_ok, _ = _check_auth(request)
        if not auth_ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        if not 1 <= days <= 366:
            return JSONResponse({"detail": "days must be between 1 and 366"}, status_code=422)
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            return JSONResponse({"detail": "DATABASE_URL not set"}, status_code=503)
        try:
            rows = await web_queries.energy_daily_balance(db_url, days)
            data = [{
                "day": str(r["day"]),
                **{key: float(r[key] or 0) for key in (
                    "pv_kwh", "grid_kwh", "battery_load_kwh",
                    "total_load_kwh", "self_pct",
                )},
            } for r in rows]
            return JSONResponse({"days": data})
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
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return JSONResponse({"detail": "DATABASE_URL not set"}, status_code=503)
            rows = await web_queries.inverter_metrics(db_url)
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
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return JSONResponse({"detail": "DATABASE_URL not set"}, status_code=503)
            rows = await web_queries.energy_devices_today(db_url)
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
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return JSONResponse({"detail": "DATABASE_URL not set"}, status_code=503)
            rows = await web_queries.energy_meter_daily(db_url, str(days))
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
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return JSONResponse({"detail": "DATABASE_URL not set"}, status_code=503)
            rows = await web_queries.charts_inverter(db_url, str(hours))
            data = []
            for r in rows:
                data.append({
                    "time": r["time"].isoformat(),
                    "pv_power_w": r["pv_power_w"],
                    "battery_voltage": r["battery_voltage"],
                    "output_power_w": r["output_power_w"],
                    "working_mode": r["working_mode"],
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
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return JSONResponse({"detail": "DATABASE_URL not set"}, status_code=503)
            rows = await web_queries.charts_hourly_load(db_url, str(hours))
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
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return JSONResponse({"detail": "DATABASE_URL not set"}, status_code=503)
            rows = await web_queries.charts_weather(db_url, str(hours))
            data = [{"time": r["time"].isoformat(),
                     "temp": float(r["ambient_temp"]) if r["ambient_temp"] else None,
                     "humidity": float(r["humidity"]) if r["humidity"] else None}
                    for r in rows]
            return JSONResponse({"data": data})
        except Exception as exc:
            return JSONResponse({"detail": str(exc)}, status_code=500)


    # -- /api/charge/history GET -------------------------------------------
    @router.get("/api/charge/history")
    async def api_charge_history(request: Request, days: int = 1) -> Any:
        """Return raw inverter observations and observed output-mode transitions."""
        auth_ok, _ = _check_auth(request)
        if not auth_ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        if not 1 <= days <= 30:
            return JSONResponse({"detail": "days must be between 1 and 30"}, status_code=422)
        try:
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return JSONResponse({"detail": "DATABASE_URL not set"}, status_code=503)
            as_of = datetime.now(timezone.utc)
            window_start = as_of - timedelta(days=days)
            summary_start = as_of - timedelta(days=1)
            rows = await web_queries.charge_history(db_url, window_start, as_of)
            # Legacy name: this is the earliest collection time, not verified device time.
            verified_since = rows[0]['verified_since'] if rows else None
            rows = [row for row in rows if row['time'] is not None]
            observations, transitions, summary = analyze_history(rows, window_start, as_of, summary_start)
            for observation in observations:
                observation["time"] = observation["time"].isoformat()
            for transition in transitions:
                transition["observed_at"] = transition["observed_at"].isoformat()
            return JSONResponse({
                "verified_since": verified_since.isoformat() if verified_since is not None else None,
                "observations": observations,
                "transitions": transitions,
                "summary_24h": summary,
                "observed_gap_threshold_seconds": OBSERVED_TELEMETRY_GAP_SECONDS,
            })
        except Exception:
            return JSONResponse({"detail": "Unable to load inverter diagnostics"}, status_code=500)

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
