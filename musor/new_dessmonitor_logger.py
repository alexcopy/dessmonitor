#!/usr/bin/env python3

import json
import sys
import time
import hashlib
import urllib.parse
import urllib.request
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import dataclass

# MQTT integration
try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

# Constants
API_BASE_URL = "http://api.dessmonitor.com/public/"
DEFAULT_INTERVAL = 30
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"


@dataclass
class APIConfig:
    email: str
    password: str
    company_key: str
    pn: str
    devcode: str
    sn: str
    devaddr: int = 1
    interval: int = DEFAULT_INTERVAL


@dataclass
class MQTTConfig:
    enabled: bool = False
    host: str = "localhost"
    port: int = 1883
    topic: str = "home/dessmonitor"
    username: str = ""
    password: str = ""


class DessMonitorAPI:
    def __init__(self, config: APIConfig):
        self.config = config
        self.token: Optional[str] = None
        self.secret: Optional[str] = None
        self.token_expiry: float = 0
        self.logger = logging.getLogger("DessMonitorAPI")

    def _generate_signature(self, params_str: str, use_password: bool = False) -> tuple:
        salt = str(int(time.time() * 1000))
        if use_password:
            pwd_hash = hashlib.sha1(self.config.password.encode()).hexdigest()
            raw = salt + pwd_hash + params_str
        else:
            raw = salt + self.secret + self.token + params_str
        return hashlib.sha1(raw.encode()).hexdigest(), salt

    def _api_request(self, params: Dict[str, Any], need_auth: bool = True) -> Dict[str, Any]:
        base_params = {
            "source": "1",
            "_app_client_": "python",
            "_app_id_": "com.demo.test",
            "_app_version_": "3.6.2.1"
        }
        params.update(base_params)

        query_str = "&".join(
            f"{urllib.parse.quote_plus(str(k))}={urllib.parse.quote_plus(str(v))}"
            for k, v in params.items()
        )

        if need_auth:
            sign, salt = self._generate_signature(f"&{query_str}")
            url = f"{API_BASE_URL}?sign={sign}&salt={salt}&token={self.token}&{query_str}"
        else:
            sign, salt = self._generate_signature(f"&{query_str}", use_password=True)
            url = f"{API_BASE_URL}?sign={sign}&salt={salt}&{query_str}"

        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            self.logger.error(f"API request failed: {str(e)}")
            raise

    def authenticate(self):
        params = {
            "action": "authSource",
            "usr": self.config.email,
            "company-key": self.config.company_key
        }
        response = self._api_request(params, need_auth=False)

        if response.get("err") != 0:
            raise AuthenticationError(response.get("desc", "Unknown error"))

        data = response["dat"]
        self.token = data["token"]
        self.secret = data["secret"]
        self.token_expiry = time.time() + data["expire"]
        self.logger.info("Authentication successful")

    def refresh_token(self):
        params = {"action": "updateToken"}
        response = self._api_request(params)

        if response.get("err") != 0:
            raise TokenRefreshError(response.get("desc", "Unknown error"))

        data = response["dat"]
        self.token = data.get("token", self.token)
        self.secret = data.get("secret", self.secret)
        self.token_expiry = time.time() + data.get("expire", self.token_expiry)
        self.logger.info("Token refreshed successfully")

    def get_device_data(self) -> Dict[str, Any]:
        params = {
            "action": "queryDeviceLastData",
            "i18n": "en_US",
            "pn": self.config.pn,
            "devcode": self.config.devcode,
            "devaddr": self.config.devaddr,
            "sn": self.config.sn
        }
        response = self._api_request(params)

        if response.get("err") != 0:
            if "TOKEN" in response.get("desc", ""):
                raise TokenExpiredError()
            raise APIError(response.get("desc", "Unknown error"))

        return response["dat"]


