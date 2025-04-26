#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import hashlib
import urllib.request
import urllib.parse
import json

# Try to import MQTT client if MQTT is enabled
mqtt_available = True
try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt_available = False

# Load configuration
with open("../config.json", "r") as f:
    config = json.load(f)

EMAIL       = config.get("email")
PASSWORD    = config.get("password")
COMPANY_KEY = config.get("company_key") or config.get("company-key")  # support both key styles
PN          = config.get("pn")
DEVCODE     = config.get("devcode")
DEVADDR     = config.get("devaddr")
SN          = config.get("sn")
INTERVAL    = config.get("interval", 30)
LOG_FILE    = config.get("log_file", "dessmonitor.log")

# MQTT configuration
mqtt_conf = config.get("mqtt", {})
MQTT_ENABLED = mqtt_conf.get("enabled", False)
MQTT_HOST    = mqtt_conf.get("host", "localhost")
MQTT_PORT    = mqtt_conf.get("port", 1883)
MQTT_TOPIC   = mqtt_conf.get("topic", "home/dessmonitor")
MQTT_USER    = mqtt_conf.get("username", "")
MQTT_PASS    = mqtt_conf.get("password", "")

# API constants (from DESSMonitor API spec)
API_BASE    = "http://api.dessmonitor.com/public/"  # base URL for API requests
APP_ID      = "com.demo.test"     # example app id as per documentation
APP_VERSION = "3.6.2.1"           # example app version
APP_CLIENT  = "android"           # example client (android/ios/web)

# Global variables for API session
token  = None
secret = None
token_expiry = None  # in seconds (duration)
token_acquired_time = None  # timestamp when token was obtained

# Initialize log file
logfh = open(LOG_FILE, "a", buffering=1)  # line-buffered file for logging
# Write a header to the log if desired (optional)
# logfh.write("Timestamp, Battery_Voltage, Mode(Battery/Grid), Charge_Status(Charging/Discharging/Idle)\n")

# MQTT setup (if enabled and available)
mqtt_client = None
if MQTT_ENABLED:
    if not mqtt_available:
        print("WARNING: MQTT output enabled in config, but paho-mqtt is not installed. MQTT will be disabled.")
        MQTT_ENABLED = False
    else:
        mqtt_client = mqtt.Client(client_id="dessmonitor_logger")
        if MQTT_USER:
            mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
        try:
            mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            mqtt_client.loop_start()  # start background thread to maintain connection
            print(f"[INFO] Connected to MQTT broker at {MQTT_HOST}:{MQTT_PORT}, publishing to topic '{MQTT_TOPIC}'.")
        except Exception as e:
            print(f"ERROR: Could not connect to MQTT broker: {e}")
            MQTT_ENABLED = False

def generate_sign(params, use_password=False):
    """
    Generate the API request signature (sign) using either password or secret+token.
    If use_password is True, uses PASSWORD in hash (for authSource).
    Otherwise uses current secret+token (for other API calls).
    `params` should be the string of URL parameters starting with &action=... (everything after token).
    """
    global secret, token
    salt = str(int(time.time() * 1000))  # use current time in ms as salt
    if use_password:
        # SHA-1 of password
        pwd_hash = hashlib.sha1(PASSWORD.encode('utf-8')).hexdigest()
        # sign = SHA-1(salt + pwd_hash + params)
        raw_str = salt + pwd_hash + params
    else:
        # sign = SHA-1(salt + secret + token + params)
        if not secret or not token:
            raise RuntimeError("Secret/Token not available for signing.")
        raw_str = salt + secret + token + params
    sign = hashlib.sha1(raw_str.encode('utf-8')).hexdigest()
    return sign, salt

