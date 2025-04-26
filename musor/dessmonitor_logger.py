#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import hashlib
import urllib.request
import urllib.parse
import json
import sys
import logging

# --- [Опционально] Попытаемся загрузить paho.mqtt для MQTT-публикации ---
mqtt_available = True
try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt_available = False

########################################
# 1) ЧТЕНИЕ КОНФИГА (config.json)
########################################

CONFIG_FILE = "config.json"
try:
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
except Exception as e:
    print(f"❌ Ошибка чтения {CONFIG_FILE}: {e}")
    sys.exit(1)

EMAIL = config.get("email")
PASSWORD = config.get("password")
COMPANY_KEY = config.get("company_key", "")  # может быть "company-key"
PN = config.get("pn")
DEV_CODE = config.get("devcode")
DEV_ADDR = config.get("devaddr", 1)
SN = config.get("sn")
INTERVAL = config.get("interval", 30)
LOG_FILE = config.get("log_file", "dessmonitor.log")

# Настройки MQTT
mqtt_conf = config.get("mqtt", {})
MQTT_ENABLED = mqtt_conf.get("enabled", False)
MQTT_HOST = mqtt_conf.get("host", "localhost")
MQTT_PORT = mqtt_conf.get("port", 1883)
MQTT_TOPIC = mqtt_conf.get("topic", "home/dessmonitor")
MQTT_USER = mqtt_conf.get("username", "")
MQTT_PASS = mqtt_conf.get("password", "")

########################################
# 2) КОНСТАНТЫ / ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
########################################

API_BASE = "http://api.dessmonitor.com/public/"  # Основной URL API
APP_ID = "com.demo.test"  # Пример ID приложения
APP_VERSION = "3.6.2.1"  # Пример версии
APP_CLIENT = "python"  # Можем указать "android" / "ios" / "web"

# Переменные для токена
token = None
secret = None
token_expiry = None
token_acquired_time = None

# Файл для лога
try:
    logfh = open(LOG_FILE, "a", buffering=1)  # Показывает, что будем построчно буферизовать
except Exception as e:
    print(f"❌ Ошибка открытия лог-файла {LOG_FILE}: {e}")
    sys.exit(1)

# Инициализируем MQTT-клиент (если включено и доступно)
mqtt_client = None
if MQTT_ENABLED:
    if not mqtt_available:
        print("⚠ MQTT включён, но библиотека paho-mqtt не установлена. Отключаем MQTT.")
        MQTT_ENABLED = False
    else:
        mqtt_client = mqtt.Client(client_id="dessmonitor_logger_py")
        if MQTT_USER:
            mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
        try:
            mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            mqtt_client.loop_start()
            print(f"[INFO] Подключены к MQTT-брокеру {MQTT_HOST}:{MQTT_PORT}, топик='{MQTT_TOPIC}'")
        except Exception as e:
            print(f"⚠ Ошибка подключения к MQTT-брокеру: {e}")
            MQTT_ENABLED = False


########################################
# 3) ФУНКЦИИ ДЛЯ ПОДПИСИ И ЗАПРОСОВ
########################################

def generate_sign(params_str, use_password=False):
    """
    Генерируем sign (подпись) для запроса:
      - если use_password=True → SHA-1(salt + SHA-1(password) + params_str)
      - иначе (обычные вызовы) → SHA-1(salt + secret + token + params_str)
    Возвращает (sign, salt).
    """
    global token, secret, PASSWORD
    salt = str(int(time.time() * 1000))  # текущая метка времени в мс
    if use_password:
        # Хеш пароля
        pwd_hash = hashlib.sha1(PASSWORD.encode('utf-8')).hexdigest()
        raw = salt + pwd_hash + params_str
    else:
        if not secret or not token:
            raise RuntimeError("Нет secret или token для подписи.")
        raw = salt + secret + token + params_str

    sign = hashlib.sha1(raw.encode('utf-8')).hexdigest()
    return sign, salt


