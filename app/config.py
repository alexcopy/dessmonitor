# app/config.py
from __future__ import annotations
import json, os, sys
from pathlib import Path
from typing import Any, Dict

_DEFAULT_PATH = Path("config.json")          # прежнее поведение
_DOCKER_SECRET = Path("/run/secrets/config_json")   # если монтируем secret


class Config:                                 # прежний API остаётся!
    """
    Источник ищем в аком порядке (первый найденный → победил):

    1.  ENV `MONITOR_CONFIG_JSON`  – строка‑JSON целиком
    2.  ENV `MONITOR_CONFIG_PATH`  – путь к файлу
    3.  Docker / k8s secret        – `/run/secrets/config_json`
    4.  локальный файл `config.json` (или переданный аргумент `path`)
    """

    # ────────────────────────────────────────────────────────────────
    def __init__(self, path: str | Path | None = None) -> None:
        cfg_dict = self._load_config(path)

        # ↓ ниже – вся старая распаковка полей (ничего не ломаем)
        self.email          = cfg_dict.get("email")
        self.password       = cfg_dict.get("password")
        self.company_key    = cfg_dict.get("company_key", "")
        self.pn             = cfg_dict.get("pn")
        self.dev_code       = cfg_dict.get("devcode")
        self.dev_addr       = cfg_dict.get("devaddr", 1)
        self.sn             = cfg_dict.get("sn")
        self.web_fallback_url = cfg_dict.get("web_fallback_url")
        self.interval       = cfg_dict.get("interval", 30)
        self.log_file       = cfg_dict.get("log_file", "logs/dessmonitor.log")

        mqtt                = cfg_dict.get("mqtt", {})
        self.mqtt_enabled   = mqtt.get("enabled", False)
        self.mqtt_host      = mqtt.get("host", "localhost")
        self.mqtt_port      = mqtt.get("port", 1883)
        self.mqtt_topic     = mqtt.get("topic", "home/dessmonitor")
        self.mqtt_user      = mqtt.get("username", "")
        self.mqtt_pass      = mqtt.get("password", "")

    # ────────────────────────────────────────────────────────────────
    @staticmethod
    def _load_config(path_arg: str | Path | None) -> Dict[str, Any]:
        # 1️⃣  JSON прямо в ENV
        if os.getenv("MONITOR_CONFIG_JSON"):
            try:
                return json.loads(os.environ["MONITOR_CONFIG_JSON"])
            except json.JSONDecodeError as e:
                print(f"❌ ENV MONITOR_CONFIG_JSON: неверный JSON: {e}")

        # 2️⃣  путь к файлу в ENV
        env_path = os.getenv("MONITOR_CONFIG_PATH")
        if env_path and Path(env_path).exists():
            return _read_json(env_path)

        # 3️⃣  Docker / k8s‑secret
        if _DOCKER_SECRET.exists():
            return _read_json(_DOCKER_SECRET)

        # 4️⃣  «старый» способ (аргумент или по умолчанию)
        final_path = Path(path_arg) if path_arg else _DEFAULT_PATH
        if final_path.exists():
            return _read_json(final_path)

        print("❌ Не найден ни один источник конфигурации!")
        sys.exit(1)


# ───── helper ───────────────────────────────────────────────────────
def _read_json(p: str | Path) -> Dict[str, Any]:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Ошибка чтения конфигурации {p}: {e}")
        sys.exit(1)