def api_request(params, require_auth=True):
    """
    Perform an API GET request to the DESSMonitor API with given query parameters.
    `params` is a dict of query parameters (without sign, salt, token).
    If require_auth is True, include token in request and sign with secret+token.
    If require_auth is False (for authSource), sign with password.
    Returns parsed JSON (as dict), or raises exception on error (e.g., network issues).
    """
    global token, secret
    # Base required params in all requests
    params.setdefault("source", "1")  # 1 = energy storage platform
    params.setdefault("_app_client_", APP_CLIENT)
    params.setdefault("_app_id_",     APP_ID)
    params.setdefault("_app_version_", APP_VERSION)
    # Construct the portion of query string from action onward
    query_str = "&".join(f"{urllib.parse.quote_plus(str(k))}={urllib.parse.quote_plus(str(v))}" for k, v in params.items())
    if require_auth:
        # Ensure we have a token
        if not token or not secret:
            raise RuntimeError("API call requires authentication, but no token/secret available.")
        # Compute sign using secret+token
        sign, salt = generate_sign("&" + query_str, use_password=False)
        full_url = f"{API_BASE}?sign={sign}&salt={salt}&token={token}&{query_str}"
    else:
        # Authentication (authSource) call, uses password in sign
        sign, salt = generate_sign("&" + query_str, use_password=True)
        full_url = f"{API_BASE}?sign={sign}&salt={salt}&{query_str}"
    # Perform HTTP GET request
    try:
        with urllib.request.urlopen(full_url, timeout=10) as resp:
            resp_data = resp.read()
    except Exception as e:
        raise  # propagate exception to handle elsewhere (will be caught in main loop)
    # Parse JSON response
    data = json.loads(resp_data.decode('utf-8'))
    return data

def authenticate():
    """Authenticate with the API (authSource) to obtain token and secret."""
    global token, secret, token_expiry, token_acquired_time
    print("[INFO] Authenticating with DESSMonitor...")
    params = {
        "action": "authSource",
        "usr": EMAIL,
        "pwd": "",           # Note: password is not sent directly; it's used in sign instead.
        "company-key": COMPANY_KEY
    }
    result = api_request(params, require_auth=False)
    if result.get("err") != 0:
        raise RuntimeError(f"Authentication failed: {result.get('desc')}")
    # Extract token and secret from result
    dat = result.get("dat", {})
    token  = dat.get("token")
    secret = dat.get("secret")
    token_expiry = dat.get("expire")  # seconds of validity
    token_acquired_time = time.time()
    if token and secret:
        print("[INFO] Authentication successful. Token acquired.")
    else:
        raise RuntimeError("Authentication response did not contain token/secret.")

def refresh_token():
    """Refresh the token using updateToken API (to avoid full re-login)."""
    global token, secret, token_expiry, token_acquired_time
    print("[INFO] Refreshing API token...")
    params = { "action": "updateToken" }
    result = api_request(params, require_auth=True)
    if result.get("err") != 0:
        raise RuntimeError(f"Token refresh failed: {result.get('desc')}")
    dat = result.get("dat", {})
    # Typically updateToken returns a new token (and possibly a new secret and expire)
    new_token  = dat.get("token")
    new_secret = dat.get("secret")
    new_expire = dat.get("expire")
    # Update only if present (some APIs might keep same secret)
    if new_token:
        token = new_token
    if new_secret:
        secret = new_secret
    if new_expire:
        token_expiry = new_expire
    token_acquired_time = time.time()
    print("[INFO] Token refreshed successfully.")