def do_api_request(params, need_auth=True):
    """
    Выполняем GET-запрос на DESSMonitor API.
    params: dict с ключ-значение (action, pn, sn и т.д.)
    need_auth: если True, то используем token+secret; если False (для authSource), используем пароль.
    Возвращает JSON-ответ как dict.
    """
    # Добавим обязательные параметры
    params["source"] = "1"  # тип
    params["_app_client_"] = APP_CLIENT
    params["_app_id_"] = APP_ID
    params["_app_version_"] = APP_VERSION

    # Собираем часть query после "?"
    query_str = "&".join(
        f"{urllib.parse.quote_plus(str(k))}={urllib.parse.quote_plus(str(v))}" for k, v in params.items())

    if need_auth:
        # sign = SHA1(salt + secret + token + '&' + query_str)
        sign, salt = generate_sign("&" + query_str, use_password=False)
        url = f"{API_BASE}?sign={sign}&salt={salt}&token={token}&{query_str}"
    else:
        # authSource
        sign, salt = generate_sign("&" + query_str, use_password=True)
        url = f"{API_BASE}?sign={sign}&salt={salt}&{query_str}"

    # Запрос
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw_data = resp.read()
        data = json.loads(raw_data.decode('utf-8'))
        return data
    except Exception as e:
        raise RuntimeError(f"Ошибка запроса: {e}")


########################################
# 4) ФУНКЦИИ АВТОРИЗАЦИИ И ОБНОВЛЕНИЯ ТОКЕНА
########################################

def authenticate():
    """
    Вызываем action=authSource, чтобы получить token + secret.
    Использует company_key, email (usr) и пароль (вместе с sign).
    """
    global token, secret, token_expiry, token_acquired_time
    print("[INFO] Авторизация (authSource)...")

    params = {
        "action": "authSource",
        "usr": EMAIL,
        "company-key": COMPANY_KEY,
        # pwd не передаем открыто, он участвует только в sign
    }
    result = do_api_request(params, need_auth=False)

    err = result.get("err")
    if err != 0:
        raise RuntimeError(f"authSource неудачно: {result.get('desc', 'Unknown error')}")

    dat = result.get("dat", {})
    token = dat.get("token")
    secret = dat.get("secret")
    token_expiry = dat.get("expire")  # в секундах
    token_acquired_time = time.time()

    if not token or not secret:
        raise RuntimeError("Не получили token/secret от authSource.")

    print("[INFO] Успешно авторизовались. Получен token.")


def refresh_token():
    """
    Вызываем action=updateToken для продления токена.
    """
    global token, secret, token_expiry, token_acquired_time
    print("[INFO] Обновляем токен (updateToken)...")

    params = {
        "action": "updateToken",
    }
    result = do_api_request(params, need_auth=True)

    if result.get("err") != 0:
        raise RuntimeError(f"updateToken неудачно: {result.get('desc')}")

    dat = result.get("dat", {})
    new_token = dat.get("token")
    new_secret = dat.get("secret")
    new_expire = dat.get("expire")

    if new_token:  token = new_token
    if new_secret: secret = new_secret
    if new_expire: token_expiry = new_expire
    token_acquired_time = time.time()

    print("[INFO] Токен обновлён.")


########################################
# 5) ОСНОВНАЯ ЛОГИКА ЗАПРОСА ДАННЫХ С УСТРОЙСТВА
########################################
def parse_and_display_data(data):
    battery_voltage = None
    working_state = None
    timestamp = None

    for item in data["dat"]:
        if item["title"] == "Battery Voltage":
            battery_voltage = item["val"]
        if item["title"] == "Working State":
            working_state = item["val"]
        if item["title"] == "Timestamp":
            timestamp = item["val"]

        print(f"Title:{item['title']} = {item['val']}")

    # Интерпретация источника
    source = "❓ Неизвестно"
    if working_state:
        if working_state == "Line Mode":
            source = "⚡ Сеть"
        elif working_state == "Battery Mode":
            source = "🔋 Батарея"
        elif working_state == "PV Mode":
            source = "☀️ Солнечные панели"
        elif working_state == "Power Saving Mode":
            source = "💤 Энергосбережение"
        elif working_state == "Standby Mode":
            source = "⏸️ Ожидание"
        elif working_state == "Bypass Mode":
            source = "↪️ Bypass"
        elif working_state == "Fault Mode":
            source = "❌ Ошибка"

    log_line = f"[Device: {timestamp}] - Battery: {battery_voltage} V | Состояние: {working_state} → {source}"
    print(log_line)
    logging.info(log_line)


