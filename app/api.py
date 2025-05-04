
# dessmonitor/api.py
import time
import hashlib
import urllib.request
import urllib.parse
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from shared_state.shared_state import shared_state

_TOKEN_FILE = Path("app/cache/dess_token.json")

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
    output_power: Optional[float] = None
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
        """Читаемое резюме для inverter.log ― компактно, но информативно."""
        mode_icons = {
            "Line Mode":        "⚡ Сеть",
            "Battery Mode":     "🔋 Батарея",
            "PV Mode":          "☀️  Солнечные",
            "Power Saving Mode":"💤 Энергосбер.",
            "Standby Mode":     "⏸️  Ожидание",
            "Bypass Mode":      "↪️  Bypass",
            "Fault Mode":       "❌ Ошибка",
            "Invert Mode":      "🔃 Инвертор",
        }
        mode_txt  = self.working_state or "—"
        mode_icon = mode_icons.get(mode_txt, f"ℹ️ {mode_txt}")

        # helpers ────────────────────────────────────────────────
        def fmt(v, unit="", width=5, prec=1):
            return f"{v:>{width}.{prec}f}{unit}" if v is not None else " " * (width+len(unit))

        # строки лога ────────────────────────────────────────────
        lines = ["\n",
            f"┌─ {self.timestamp} ───────────────────────────────────",
            f"│ Режим        : {mode_icon}",
            f"│ Battery      : {fmt(self.battery_voltage,' V')} | {fmt(self.battery_capacity,' %',width=3,prec=0)}",
            f"│ Charge curr. : {fmt(self.battery_charging_current,' A')}",
            f"│ DisChrg curr.: {fmt(self.battery_discharging_current,' A')}",
            f"│ PV1          : {fmt(self.pv1_voltage,' V')} | {fmt(self.pv1_power,' W',prec=0)}",
            f"│ PV2          : {fmt(self.pv2_voltage,' V')} | {fmt(self.pv2_power,' W',prec=0)}",
            f"│ AC‑in        : {fmt(self.ac_input_voltage,' V')}",
            f"│ Output load  : {fmt(self.output_power,' W',prec=0)} | {fmt(self.ac_output_load,' %',width=3,prec=0)}",
             "└───────────────────────────────────────────────────────",
        ]
        # убираем пустые строки (где всё None)
        neat = [ln for ln in lines if not ln.rstrip().endswith("│")]  # условие может быть гибче
        return "\n".join(neat)

class TokenExpiredError(Exception):
    pass