def fetch_device_data():
    """Fetch the latest device data using queryDeviceLastData API. Returns a dict of key parameters."""
    params = {
        "action": "queryDeviceLastData",
        "i18n": "en_US",        # request data in English&#8203;:contentReference[oaicite:1]{index=1}
        "pn": PN,
        "devcode": DEVCODE,
        "devaddr": DEVADDR,
        "sn": SN
    }
    result = api_request(params, require_auth=True)
    if result.get("err") != 0:
        # If token expired or invalid, raise a specific error to handle re-auth
        err_desc = result.get("desc", "")
        if "TOKEN" in err_desc or "SECRET" in err_desc or result.get("err") == 0x0002:
            # Assume this means token expired or invalid (just a heuristic check)
            raise RuntimeError("TOKEN_EXPIRED")
        # Other errors (no data, device not found, etc.)
        raise RuntimeError(f"Device data query failed: {result.get('desc')}")
    data_list = result.get("dat", [])
    # Parse out key parameters from data_list.
    # The data_list is an array of { "title": "...", "val": "...", "unit": "..." }.
    # According to docs, index 0 is an internal ID, index 1 is timestamp.
    device_time = None
    key_params = {}
    for item in data_list:
        title = str(item.get("title", "")).strip()
        value = str(item.get("val", "")).strip()
        unit  = item.get("unit", "")
        # Identify device timestamp
        if title.lower() in ["时间戳", "timestamp"]:  # support Chinese or English title
            device_time = value
        # Battery Voltage
        if "Battery Voltage" in title or "电池电压" in title:
            # convert to float if possible
            try:
                key_params["battery_voltage"] = float(value)
            except:
                key_params["battery_voltage"] = value  # keep as string if not numeric
        # Battery Current
        if "Battery Current" in title or "电池电流" in title:
            try:
                batt_cur = float(value)
            except:
                batt_cur = None
            key_params["battery_current"] = batt_cur if batt_cur is not None else value
        # Work/Operating Mode (battery or line mode)
        if "Mode" in title or "工作模式" in title or "运行模式" in title:
            # If the device provides an operating mode (e.g., "Battery Mode" vs "Line Mode")
            key_params["mode_text"] = value
        # Charge/Discharge Status
        if "Charge Status" in title or "Charging Status" in title or "充电状态" in title:
            key_params["charge_status_text"] = value
        # (Optional) Battery State of Charge (SOC)
        if title.lower().startswith("soc") or "State of Charge" in title or "电池SOC" in title:
            # We'll include SOC if available
            try:
                key_params["battery_soc"] = float(value)
            except:
                key_params["battery_soc"] = value
    # Derive mode (battery vs grid) and charge status if not directly provided as text:
    mode = None
    status = None
    # If textual mode info is available (e.g., "Battery Mode"/"Line Mode")
    if "mode_text" in key_params:
        text = key_params["mode_text"].lower()
        if "battery" in text:
            mode = "BATTERY"
        elif "line" in text or "grid" in text:
            mode = "GRID"
    # If textual charge status available (e.g., "Charging"/"Discharging")
    if "charge_status_text" in key_params:
        text = key_params["charge_status_text"].lower()
        if "charging" in text:
            status = "CHARGING"
            # If charging, likely running on grid
            if mode is None:
                mode = "GRID"
        elif "discharging" in text:
            status = "DISCHARGING"
            if mode is None:
                mode = "BATTERY"
        elif "idle" in text or "standby" in text:
            status = "IDLE"
        else:
            status = text.title()  # use the text as is (title-case)
    # If no explicit status text, use battery current to infer status and mode
    batt_cur = key_params.get("battery_current")
    if status is None or mode is None:
        if isinstance(batt_cur, (int, float)):
            # Use a small threshold to filter out very tiny currents
            if batt_cur is not None:
                if batt_cur > 0.1:
                    status = "DISCHARGING"
                    mode = mode or "BATTERY"
                elif batt_cur < -0.1:
                    status = "CHARGING"
                    mode = mode or "GRID"
                else:
                    status = status or "IDLE"
                    mode = mode or "GRID"
        else:
            # If battery_current is not available as number (or missing), set unknown if not set
            status = status or "UNKNOWN"
            mode = mode or "UNKNOWN"
    # Prepare result dictionary of key values
    result_data = {
        "device_time": device_time or time.strftime("%Y-%m-%d %H:%M:%S"),
        "battery_voltage": key_params.get("battery_voltage"),
        "mode": mode,
        "charge_status": status
    }
    if "battery_soc" in key_params:
        result_data["battery_soc"] = key_params["battery_soc"]
    return result_data

# --- Main polling loop ---
try:
    authenticate()  # initial login to get token/secret
except Exception as e:
    print(f"ERROR: Authentication failed. {e}")
    logfh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR: Authentication failed: {e}\n")
    logfh.close()
    exit(1)