def fetch_device_data():
    """
    Запрашиваем action=queryDeviceLastData, парсим основные поля:
      - Battery Voltage
      - Рабочий режим (Battery/Grid)
      - Charge/Discharge
      - SOC, если есть
      - device_time
    Возвращаем dict c ключами: device_time, battery_voltage, mode, charge_status, battery_soc
    """
    params = {
        "action": "queryDeviceLastData",
        "i18n": "en_US",  # либо zh_CN, en_US ...
        "pn": PN,
        "devcode": DEV_CODE,
        "devaddr": DEV_ADDR,
        "sn": SN
    }
    result = do_api_request(params, need_auth=True)


    if result.get("err") != 0:
        # Может быть "TOKEN_EXPIRED" или другая ошибка
        code = result.get("err")
        desc = result.get("desc", "")
        if "TOKEN" in desc or code == 0x0002:
            raise RuntimeError("TOKEN_EXPIRED")
        raise RuntimeError(f"Ошибка queryDeviceLastData: {desc}")

    parse_and_display_data(result)
    data_list = result.get("dat", [])
    # data_list - это массив из объектов: { "title":"", "val":"", "unit":"..." }

    parsed = {
        "device_time": time.strftime("%Y-%m-%d %H:%M:%S"),  # default fallback
        "battery_voltage": None,
        "mode": None,
        "charge_status": None,
        "battery_soc": None
    }

    battery_current = None

    for item in data_list:
        title = (item.get("title") or "").strip()
        val = (item.get("val") or "").strip()
        unit = item.get("unit", "")

        # possible device time
        if title.lower() in ["时间戳", "timestamp", "time"]:
            parsed["device_time"] = val

        # battery voltage
        if "Battery Voltage" in title or "电池电压" in title:
            try:
                parsed["battery_voltage"] = float(val)
            except:
                parsed["battery_voltage"] = val

        # battery current
        if "Battery Current" in title or "电池电流" in title:
            try:
                battery_current = float(val)
            except:
                battery_current = None

        # mode or run mode
        if "Mode" in title or "运行模式" in title or "Work Mode" in title:
            parsed["mode"] = val  # might be "Battery Mode" or "Line Mode" etc.

        # charge status
        if "Charge Status" in title or "Charging Status" in title or "充电状态" in title:
            parsed["charge_status"] = val

        # battery SOC
        if "SOC" in title or "电池SOC" in title:
            try:
                parsed["battery_soc"] = float(val)
            except:
                parsed["battery_soc"] = val

    # Если parsed["mode"] не установлено, но есть battery_current
    # Попробуем вывести mode=BATTERY/GRID, charge_status=CHARGING/DISCHARGING
    # в случае если нет явного "Mode" / "Charge Status"
    mode = parsed["mode"]
    status = parsed["charge_status"]

    def guess_mode_and_status():
        nonlocal mode, status, battery_current
        # Упрощённая логика
        if isinstance(battery_current, float):
            if abs(battery_current) < 0.1:
                # тока почти нет
                return ("GRID", "IDLE")  # условно
            elif battery_current > 0:
                # разряд батареи
                return ("BATTERY", "DISCHARGING")
            else:
                # отрицательный ток - заряд
                return ("GRID", "CHARGING")
        # fallback
        return (None, None)

    if not mode or not status:
        m, s = guess_mode_and_status()
        if not mode:
            mode = m
        if not status:
            status = s
        parsed["mode"] = mode
        parsed["charge_status"] = status

    return parsed


########################################
# 6) ГЛАВНЫЙ ЦИКЛ
########################################