class DessAPI:
    API_BASE = "http://api.dessmonitor.com/public/"
    APP_ID = "com.demo.test"
    APP_VERSION = "3.6.2.1"
    APP_CLIENT = "android"

    TITLE_MAPPING = {
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
        "Output Voltage": "output_voltage",
        "Output Active Power": "output_power",
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
        self.token = None
        self.secret = None
        self.token_expiry = None
        self.token_acquired_time = None
        self._load_cached_token()        # ← попробуем

        if not self.token:               # первый запуск или токен протух
            self.authenticate()


    def _generate_sign(self, param_str: str, use_password=False):
        salt = str(int(time.time() * 1000))
        if use_password:
            pwd_hash = hashlib.sha1(self.password.encode()).hexdigest()
            raw = f"{salt}{pwd_hash}{param_str}"
        else:
            raw = f"{salt}{self.secret}{self.token}{param_str}"
        return hashlib.sha1(raw.encode()).hexdigest(), salt

    def _do_api_request(self, params: dict, need_auth: bool = True) -> dict:
        # 1. копируем исходный словарь
        params = params.copy()  # action / usr / company-key уже есть

        # 2. ***Добавляем служебные поля ПОСЛЕ*** – update() сохраняет исходный порядок,
        #    новые ключи уходят в конец, как в «старом» скрипте
        params.update({
            "source": "1",
            "_app_client_": self.APP_CLIENT,
            "_app_id_": self.APP_ID,
            "_app_version_": self.APP_VERSION
        })

        # 3. формируем query_str в исходном порядке
        query_str = "&".join(
            f"{urllib.parse.quote_plus(str(k))}={urllib.parse.quote_plus(str(v))}"
            for k, v in params.items()
        )
        query_with_amp = "&" + query_str

        # 4. подпись
        sign, salt = self._generate_sign(query_with_amp, use_password=not need_auth)

        # 5. финальный URL
        if need_auth:
            url = f"{self.API_BASE}?sign={sign}&salt={salt}&token={self.token}{query_with_amp}"
        else:
            url = f"{self.API_BASE}?sign={sign}&salt={salt}{query_with_amp}"

        self.logger.info(f"[API] Выполняем запрос: {url}")

        try:
            print("The request YRL >>>", url, "\n>>> ", end="", flush=True)
            with urllib.request.urlopen(url, timeout=120) as resp:
                raw_data = resp.read()
            data = json.loads(raw_data.decode("utf-8"))
            if data.get("err") != 0:
                desc = data.get("desc", "Unknown error")
                if "TOKEN" in desc or data.get("err") == 0x0002:
                    raise TokenExpiredError("Токен истёк")
                raise RuntimeError(f"Ошибка API: {desc}")
            return data
        except TokenExpiredError:
            raise
        except Exception as e:
            self.logger.error(f"[API] Ошибка при запросе: {e}")
            raise RuntimeError(f"Ошибка запроса: {e}")

    def authenticate(self) -> None:
        self.logger.info("Авторизация (authSource)...")
        params = {"action": "authSource", "usr": self.email, "company-key": self.company_key}
        result = self._do_api_request(params, need_auth=False)
        dat = result.get("dat", {})
        self.token = dat.get("token")
        self.secret = dat.get("secret")
        self.token_expiry = dat.get("expire")
        self.token_acquired_time = time.time()
        if not self.token or not self.secret:
            raise RuntimeError("Не получены token/secret от authSource.")
        self.logger.info("Успешно авторизованы. Получен token.")
        self._save_token()

    def refresh_token(self) -> None:
        self.logger.info("Обновление токена (updateToken)...")
        params = {"action": "updateToken"}
        result = self._do_api_request(params, need_auth=True)
        dat = result.get("dat", {})
        self.token = dat.get("token") or self.token
        self.secret = dat.get("secret") or self.secret
        self.token_expiry = dat.get("expire") or self.token_expiry
        self.token_acquired_time = time.time()
        self.logger.info("Токен обновлён.")
        self._save_token()

    def should_refresh_token(self) -> bool:
        if self.token_expiry is None:
            return True
        return (time.time() - self.token_acquired_time) >= 0.9 * self.token_expiry

    def fetch_device_data(self) -> DeviceData:
        try:
            if self.should_refresh_token():
                try:
                    self.refresh_token()
                except TokenExpiredError:
                    self.authenticate()

            params = {
                "action": "queryDeviceLastData",
                "i18n": "en_US",
                "pn": self.pn,
                "devcode": self.dev_code,
                "devaddr": self.dev_addr,
                "sn": self.sn,
            }
            result = self._do_api_request(params, need_auth=True)
            dd= self._parse_device_data(result)

        except Exception as main_exc:
            self.logger.info(f"[API] основной API упал: {main_exc}. Пытаемся веб‑кролл…")
            dd= self.fetch_device_data_fallback()

        shared_state.update(
                battery_voltage=dd.battery_voltage,
                working_mode=dd.working_state,
                pv_power=dd.pv_total_power,
                load_percent=dd.ac_output_load,
            )
        shared_state["inverter_raw"] = dd.to_dict()
        return dd

    def fetch_device_data_fallback(self) -> DeviceData:
        """
        Резервный вариант: web-кролл querySPDeviceLastData
        """
        # собираем строку action точно так же, как в твоём примере
        action_str = (
            "&action=querySPDeviceLastData"
            f"&pn={urllib.parse.quote_plus(str(self.pn))}"
            f"&devcode={urllib.parse.quote_plus(str(self.dev_code))}"
            f"&devaddr={urllib.parse.quote_plus(str(self.dev_addr))}"
            f"&sn={urllib.parse.quote_plus(str(self.sn))}"
            "&i18n=en_US"
        )

        # подпись и salt
        sign, salt = self._generate_sign(action_str, use_password=False)
        # в web‑варианте всегда передаём token
        url = (
            f"https://web.dessmonitor.com/public/"
            f"?sign={sign}&salt={salt}&token={self.token}{action_str}"
        )
        self.logger.info(f"[WEB] Выполняем запрос: {url}")

        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
            payload = json.loads(raw)
        except Exception as e:
            self.logger.error(f"[WEB] Ошибка запроса: {e}")
            raise RuntimeError(f"Веб‑кролл не удался: {e}")

        # разбираем полученный JSON
        dat = payload.get("dat", {})
        # DeviceData инициализируем сразу с gts как timestamp
        dd = DeviceData(timestamp=dat.get("gts"))

        # pars — словарь массивов: gd_, sy_, pv_, bt_, bc_
        for section in dat.get("pars", {}).values():
            for item in section:
                title = item.get("par")
                val   = item.get("val")
                # если это поле есть в TITLE_MAPPING — запишем в dd
                field = self.TITLE_MAPPING.get(title)
                if not field:
                    continue
                # строковые поля
                if field in [
                    "working_state", "battery_status", "pv_status",
                    "mains_status", "load_status", "charger_priority", "output_priority"
                ]:
                    setattr(dd, field, val)
                else:
                    try:
                        setattr(dd, field, float(val))
                    except Exception:
                        # если не число — сохраняем None
                        setattr(dd, field, None)

        self.logger.info("[WEB] Успешно спарсили данные из веб‑кролла.")
        return dd

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

    def _load_cached_token(self):
        if not _TOKEN_FILE.exists():
            return
        try:
            data = json.loads(_TOKEN_FILE.read_text())
            # небольшая проверка «жив ли» (10% буфер)
            if time.time() - data["acquired_at"] < 0.9*data["expires_in"]:
                self.token            = data["token"]
                self.secret           = data["secret"]
                self.token_expiry     = data["expires_in"]
                self.token_acquired_time = data["acquired_at"]
                self.logger.info("[API] Восстановили token из кеша")
        except Exception as e:
            self.logger.error(f"[API] не смогли прочитать кеш token: {e}")

    def _save_token(self):
        try:
            _TOKEN_FILE.parent.mkdir(exist_ok=True)
            _TOKEN_FILE.write_text(json.dumps({
                "token":       self.token,
                "secret":      self.secret,
                "expires_in":  self.token_expiry,
                "acquired_at": self.token_acquired_time
            }))
        except Exception as e:
            self.logger.error(f"[API] не смогли записать кеш token: {e}")
