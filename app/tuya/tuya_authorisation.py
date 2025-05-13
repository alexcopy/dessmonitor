import logging
from pathlib import Path

from tuya_connector import TuyaOpenAPI
from tuya_iot import (
    AuthType,
    TuyaOpenMQ,
    TuyaDeviceManager,
    TUYA_LOGGER, TuyaCloudOpenAPIEndpoint
)

from app.device_initializer import DeviceInitializer
from app.config import Config
from app.logger import add_file_logger

config = Config()
tuya_config = DeviceInitializer().get_tuya_config()
ENDPOINT = TuyaCloudOpenAPIEndpoint.EUROPE
ACCESS_ID = tuya_config.get("ACCESS_ID")
ACCESS_KEY = tuya_config.get("ACCESS_KEY")


class SingletonMeta(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]


class TuyaAuthorisation(metaclass=SingletonMeta):
    def __init__(self):
        # ▸ 1. переводим SDK-логгер на WARNING (или ERROR)
        TUYA_LOGGER.setLevel(logging.ERROR)
        # ▸ 2. убираем его «консольный» handler
        for h in TUYA_LOGGER.handlers[:]:
            TUYA_LOGGER.removeHandler(h)
        # ▸ 3. чтобы случайно не всплывал в root-логгер
        TUYA_LOGGER.propagate = False

        sdk_file_logger = add_file_logger(
            name="TUYA_SDK",
            path=Path("logs/tuya_sdk.log"),
            level=logging.ERROR,  # WARNING или INFO — по желанию
        )

        TUYA_LOGGER.addHandler(sdk_file_logger.handlers[0])
        self.openapi = TuyaOpenAPI(ENDPOINT, ACCESS_ID, ACCESS_KEY)
        self.openapi.connect()
        self.openapi.auth_type = AuthType.SMART_HOME
        self.deviceManager = TuyaDeviceManager(self.openapi, TuyaOpenMQ(self.openapi))
        self.deviceStatuses = {}

    @property
    def device_manager(self):
        return self.deviceManager
