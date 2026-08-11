import logging
import os
# dessmonitor/api.py
import socket
import time
import hashlib
import urllib.request
import urllib.error
import urllib.parse
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Union, Iterable

from app.logger import loki_handler
from shared_state.shared_state import shared_state

_TOKEN_FILE = Path(
    os.getenv("MONITOR_TOKEN_PATH", "app/cache/dess_token.json")
)

# ──────────────────────────────────────────────────────────────────────────────
# Модель данных
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class DeviceData:
    timestamp: Optional[str] = None
    working_state: Optional[str] = None
    battery_voltage: Optional[float] = None
    battery_capacity: Optional[float] = None
    battery_charging_current: Optional[float] = None
    battery_discharging_current: Optional[float] = None
    pv1_voltage: Optional[float] = None
    pv1_power: Optional[float] = None
    pv2_voltage: Optional[float] = None
    pv2_power: Optional[float] = None
    pv_total_power: Optional[float] = None
    output_voltage: Optional[float] = None
    output_power: Optional[float] = None            # Active power
    output_apparent_power: Optional[float] = None   # ⬅️ NEW (для WEB)
    ac_input_voltage: Optional[float] = None
    ac_input_frequency: Optional[float] = None
    ac_output_load: Optional[float] = None
    battery_status: Optional[str] = None
    pv_status: Optional[str] = None
    mains_status: Optional[str] = None
    load_status: Optional[str] = None
    charger_priority: Optional[str] = None
    output_priority: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)  # type: ignore[arg-type]

    def summary(self) -> str:
        """Красивое многострочное резюме для inverter.log."""
        mode_icons = {
            "Line Mode": "⚡ Сеть",
            "Battery Mode": "🔋 Батарея",
            "PV Mode": "☀️  Солнечные",
            "Power Saving Mode": "💤 Энергосбер.",
            "Standby Mode": "⏸️  Ожидание",
            "Bypass Mode": "↪️  Bypass",
            "Fault Mode": "❌ Ошибка",
            "Invert Mode": "🔋 Инвертор",
        }
        mode_txt = self.working_state or "—"
        mode_icon = mode_icons.get(mode_txt, f"ℹ️ {mode_txt}")

        # если нет активной мощности — подставим кажущуюся
        out_power = self.output_power if self.output_power is not None else self.output_apparent_power
        # Получаем температуру воды из shared_state
        temp_raw = shared_state.get("temp_current")
        water_temp = shared_state.get("water_temp") or shared_state.get("pondtemp")
        # ── helper ──────────────────────────────────────────────
        def fmt(val, unit: str = "", width: int = 5, prec: int = 1):
            return f"{val:>{width}.{prec}f}{unit}" if val is not None else ""

        # ── строки лога ─────────────────────────────────────────
        raw_lines = [
            "",
            f"┌─ {self.timestamp} ───────────────────────────────────",
            f"│ Режим           : {mode_icon}",
            f"│ Battery         : {fmt(self.battery_voltage, ' V')}  "
            f"| {fmt(self.battery_capacity, ' %', width=3, prec=0)}",
            f"│   Charge curr.  : {fmt(self.battery_charging_current, ' A')}",
            f"│   Disch. curr.  : {fmt(self.battery_discharging_current, ' A')}",
            f"│ PV-1            : {fmt(self.pv1_voltage, ' V')}  "
            f"| {fmt(self.pv1_power, ' W', prec=0)}",
            f"│ PV-2            : {fmt(self.pv2_voltage, ' V')}  "
            f"| {fmt(self.pv2_power, ' W', prec=0)}",
            f"│ AC-in           : {fmt(self.ac_input_voltage, ' V')}",
            f"│ Water temp      : {fmt(water_temp, '°C')}  ",
            f"│ Load            : {fmt(self.ac_output_load, ' %', width=3, prec=0)}",
            f"│ Output power    : {fmt(out_power, ' W', prec=0)}  ",
            "└───────────────────────────────────────────────────────",
            "\n "
        ]
        # — удаляем строки, где после «: » ничего не осталось —
        lines = []
        for ln in raw_lines:
            if ln.strip() == "":  # явно пустая
                lines.append(ln)
                continue
            if ln.endswith(":") or ln.rstrip().endswith(":"):  # поле None → пропуск
                continue
            lines.append(ln)
        return "\n".join(lines)


class TokenExpiredError(Exception):
    pass


class DessAPIError(RuntimeError):
    def __init__(self, message: str, *, err: int | None = None, desc: str | None = None,
                 action: str | None = None, transport: str = "api"):
        super().__init__(message)
        self.err = err
        self.desc = desc or message
        self.action = action
        self.transport = transport