class MQTTClient:
    def __init__(self, config: MQTTConfig):
        self.config = config
        self.client = mqtt.Client(client_id="dessmonitor") if mqtt else None
        self.logger = logging.getLogger("MQTTClient")

    def connect(self):
        if not self.config.enabled or not self.client:
            return

        try:
            if self.config.username:
                self.client.username_pw_set(self.config.username, self.config.password)
            self.client.connect(self.config.host, self.config.port)
            self.client.loop_start()
            self.logger.info(f"Connected to MQTT broker {self.config.host}:{self.config.port}")
        except Exception as e:
            self.logger.error(f"MQTT connection failed: {str(e)}")
            self.config.enabled = False

    def publish(self, data: dict):
        if not self.config.enabled or not self.client:
            return

        try:
            self.client.publish(self.config.topic, json.dumps(data))
        except Exception as e:
            self.logger.error(f"MQTT publish failed: {str(e)}")


# class DeviceMonitor:
#     def __init__(self, api: DessMonitorAPI, mqtt_client: MQTTClient):
#         self.api = api
#         self.mqtt_client = mqtt_client
#         self.logger = logging.getLogger("DeviceMonitor")
#
#     def _parse_device_data(self, raw_data: list) -> Dict[str, Any]:
#         parsed = {"device_time": time.strftime("%Y-%m-%d %H:%M:%S")}
#
#         for item in raw_data:
#             title = item.get("title", "").strip()
#             value = item.get("val", "").strip()
#
#             if "Battery Voltage" in title:
#                 parsed["battery_voltage"] = self._parse_float(value)
#             elif "Mode" in title:
#                 parsed["mode"] = value
#             elif "Charge Status" in title:
#                 parsed["charge_status"] = value
#             elif "SOC" in title:
#                 parsed["battery_soc"] = self._parse_float(value)
#             elif "Timestamp" in title:
#                 parsed["device_time"] = value
#
#         return parsed
#
#     def _parse_float(self, value: str) -> Optional[float]:
#         try:
#             return float(value)
#         except (ValueError, TypeError):
#             return None
#
#     def run(self, interval: int):
#         self.api.authenticate()
#         self.logger.info(f"Starting monitoring with {interval} second interval")
#
#         while True:
#             try:
#                 if time.time() > self.api.token_expiry * 0.9:
#                     self.api.refresh_token()
#
#                 raw_data = self.api.get_device_data()
#                 parsed_data = self._parse_device_data(raw_data)
#                 self._log_data(parsed_data)
#                 self.mqtt_client.publish(parsed_data)
#
#             except TokenExpiredError:
#                 self.logger.warning("Token expired, attempting refresh")
#                 self.api.refresh_token()
#             except (APIError, ConnectionError) as e:
#                 self.logger.error(f"Monitoring error: {str(e)}")
#             finally:
#                 time.sleep(interval)
#
#     def _log_data(self, data: Dict[str, Any]):
#         log_parts = [
#             f"Battery: {data.get('battery_voltage', 'N/A')}V",
#             f"Mode: {data.get('mode', 'N/A')}",
#             f"SOC: {data.get('battery_soc', 'N/A')}%"
#         ]
#         log_message = f"[{data['device_time']}] - " + " | ".join(log_parts)
#         self.logger.info(log_message)