# Continuous polling
print(f"[INFO] Starting data polling every {INTERVAL} seconds...")
while True:
    try:
        # If token is close to expiring, refresh it (e.g., if >90% of expiry time passed)
        if token_expiry:
            elapsed = time.time() - token_acquired_time
            if elapsed >= 0.9 * token_expiry:
                refresh_token()
        # Fetch device data
        data = fetch_device_data()
        # Format output message
        t = data["device_time"]
        bv = data.get("battery_voltage")
        mode = data.get("mode")
        status = data.get("charge_status")
        soc = data.get("battery_soc", None)
        # Build log/print line
        # Example: "2025-04-10 13:41:06 [Device: 2025-04-10 13:40:59] - Battery: 52.1 V, Mode: GRID, Status: CHARGING"
        log_time = time.strftime("%Y-%m-%d %H:%M:%S")
        msg_parts = []
        if bv is not None:
            # format voltage with one decimal if it's a float
            if isinstance(bv, (int, float)):
                msg_parts.append(f"Battery: {bv:.1f} V")
            else:
                msg_parts.append(f"Battery: {bv} V")
        if mode:
            msg_parts.append(f"Mode: {mode}")
        if status:
            msg_parts.append(f"Status: {status}")
        if soc is not None:
            # include SOC if available
            if isinstance(soc, (int, float)):
                msg_parts.append(f"SOC: {soc:.1f}%")
            else:
                msg_parts.append(f"SOC: {soc}%")
        output_line = f"{log_time} [Device: {t}] - " + ", ".join(msg_parts)
        # Print to console
        print(output_line)
        # Append to log file
        logfh.write(output_line + "\n")
        # Publish to MQTT if enabled
        if MQTT_ENABLED and mqtt_client:
            try:
                # Publish the key data as a JSON payload to the configured topic
                mqtt_payload = json.dumps(data)
                mqtt_client.publish(MQTT_TOPIC, mqtt_payload, qos=0, retain=False)
            except Exception as mq:
                print(f"WARNING: MQTT publish failed: {mq}")
    except Exception as e:
        # Handle exceptions (such as network error or token expiration)
        if str(e) == "TOKEN_EXPIRED":
            # Our heuristic detected an expired token, attempt to refresh and retry immediately
            try:
                refresh_token()
                # After refreshing token, retry once immediately
                data = fetch_device_data()
                # (We can reuse the logging code above by looping, but to keep it simple, just replicate minimal logging)
                t = data.get("device_time")
                bv = data.get("battery_voltage")
                mode = data.get("mode")
                status = data.get("charge_status")
                soc = data.get("battery_soc", None)
                log_time = time.strftime("%Y-%m-%d %H:%M:%S")
                msg_parts = []
                if bv is not None:
                    if isinstance(bv, (int, float)):
                        msg_parts.append(f"Battery: {bv:.1f} V")
                    else:
                        msg_parts.append(f"Battery: {bv} V")
                if mode:
                    msg_parts.append(f"Mode: {mode}")
                if status:
                    msg_parts.append(f"Status: {status}")
                if soc is not None:
                    if isinstance(soc, (int, float)):
                        msg_parts.append(f"SOC: {soc:.1f}%")
                    else:
                        msg_parts.append(f"SOC: {soc}%")
                output_line = f"{log_time} [Device: {t}] - " + ", ".join(msg_parts)
                print(output_line)
                logfh.write(output_line + "\n")
                if MQTT_ENABLED and mqtt_client:
                    try:
                        mqtt_payload = json.dumps(data)
                        mqtt_client.publish(MQTT_TOPIC, mqtt_payload)
                    except Exception as mq:
                        print(f"WARNING: MQTT publish failed: {mq}")
            except Exception as e2:
                print(f"ERROR: Token refresh or retry failed: {e2}")
                logfh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR: Token refresh failed: {e2}\n")
        else:
            # General error (e.g., network down or API error)
            print(f"ERROR: {e}")
            logfh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR: {e}\n")
    # Wait for the next polling interval
    time.sleep(INTERVAL)