class DessAuthError(DessAPIError):
    pass


class DessTimeoutError(DessAPIError):
    pass


class DessSignError(DessAPIError):
    pass


class DessAPI:
    API_BASE = "https://api.dessmonitor.com/public/"  # https
    WEB_FALLBACK_BASE = "https://web.dessmonitor.com/public/"
    REQUEST_TIMEOUT = 20
    APP_ID = "com.demo.test"
    APP_VERSION = "3.6.2.1"
    APP_CLIENT = "android"
    ERR_TIMEOUT = 0x0002
    ERR_SIGN = 0x0004
    ERR_SALT = 0x0005
    ERR_NO_AUTH = 0x000A

    TITLE_MAPPING = {
        # общие
        "Timestamp": "timestamp",
        "时间戳": "timestamp",

        "Working State": "working_state",

        "Battery Voltage": "battery_voltage",
        "电池电压": "battery_voltage",
        "Battery Capacity": "battery_capacity",
        "Battery Charging Current": "battery_charging_current",
        "Battery Discharge Current": "battery_discharging_current",

        "PV1 Input Voltage": "pv1_voltage",
        "PV1 Input Power": "pv1_power",
        "PV2 input voltage": "pv2_voltage",
        "PV2 input power": "pv2_power",
        "PV total Power": "pv_total_power",
        "PV Total Power": "pv_total_power",  # иногда другой регистр

        "Output Voltage": "output_voltage",
        "Output Active Power": "output_power",
        "Output Apparent Power": "output_apparent_power",  # ⬅️ NEW

        "AC Input Voltage": "ac_input_voltage",
        "AC Input Frequency": "ac_input_frequency",
        "AC Output Load": "ac_output_load",

        "Battery Status": "battery_status",
        "PV Status": "pv_status",
        "Mains Status": "mains_status",
        "Load Status": "load_status",
        "Charger Source Priority": "charger_priority",
        "Output Source Priority": "output_priority",
    }

    def __init__(self, config, logger):
        self.email = config.email
        self.password = config.password
        self.company_key = config.company_key
        self.pn = config.pn
        self.dev_code = config.dev_code
        self.dev_addr = config.dev_addr
        self.sn = config.sn
        self.logger = logger
        self.token: Optional[str] = None
        self.secret: Optional[str] = None
        self.token_expiry: Optional[float] = None
        self.token_acquired_time: Optional[float] = None
        self._load_cached_token()  # ← попробуем
        # Auth deferred to first use — authenticate() called from run.py via asyncio.to_thread

        lh = loki_handler()
        if lh not in self.logger.handlers:
            self.logger.addHandler(lh)

        self.imp = logging.getLogger("IMPORTANT")

    # ──────────────────────────────────────────────────────────
    # Подпись
    # ──────────────────────────────────────────────────────────
    def _sha1_hex(self, raw: Union[str, bytes]) -> str:
        """Безопасно для type-checker: приводим к bytes."""
        raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
        return hashlib.sha1(raw_bytes).hexdigest()

    def _generate_sign(self, param_str: str, use_password: bool = False):
        if not param_str.startswith("&action="):
            raise ValueError("Signed action string must start with '&action='")

        salt = str(int(time.time() * 1000))
        if use_password:
            pwd_hash = self._sha1_hex(self.password)
            raw = f"{salt}{pwd_hash}{param_str}"
        else:
            # self.secret/self.token точно строки к этому моменту
            raw = f"{salt}{self.secret}{self.token}{param_str}"
        return self._sha1_hex(raw), salt

    @staticmethod
    def _redact_url(url: str) -> str:
        """Return a diagnostics-safe representation of a signed DESS URL.

        The scheme, host, path, action, and i18n parameters remain visible.
        All credential, device-identifying, and app-identifying parameters
        are replaced with "REDACTED".

        The actual URL passed to urllib.request.urlopen is never changed.
        """
        REDACTED = "REDACTED"
        SENSITIVE_QUERY_PARAMS = frozenset({
            "sign", "salt", "token", "usr", "company-key",
            "pn", "sn", "devcode", "devaddr",
            "_app_client_", "_app_id_", "_app_version_", "source",
        })
        try:
            parsed = urllib.parse.urlparse(url)
            query_params = urllib.parse.parse_qs(
                parsed.query, keep_blank_values=True
            )
            safe_params = {}
            for key, values in query_params.items():
                if key in SENSITIVE_QUERY_PARAMS:
                    safe_params[key] = REDACTED
                else:
                    safe_params[key] = values[0] if values else ""
            safe_query = urllib.parse.urlencode(safe_params, doseq=False)
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{safe_query}"
        except Exception:
            return "[REDACTED URL]"

    @staticmethod
    def _coerce_float(value: object) -> float | None:
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    def _build_action_pairs(self, params: dict) -> list[tuple[str, str]]:
        if "action" not in params:
            raise ValueError("Missing required 'action' parameter")
        action_value = str(params["action"])
        pairs: list[tuple[str, str]] = [("action", action_value)]
        for key, value in params.items():
            if key == "action":
                continue
            pairs.append((str(key), str(value)))
        pairs.extend([
            ("source", "1"),
            ("_app_client_", self.APP_CLIENT),
            ("_app_id_", self.APP_ID),
            ("_app_version_", self.APP_VERSION),
        ])
        return pairs

    @staticmethod
    def _serialize_action_pairs(pairs: Iterable[tuple[str, str]]) -> str:
        encoded = "&".join(
            f"{urllib.parse.quote_plus(key)}={urllib.parse.quote_plus(value)}"
            for key, value in pairs
        )
        action_str = f"&{encoded}"
        if not action_str.startswith("&action="):
            raise ValueError("Serialized action string must start with '&action='")
        return action_str

    @staticmethod
    def _is_token_auth_failure(desc: str | None) -> bool:
        if not desc:
            return False
        desc_l = desc.lower()
        markers = (
            "invalid token",
            "token invalid",
            "token expired",
            "expired token",
            "token error",
            "no auth",
            "not auth",
            "auth fail",
        )
        return any(marker in desc_l for marker in markers)

    def _raise_api_error(self, *, err: int, desc: str, action: str, transport: str) -> None:
        msg = f"[{transport.upper()}] action={action} err={err} desc={desc}"
        if err == self.ERR_NO_AUTH or self._is_token_auth_failure(desc):
            raise DessAuthError(msg, err=err, desc=desc, action=action, transport=transport)
        if err == self.ERR_TIMEOUT:
            raise DessTimeoutError(msg, err=err, desc=desc, action=action, transport=transport)
        if err == self.ERR_SIGN:
            raise DessSignError(msg, err=err, desc=desc, action=action, transport=transport)
        raise DessAPIError(msg, err=err, desc=desc, action=action, transport=transport)

    # ──────────────────────────────────────────────────────────
    # HTTP запрос
    # ──────────────────────────────────────────────────────────
    def _do_api_request(
        self,
        params: dict,
        *,
        need_auth: bool = True,
        base_url: str | None = None,
        transport: str = "api",
    ) -> dict:
        action = str(params.get("action", ""))
        action_str = self._serialize_action_pairs(self._build_action_pairs(params.copy()))
        sign, salt = self._generate_sign(action_str, use_password=not need_auth)
        if need_auth and (not self.token or not self.secret):
            raise DessAuthError(
                f"[{transport.upper()}] missing token/secret for action={action}",
                action=action,
                transport=transport,
            )
        url = (
            f"{(base_url or self.API_BASE)}?sign={sign}&salt={salt}"
            f"{f'&token={self.token}' if need_auth else ''}"
            f"{action_str}"
        )

        self.logger.info("[%s] Request: %s", transport.upper(), self._redact_url(url))

        try:
            with urllib.request.urlopen(url, timeout=self.REQUEST_TIMEOUT) as resp:
                raw_data = resp.read()
            data = json.loads(raw_data.decode("utf-8"))
            if data.get("err") != 0:
                desc = data.get("desc", "Unknown error")
                self._raise_api_error(
                    err=int(data.get("err")),
                    desc=str(desc),
                    action=action,
                    transport=transport,
                )
            return data
        except (DessAPIError, ValueError):
            raise
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError) or isinstance(reason, socket.timeout):
                raise DessTimeoutError(
                    f"[{transport.upper()}] action={action} transport timeout",
                    action=action,
                    transport=transport,
                ) from exc
            self.logger.error(
                "[%s] request error: %s",
                transport.upper(),
                exc,
                extra={"type": "dess_api", "evt": "error"},
            )
            raise DessAPIError(
                f"[{transport.upper()}] action={action} request failed: {exc}",
                action=action,
                transport=transport,
            ) from exc
        except TimeoutError as exc:
            raise DessTimeoutError(
                f"[{transport.upper()}] action={action} transport timeout",
                action=action,
                transport=transport,
            ) from exc
        except socket.timeout as exc:
            raise DessTimeoutError(
                f"[{transport.upper()}] action={action} transport timeout",
                action=action,
                transport=transport,
            ) from exc
        except json.JSONDecodeError as exc:
            raise DessAPIError(
                f"[{transport.upper()}] action={action} invalid JSON response",
                action=action,
                transport=transport,
            ) from exc
        except Exception as e:
            self.logger.error("[API] request error: %s", e,
                              extra={"type": "dess_api", "evt": "error"})
            raise DessAPIError(
                f"[{transport.upper()}] action={action} request failed: {e}",
                action=action,
                transport=transport,
            ) from e

    def _query_device_last_data_once(self) -> DeviceData:
        params = {
            "action": "queryDeviceLastData",
            "i18n": "en_US",
            "pn": self.pn,
            "devcode": self.dev_code,
            "devaddr": self.dev_addr,
            "sn": self.sn,
        }
        result = self._do_api_request(params, need_auth=True, transport="api")
        dd = self._parse_device_data(result)
        self.logger.info("[DESS] official queryDeviceLastData success")
        return dd

    def _ensure_authenticated(self) -> None:
        if not self.token or not self.secret:
            self.authenticate()
        if self.should_refresh_token():
            try:
                self.refresh_token()
            except DessAuthError:
                self.logger.info("[DESS] updateToken not authorised; performing full auth")
                self.authenticate()
            except DessAPIError as exc:
                self.logger.info("[DESS] updateToken failed (%s); performing full auth", exc.desc)
                self.authenticate()

    # ──────────────────────────────────────────────────────────
    # Аутентификация
    # ──────────────────────────────────────────────────────────
    def authenticate(self) -> None:
        self.logger.info("[DESS] authSource start")
        params = {"action": "authSource", "usr": self.email, "company-key": self.company_key}
        result = self._do_api_request(params, need_auth=False, transport="api")
        dat = result.get("dat", {})
        self.token = dat.get("token")
        self.secret = dat.get("secret")
        self.token_expiry = self._coerce_float(dat.get("expire"))
        self.token_acquired_time = time.time()
        if not self.token or not self.secret:
            raise RuntimeError("Не получены token/secret от authSource.")
        self.logger.info("[DESS] authSource success")
        self._save_token()

    def refresh_token(self) -> None:
        self.logger.info("[DESS] updateToken start")
        params = {"action": "updateToken"}
        result = self._do_api_request(params, need_auth=True, transport="api")
        dat = result.get("dat", {})
        self.token = dat.get("token") or self.token
        self.secret = dat.get("secret") or self.secret
        refreshed_expire = self._coerce_float(dat.get("expire"))
        self.token_expiry = refreshed_expire or self.token_expiry
        self.token_acquired_time = time.time()
        self.logger.info("[DESS] updateToken success")
        self._save_token()

    def should_refresh_token(self) -> bool:
        if self.token_expiry is None or self.token_acquired_time is None:
            return True
        return (time.time() - self.token_acquired_time) >= 0.9 * float(self.token_expiry)

    # ──────────────────────────────────────────────────────────
    # Публичные методы
    # ──────────────────────────────────────────────────────────
    def fetch_device_data(self) -> DeviceData:
        try:
            self._ensure_authenticated()
            try:
                dd = self._query_device_last_data_once()
            except DessAuthError as auth_exc:
                self.logger.warning(
                    "[DESS] official API auth rejected err=%s desc=%s; re-authenticating once",
                    auth_exc.err,
                    auth_exc.desc,
                )
                self.authenticate()
                dd = self._query_device_last_data_once()
            return dd

        except Exception as main_exc:
            if not self.token or not self.secret:
                self.logger.error("[DESS] official API failed and no token/secret available for web fallback")
                raise
            err = getattr(main_exc, "err", None)
            desc = getattr(main_exc, "desc", str(main_exc))
            self.logger.warning(
                "[DESS] official API failed err=%s desc=%s; using web fallback",
                err,
                desc,
            )
            return self.fetch_device_data_fallback()

    def fetch_device_data_fallback(self) -> DeviceData:
        """
        Резервный вариант: web-кролл querySPDeviceLastData
        """
        try:
            payload = self._do_api_request(
                {
                    "action": "querySPDeviceLastData",
                    "pn": self.pn,
                    "devcode": self.dev_code,
                    "devaddr": self.dev_addr,
                    "sn": self.sn,
                    "i18n": "en_US",
                },
                need_auth=True,
                base_url=self.WEB_FALLBACK_BASE,
                transport="web",
            )
        except DessAPIError as exc:
            self.logger.error("[WEB] fallback request failed err=%s desc=%s", exc.err, exc.desc)
            raise RuntimeError(f"Веб-кролл не удался: {exc.desc}") from exc

        dat = payload.get("dat", {})
        dd = DeviceData(timestamp=dat.get("gts"))

        # человекочитаемое время из gts (мс)
        gts = dat.get("gts")
        if gts:
            try:
                # gts приходит в миллисекундах
                timestamp_sec = int(gts) // 1000
                ts = time.localtime(timestamp_sec)
                dd.timestamp = time.strftime("%Y-%m-%d %H:%M:%S", ts)

                # Логируем расхождение timezone если есть
                from datetime import datetime, timezone
                utc_time = datetime.fromtimestamp(timestamp_sec, tz=timezone.utc)
                local_time = datetime.fromtimestamp(timestamp_sec)
                if utc_time.hour != local_time.hour:
                    self.logger.debug(
                        f"[TIME] Timezone offset detected: "
                        f"UTC={utc_time.strftime('%Y-%m-%d %H:%M:%S')}, "
                        f"Local={local_time.strftime('%Y-%m-%d %H:%M:%S')} "
                        f"(offset: {(local_time.hour - utc_time.hour) % 24}h)"
                    )
            except Exception as e:
                self.logger.warning(f"[TIME] Failed to parse timestamp: {e}")
                pass

        # pars — словарь массивов: gd_, sy_, pv_, bt_, bc_
        parsed_fields = []  # ⬅️ Для отладки
        for section_name, section in dat.get("pars", {}).items():
            for item in section:
                title = item.get("par")
                val = item.get("val")
                field = self.TITLE_MAPPING.get(title)
                if not field:
                    continue

                if field in [
                    "timestamp", "working_state", "battery_status",
                    "pv_status", "mains_status", "load_status",
                    "charger_priority", "output_priority"
                ]:
                    setattr(dd, field, val)
                    parsed_fields.append(f"{field}={val}")
                else:
                    try:
                        float_val = float(val)
                        setattr(dd, field, float_val)
                        parsed_fields.append(f"{field}={float_val}")
                    except Exception as e:
                        self.logger.warning(f"[WEB] Не удалось преобразовать {title}={val} в float: {e}")
                        setattr(dd, field, None)

        self.logger.info(f"[WEB] Успешно спарсили {len(parsed_fields)} полей: {', '.join(parsed_fields[:5])}...")
        return dd

    # ──────────────────────────────────────────────────────────
    # Парсер ответа основного API
    # ──────────────────────────────────────────────────────────
    def _parse_device_data(self, data: dict) -> DeviceData:
        dd = DeviceData()
        for item in data.get("dat", []):
            title = item.get("title", "").strip()
            val = item.get("val", "").strip()
            field = self.TITLE_MAPPING.get(title)
            if field:
                if field in ["timestamp", "working_state", "battery_status",
                             "pv_status", "mains_status", "load_status",
                             "charger_priority", "output_priority"]:
                    setattr(dd, field, val)
                else:
                    try:
                        setattr(dd, field, float(val))
                    except ValueError:
                        setattr(dd, field, None)
        return dd

    # ──────────────────────────────────────────────────────────
    # Кеш токена
    # ──────────────────────────────────────────────────────────
    def _load_cached_token(self):
        if not _TOKEN_FILE.exists():
            return
        try:
            data = json.loads(_TOKEN_FILE.read_text())
            acquired_at = self._coerce_float(data.get("acquired_at"))
            expires_in = self._coerce_float(data.get("expires_in"))
            # небольшая проверка «жив ли» (10% буфер)
            if acquired_at is not None and expires_in is not None and time.time() - acquired_at < 0.9 * expires_in:
                self.token = data["token"]
                self.secret = data["secret"]
                self.token_expiry = expires_in
                self.token_acquired_time = acquired_at
                self.logger.info("[API] Восстановили token из кеша")
        except Exception as e:
            self.logger.error(f"[API] не смогли прочитать кеш token: {e}")

    def _save_token(self):
        try:
            _TOKEN_FILE.parent.mkdir(exist_ok=True)
            _TOKEN_FILE.write_text(json.dumps({
                "token": self.token,
                "secret": self.secret,
                "expires_in": self.token_expiry,
                "acquired_at": self.token_acquired_time
            }))
        except Exception as e:
            self.logger.error(f"[API] не смогли записать кеш token: {e}")