class DeviceMonitor:
    def __init__(self, api: DessMonitorAPI, mqtt_client: MQTTClient):
        self.api = api
        self.mqtt_client = mqtt_client
        self.logger = logging.getLogger("DeviceMonitor")

    def _parse_float(self, value: str) -> Optional[float]:
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def _parse_device_data(self, raw_data: list) -> Dict[str, Any]:
        parsed = {
            "device_time": datetime.now().isoformat(),
            "battery_voltage": None,
            "working_state": None,
            "source_interpretation": "❓ Неизвестно"
        }

        for item in raw_data:
            title = item.get("title", "").strip()
            value = item.get("val", "").strip()

            if "Battery Voltage" in title:
                parsed["battery_voltage"] = self._parse_float(value)
            elif "Working State" in title or "Mode" in title:
                parsed["working_state"] = value
                parsed["source_interpretation"] = self._interpret_working_state(value)
            elif "Timestamp" in title:
                parsed["device_time"] = value

        return parsed

    def _interpret_working_state(self, state: str) -> str:
        states = {
            "Line Mode": "⚡ Сеть",
            "Battery Mode": "🔋 Батарея",
            "PV Mode": "☀️ Солнечные панели",
            "Power Saving Mode": "💤 Энергосбережение",
            "Standby Mode": "⏸️ Ожидание",
            "Bypass Mode": "↪️ Bypass",
            "Fault Mode": "❌ Ошибка"
        }
        return states.get(state, "❓ Неизвестно")

    def _log_data(self, data: Dict[str, Any]):
        log_message = (
            f"[Device: {data['device_time']}] - "
            f"Battery: {data.get('battery_voltage', 'N/A')} V | "
            f"Состояние: {data.get('working_state', 'N/A')} → "
            f"{data['source_interpretation']}"
        )
        self.logger.info(log_message)
        print(log_message)

    def run(self, interval: int):
        self.api.authenticate()
        self.logger.info(f"Starting monitoring with {interval} second interval")

        while True:
            try:
                if time.time() > self.api.token_expiry * 0.9:
                    self.api.refresh_token()

                raw_data = self.api.get_device_data()
                parsed_data = self._parse_device_data(raw_data)
                self._log_data(parsed_data)
                self.mqtt_client.publish(parsed_data)

            except TokenExpiredError:
                self.logger.warning("Token expired, attempting refresh")
                self.api.refresh_token()
            except (APIError, ConnectionError) as e:
                self.logger.error(f"Monitoring error: {str(e)}")
            finally:
                time.sleep(interval)

    def _log_data(self, data: Dict[str, Any]):
        log_parts = [
            f"Battery: {data.get('battery_voltage', 'N/A')}V",
            f"Mode: {data.get('mode', 'N/A')}",
            f"SOC: {data.get('battery_soc', 'N/A')}%"
        ]
        log_message = f"[{data['device_time']}] - " + " | ".join(log_parts)
        self.logger.info(log_message)


# Custom Exceptions
class APIError(Exception): pass


class AuthenticationError(APIError): pass


class TokenExpiredError(APIError): pass


class TokenRefreshError(APIError): pass


def load_config(config_file: str) -> tuple:
    with open(config_file) as f:
        config = json.load(f)

    api_config = APIConfig(
        email=config["email"],
        password=config["password"],
        company_key=config["company_key"],
        pn=config["pn"],
        devcode=config["devcode"],
        sn=config["sn"],
        devaddr=config.get("devaddr", 1),
        interval=config.get("interval", DEFAULT_INTERVAL)
    )

    mqtt_config = MQTTConfig(
        enabled=config.get("mqtt", {}).get("enabled", False),
        host=config.get("mqtt", {}).get("host", "localhost"),
        port=config.get("mqtt", {}).get("port", 1883),
        topic=config.get("mqtt", {}).get("topic", "home/dessmonitor"),
        username=config.get("mqtt", {}).get("username", ""),
        password=config.get("mqtt", {}).get("password", "")
    )

    return api_config, mqtt_config


def main():
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        handlers=[
            logging.FileHandler("dessmonitor.log"),
            logging.StreamHandler()
        ]
    )

    try:
        api_config, mqtt_config = load_config("config.json")
        api = DessMonitorAPI(api_config)
        mqtt_client = MQTTClient(mqtt_config)
        mqtt_client.connect()

        monitor = DeviceMonitor(api, mqtt_client)
        monitor.run(api_config.interval)

    except Exception as e:
        logging.error(f"Fatal error: {str(e)}")
        sys.exit(1)
    except KeyboardInterrupt:
        logging.info("Monitoring stopped by user")


if __name__ == "__main__":
    main()
