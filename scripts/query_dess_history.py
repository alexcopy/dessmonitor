#!/usr/bin/env python3
"""
Query DESS cloud API for historical device data.
Run from inside the dessmonitor pod where config is available:
  kubectl exec -n dess deployment/dessmonitor -- python3 scripts/query_dess_history.py
"""
import sys, json, datetime
sys.path.insert(0, "/app")

from app.config import Config
from app.api import DessAPI
import logging

logging.basicConfig(level=logging.WARNING)

cfg = Config()
api = DessAPI(cfg, logging.getLogger("dess_history"))

# Authenticate
api.authenticate()
print(f"[auth] token OK: {api.token[:8]}...")

# Try known DESS history actions
HISTORY_ACTIONS = [
    "queryDeviceDataByDay",
    "queryDeviceDataList",
    "getDevDataByDay",
    "queryDayPower",
    "queryHistoryData",
]

today = datetime.date.today()

for action in HISTORY_ACTIONS:
    params = {
        "action":   action,
        "i18n":     "en_US",
        "pn":       cfg.pn,
        "devcode":  cfg.dev_code,
        "devaddr":  cfg.dev_addr,
        "sn":       cfg.sn,
        "date":     today.strftime("%Y%m%d"),   # YYYYMMDD
        "day":      today.strftime("%Y%m%d"),   # alternate key
        "stationId": "",
    }
    try:
        result = api._do_api_request(params, need_auth=True, transport="api")
        print(f"\n✅ {action} SUCCESS:")
        print(json.dumps(result, indent=2, default=str)[:2000])
        break
    except Exception as e:
        print(f"❌ {action}: {e}")

print("\nDone.")
