import json
import logging
import sys
import time
import os
from pprint import pprint

from tuya_iot import (
    TuyaOpenAPI,
    TuyaDeviceManager,
    TuyaOpenMQ,
    AuthType,
    TuyaCloudOpenAPIEndpoint as Endpoint,
    TUYA_LOGGER
)

DEVICE_ID = " "
ACCESS_ID= ""
ACCESS_KEY= ""
DP_CODE    = "switch_1"               # либо cur_power, voltage, …
HOURS      = 12                       # глубина истории
LIMIT      = 100                      # макс. строк

ENDPOINT   = Endpoint.EUROPE


class TuyaAuthorisation():
    def __init__(self):
        TUYA_LOGGER.setLevel(logging.INFO)
        self.openapi = TuyaOpenAPI(ENDPOINT, ACCESS_ID, ACCESS_KEY)
        self.openapi.auth_type = AuthType.SMART_HOME
        self.openapi.connect()
        self.deviceManager = TuyaDeviceManager(self.openapi, TuyaOpenMQ(self.openapi))

    @property
    def device_manager(self):
        return self.deviceManager


if __name__ == "__main__":
    try:
        tuya_auth = TuyaAuthorisation()
        # Вызовите напрямую метод openapi.get
        statuses = tuya_auth.device_manager.get_device_list_status([DEVICE_ID])
        print("RAW API RESPONSE:", statuses)
    except Exception as e:
        logging.exception("Critical error occurred")

