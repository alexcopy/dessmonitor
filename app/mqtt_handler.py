import json

try:
    import paho.mqtt.client as mqtt
    mqtt_available = True
except ImportError:
    mqtt_available = False

class MqttHandler:
    _client = None

    def __init__(self, config):
        if not mqtt_available:
            raise RuntimeError("MQTT библиотека paho-mqtt не установлена")

        self.topic = config.mqtt_topic
        self.client = mqtt.Client(client_id="dessmonitor_logger_py")
        if config.mqtt_user:
            self.client.username_pw_set(config.mqtt_user, config.mqtt_pass)
        try:
            self.client.connect(config.mqtt_host, config.mqtt_port, keepalive=60)
            self.client.loop_start()
            MqttHandler._client = self.client
            print(f"[INFO] Подключены к MQTT-брокеру {config.mqtt_host}:{config.mqtt_port}, топик='{self.topic}'")
        except Exception as e:
            raise RuntimeError(f"Ошибка подключения к MQTT: {e}")

    def publish(self, data):
        try:
            payload = json.dumps(data)
            self.client.publish(self.topic, payload, qos=0, retain=False)
        except Exception as e:
            print(f"⚠ Ошибка публикации MQTT: {e}")

    @staticmethod
    def cleanup():
        if MqttHandler._client:
            MqttHandler._client.loop_stop()
            MqttHandler._client.disconnect()
            MqttHandler._client = None