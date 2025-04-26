import json
import logging
import os
import time
import urllib.request

from app.api import DeviceData


class DessWebScraper:
    TITLE_MAPPING = {
        "Timestamp": "timestamp",
        "Working State": "working_state",
        "Battery Voltage": "battery_voltage",
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

    def __init__(self, url_path: str = "web_fallback_url.txt" ):
        self.url_path = url_path
        self.logger = logging.getLogger(__name__)
        self.url = self._load_url()

    def _load_url(self) -> str:
        if not os.path.exists(self.url_path):
            raise FileNotFoundError(f"[WEB] Файл с URL не найден: {self.url_path}")
        with open(self.url_path, "r", encoding="utf-8") as f:
            url = f.read().strip()
        if not url.startswith("http"):
            raise ValueError("[WEB] URL должен начинаться с http:// или https://")
        return url

    def fetch_data(self) -> DeviceData:
        self.logger.log(f"[WEB] Запрос по полному URL: {self.url}")
        try:
            with urllib.request.urlopen(self.url, timeout=35) as response:
                raw = response.read().decode("utf-8")
                payload = json.loads(raw)
        except Exception as e:
            self.logger.error(f"[WEB] Ошибка при веб-запросе: {e}")
            raise

        return self._parse_payload(payload)

    def _parse_payload(self, payload: dict) -> DeviceData:
        dat = payload.get("dat", {})
        timestamp = dat.get("gts", time.strftime("%Y-%m-%d %H:%M:%S"))
        dd = DeviceData(timestamp=timestamp)

        sections = dat.get("pars", {})
        for group in sections.values():
            for item in group:
                title = item.get("par")
                val = item.get("val")
                field = self.TITLE_MAPPING.get(title)

                if not field:
                    continue

                if field in {
                    "working_state", "battery_status", "pv_status",
                    "mains_status", "load_status", "charger_priority", "output_priority"
                }:
                    setattr(dd, field, val)
                else:
                    try:
                        setattr(dd, field, float(val))
                    except (TypeError, ValueError):
                        setattr(dd, field, None)

        self.logger.log("[WEB] Успешно распарсили веб-ответ.")
        self.logger.log(f"{dd}")
        return dd
