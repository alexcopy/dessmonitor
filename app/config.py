import json
import sys

class Config:
    def __init__(self, path="config.json"):
        try:
            with open(path, "r") as f:
                config = json.load(f)
        except Exception as e:
            print(f"❌ Ошибка чтения {path}: {e}")
            sys.exit(1)

        self.email = config.get("email")
        self.password = config.get("password")
        self.company_key = config.get("company_key", "")
        self.pn = config.get("pn")
        self.dev_code = config.get("devcode")
        self.dev_addr = config.get("devaddr", 1)
        self.sn = config.get("sn")
        self.web_fallback_url = config.get("web_fallback_url")
        self.interval = config.get("interval", 30)
        self.log_file = config.get("log_file", "logs/dessmonitor.log")

        mqtt = config.get("mqtt", {})
        self.mqtt_enabled = mqtt.get("enabled", False)
        self.mqtt_host = mqtt.get("host", "localhost")
        self.mqtt_port = mqtt.get("port", 1883)
        self.mqtt_topic = mqtt.get("topic", "home/dessmonitor")
        self.mqtt_user = mqtt.get("username", "")
        self.mqtt_pass = mqtt.get("password", "")