import logging
from pathlib import Path
from typing import Optional

from tuya_connector import TuyaOpenAPI
from tuya_iot import (
    AuthType,
    TuyaOpenMQ,
    TuyaDeviceManager,
    TUYA_LOGGER, TuyaCloudOpenAPIEndpoint
)

from app.logger import add_file_logger

ENDPOINT = TuyaCloudOpenAPIEndpoint.EUROPE


class TuyaAuthorisation:
    """Tuya cloud authorisation with explicit dependency injection.

    Production usage:
        auth = TuyaAuthorisation(access_id="...", access_key="...")

    Test usage with injected device manager:
        auth = TuyaAuthorisation(device_manager=fake_mgr)

    Module-level import does NOT read configuration or connect to Tuya.
    """

    def __init__(
        self,
        access_id: str | None = None,
        access_key: str | None = None,
        endpoint=None,
        device_manager=None,
    ):
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
            level=logging.ERROR,
        )

        TUYA_LOGGER.addHandler(sdk_file_logger.handlers[0])

        if device_manager is not None:
            # Injected device manager — no Tuya connection
            self.deviceManager = device_manager
            self.openapi = None
            return

        if not access_id or not access_key:
            raise ValueError(
                "TuyaAuthorisation requires access_id and access_key, "
                "or an injected device_manager"
            )

        ep = endpoint if endpoint is not None else ENDPOINT
        self.openapi = TuyaOpenAPI(ep, access_id, access_key)
        self.openapi.connect()
        self.openapi.auth_type = AuthType.SMART_HOME
        self.deviceManager = TuyaDeviceManager(self.openapi, TuyaOpenMQ(self.openapi))
        self.deviceStatuses = {}

    @property
    def device_manager(self):
        return self.deviceManager
