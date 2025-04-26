# app/device_initializer.py

import yaml
import os
import logging

from app.devices.relay_channel_device import RelayChannelDevice
from app.devices.relay_device_manager import RelayDeviceManager

DEVICE_CONFIG = "devices.yaml"

class DeviceInitializer:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "initialized", False):
            return

        if not os.path.exists(DEVICE_CONFIG):
            raise FileNotFoundError(f"Config file '{DEVICE_CONFIG}' not found")

        with open(DEVICE_CONFIG, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        self.device_manager = RelayDeviceManager()

        for dev_cfg in cfg.get("devices", []):
            if dev_cfg.get("device_type") == "multi_switch":
                self._process_multi_switch(dev_cfg)
            else:
                self._process_single_device(dev_cfg)

        self.initialized = True

    # app/device_initializer.py  (фрагмент)

    # ----------------------------------------------------------------------
    def _process_multi_switch(self, hub_cfg: dict) -> None:
        """
        Для 12-релейного хаба:
          • каждое switch_N превращаем в отдельный RelayChannelDevice
          • control_key =  "switch_N"
          • state_key   =  "switch_N"   (у Tuya-реле одно и то-же)
        """
        hub_id = hub_cfg["id"]
        tuya_id = hub_cfg["tuya_device_id"]
        base_min = hub_cfg.get("min_volt", 0)
        base_max = hub_cfg.get("max_volt", 0)
        parent_ok = hub_cfg.get("available", True)

        for sw_key, sw_cfg in hub_cfg.get("switches", {}).items():
            try:
                dev = RelayChannelDevice(
                    # --- обязательные поля ---
                    id=sw_cfg.get("id", f"{hub_id}_{sw_key}"),
                    name=sw_cfg["name"],
                    desc=sw_cfg.get("desc", ""),
                    tuya_device_id=tuya_id,
                    device_type="switch",
                    available=sw_cfg.get("available", parent_ok),

                    # --- управление / статус ---
                    control_key=sw_key,
                    state_key=sw_key,  # у реле это одно и то-же

                    # --- электрические параметры ---
                    min_volt=sw_cfg.get("min_volt", base_min),
                    max_volt=sw_cfg.get("max_volt", base_max),
                    load_in_wt=sw_cfg.get("load_in_wt", 0),

                    # --- приоритеты / задержки ---
                    priority=sw_cfg.get("priority", hub_cfg.get("priority", 0)),
                    time_delay=sw_cfg.get("time_delay", 10),

                    # --- стартовый статус / extra ---
                    status={sw_key: sw_cfg.get("available", parent_ok)},
                    extra={"switch_time": sw_cfg.get("time_delay", 10)},
                )
                self.device_manager.add_device(dev)

            except Exception as exc:
                logging.error(f"[multi_switch] init {sw_key} failed: {exc}", exc_info=True)

    # ----------------------------------------------------------------------
    def _process_single_device(self, cfg: dict) -> None:
        """
        pump / switch / thermometer …
        • для насоса задаём control_key='P',  state_key='Power'
        • для остальных  control_key=state_key=channel|api_sw
        """
        try:
            dtype = cfg.get("device_type", "switch").lower()

            # ---- ключи управления/статуса ---------------------------------
            if dtype == "pump":
                control_key = "P"
                state_key = "Power"
            else:
                control_key = cfg.get("channel") or cfg.get("api_sw") or "switch_1"
                state_key = control_key

            dev = RelayChannelDevice(
                id=cfg["id"],
                name=cfg["name"],
                desc=cfg.get("desc", ""),
                tuya_device_id=cfg["tuya_device_id"],
                device_type=dtype,
                available=cfg.get("available", False),

                # управление / статус
                control_key=control_key,
                state_key=state_key,

                # электрика
                min_volt=cfg.get("min_volt", 0),
                max_volt=cfg.get("max_volt", 0),
                load_in_wt=cfg.get("load_in_wt", 0),

                # тайминги / приоритет
                priority=cfg.get("priority", 0),
                time_delay=cfg.get("time_delay", 10),

                # стартовый статус / extra
                status=cfg.get("status", {}),
                extra=cfg.get("extra", {}),
            )
            self.device_manager.add_device(dev)

        except Exception as exc:
            logging.error(f"[single_device] init {cfg.get('id')} failed: {exc}", exc_info=True)

    @property
    def device_controller(self) -> RelayDeviceManager:
        return self.device_manager

    def get_tuya_config(self):
        with open(DEVICE_CONFIG, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        return config.get("tuya", {})