def main_loop():
    global token, token_expiry, token_acquired_time

    # 1) Авторизация
    try:
        authenticate()
    except Exception as e:
        print(f"❌ Ошибка авторизации: {e}")
        logfh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} AUTH_ERROR: {e}\n")
        sys.exit(1)

    print(f"[INFO] Начинаем опрос данных каждые {INTERVAL} секунд...")

    while True:
        try:
            # Проверим, не пора ли обновлять токен
            if token_expiry:
                elapsed = time.time() - token_acquired_time
                if elapsed >= 0.9 * token_expiry:
                    refresh_token()

            # Запрашиваем данные устройства
            data = fetch_device_data()


            # Пример: 2025-04-10 13:41:06 [Device: 2025-04-10 13:40:59] - Battery: 52.1 V, Mode: GRID, Status: CHARGING
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            dev_time = data.get("device_time", now_str)
            bv = data.get("battery_voltage")
            mode = data.get("mode")
            st = data.get("charge_status")
            soc = data.get("battery_soc")

            parts = []
            if bv is not None:
                if isinstance(bv, (int, float)):
                    parts.append(f"Battery: {bv:.1f} V")
                else:
                    parts.append(f"Battery: {bv} V")
            if mode: parts.append(f"Mode: {mode}")
            if st:   parts.append(f"Status: {st}")
            if soc is not None:
                if isinstance(soc, (int, float)):
                    parts.append(f"SOC: {soc:.1f}%")
                else:
                    parts.append(f"SOC: {soc}%")

            line = f"{now_str} [Device: {dev_time}] - " + ", ".join(parts)

            print(line)
            logfh.write(line + "\n")

            # Публикуем в MQTT (если включено)
            if MQTT_ENABLED and mqtt_client:
                try:
                    payload = json.dumps(data)
                    mqtt_client.publish(MQTT_TOPIC, payload, qos=0, retain=False)
                except Exception as e_mqtt:
                    print(f"⚠ Ошибка публикации MQTT: {e_mqtt}")

        except RuntimeError as e:
            if str(e) == "TOKEN_EXPIRED":
                # Попробуем обновить токен
                try:
                    refresh_token()
                    # И сразу повторим запрос
                    data = fetch_device_data()
                    # ... далее аналогичная логика вывода/лога
                    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                    dev_time = data.get("device_time", now_str)
                    bv = data.get("battery_voltage")
                    mode = data.get("mode")
                    st = data.get("charge_status")
                    soc = data.get("battery_soc")

                    parts = []
                    if bv is not None:
                        if isinstance(bv, (int, float)):
                            parts.append(f"Battery: {bv:.1f} V")
                        else:
                            parts.append(f"Battery: {bv} V")
                    if mode: parts.append(f"Mode: {mode}")
                    if st:   parts.append(f"Status: {st}")
                    if soc is not None:
                        if isinstance(soc, (int, float)):
                            parts.append(f"SOC: {soc:.1f}%")
                        else:
                            parts.append(f"SOC: {soc}%")

                    line = f"{now_str} [Device: {dev_time}] - " + ", ".join(parts)

                    print(line)
                    logfh.write(line + "\n")
                    # MQTT
                    if MQTT_ENABLED and mqtt_client:
                        try:
                            payload = json.dumps(data)
                            mqtt_client.publish(MQTT_TOPIC, payload)
                        except Exception as e_mqtt:
                            print(f"⚠ Ошибка MQTT: {e_mqtt}")
                except Exception as e2:
                    print(f"❌ Сбой при обновлении токена: {e2}")
                    logfh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} REFRESH_ERROR: {e2}\n")
            else:
                print(f"❌ Ошибка: {e}")
                logfh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR: {e}\n")
        except Exception as e:
            print(f"❌ Общая ошибка: {e}")
            logfh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} EXCEPTION: {e}\n")

        time.sleep(INTERVAL)


########################################
# 7) ЗАПУСК
########################################

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("\n[INFO] Завершение по Ctrl+C...")
    except Exception as e:
        print(f"❌ Непредвиденная ошибка: {e}")
    finally:
        if mqtt_client and MQTT_ENABLED:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        logfh.close()
        sys.exit(0)
