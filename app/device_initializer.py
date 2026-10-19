# app/device_initializer.py

import yaml
import os
import shutil
import logging
import tempfile
import math
import re
from threading import RLock
from datetime import datetime

from app.devices.relay_channel_device import RelayChannelDevice, ANALOG_TYPES
from app.devices.relay_device_manager import RelayDeviceManager
from app.devices.device_property_mapping import (
    DevicePropertyMapping,
    MappingValidity,
)

DEVICE_CONFIG = "devices.yaml"
_CONFIG_LOCK = RLock()


class DeviceConfigNotFoundError(Exception):
    """Raised when the device configuration file cannot be found."""
    pass


class DeviceInitializer:
    """Initializes devices from a YAML configuration file.

    Configuration path resolution precedence:
    1. Explicit constructor argument (config_path).
    2. DEVICE_CONFIG_PATH environment variable.
    3. Legacy default "devices.yaml".

    Module-level import does NOT read configuration.
    """

    _instance = None
    _instance_path = None

    def __new__(cls, config_path=None):
        resolved = cls._resolve_config_path(config_path)
        if cls._instance is not None and cls._instance_path == resolved:
            return cls._instance
        instance = super().__new__(cls)
        cls._instance = instance
        cls._instance_path = resolved
        return instance

    @staticmethod
    def _resolve_config_path(config_path: str | None) -> str:
        """Resolve the device configuration file path.

        Precedence:
        1. Explicit constructor argument.
        2. DEVICE_CONFIG_PATH environment variable.
        3. Legacy default "devices.yaml".
        """
        if config_path is not None:
            return config_path
        env_path = os.environ.get("DEVICE_CONFIG_PATH")
        if env_path:
            return env_path
        return DEVICE_CONFIG

    def __init__(self, config_path: str | None = None):
        if getattr(self, "initialized", False):
            return

        self._config_path = self._resolve_config_path(config_path)

        if not os.path.exists(self._config_path):
            raise DeviceConfigNotFoundError(
                f"Device config file not found: {self._config_path}"
            )

        with open(self._config_path, encoding="utf-8") as f:
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
                mapping = DevicePropertyMapping.multi_switch_child(sw_key)
                parent_enabled = hub_cfg.get("enabled", True)
                child_enabled = sw_cfg.get("enabled", True)
                effective_enabled = parent_enabled and child_enabled
                dev = RelayChannelDevice(
                    # --- обязательные поля ---
                    id=sw_cfg.get("id", f"{hub_id}_{sw_key}"),
                    name=sw_cfg["name"],
                    desc=sw_cfg.get("desc", ""),
                    tuya_device_id=tuya_id,
                    device_type="switch",
                    available=sw_cfg.get("available", parent_ok),
                    enabled=effective_enabled,

                    # --- управление / статус ---
                    control_key=sw_key,
                    state_key=sw_key,  # у реле это одно и то-же

                    # --- электрические параметры ---
                    min_volt=sw_cfg.get("min_volt", base_min),
                    max_volt=sw_cfg.get("max_volt", base_max),
                    load_in_wt=sw_cfg.get("load_in_wt", 0),
                    coefficient=float(sw_cfg.get("coefficient", hub_cfg.get("coefficient", 1.0))),
                    has_energy_meter=bool(sw_cfg.get("has_energy_meter", False)),

                    # --- приоритеты / задержки ---
                    priority=sw_cfg.get("priority", hub_cfg.get("priority", 0)),
                    time_delay=sw_cfg.get("time_delay", 10),

                    # --- стартовый статус / extra ---
                    # physical state begins UNKNOWN — available is NOT physical ON/OFF
                    status={},
                    property_mapping=mapping,
                    extra={"switch_time": sw_cfg.get("time_delay", 10)},
                )
                if mapping.mapping_validity == MappingValidity.INVALID:
                    logging.warning(
                        "[CONFIG] Device %s (%s): %s",
                        dev.name, dev.id, mapping.safe_error or "invalid-mapping",
                    )
                self.device_manager.add_device(dev)

            except Exception as exc:
                logging.error(f"[multi_switch] init {sw_key} failed: {exc}", exc_info=True)

    # ----------------------------------------------------------------------
    def _process_single_device(self, cfg: dict) -> None:
        """
        pump / switch / thermometer …
        Uses explicit DevicePropertyMapping — no universal fallbacks.
        """
        try:
            dtype = cfg.get("device_type", "switch").lower()

            # ---- resolve DevicePropertyMapping ---------------------------
            if dtype == "pump":
                mapping = DevicePropertyMapping.pump_device(
                    control_key=cfg.get("control_key"),
                    channel=cfg.get("channel"),
                    api_sw=cfg.get("api_sw"),
                    state_key=cfg.get("state_key"),
                    p_code=(cfg.get("extra") or {}).get("p_code"),
                )
            elif dtype in ANALOG_TYPES:
                mapping = DevicePropertyMapping.sensor_device(
                    state_key=cfg.get("state_key"),
                    channel=cfg.get("channel"),
                    api_sw=cfg.get("api_sw"),
                )
            elif dtype == "switch":
                mapping = DevicePropertyMapping.single_switch(
                    control_key=cfg.get("control_key"),
                    channel=cfg.get("channel"),
                    api_sw=cfg.get("api_sw"),
                    state_key=cfg.get("state_key"),
                )
            elif dtype == "meter":
                # Read-only energy meter — no control, sensor-style mapping
                mapping = DevicePropertyMapping.sensor_device(
                    state_key=cfg.get("state_key") or "energy",
                    channel=cfg.get("channel"),
                    api_sw=cfg.get("api_sw"),
                )
            else:
                # Unknown device type — conservative binary mapping
                mapping = DevicePropertyMapping.inferred_device(
                    control_key=cfg.get("control_key"),
                    state_key=cfg.get("state_key"),
                )

            # Legacy control_key/state_key for backward compat
            control_key = mapping.control_property or ""
            state_key = mapping.state_property or mapping.control_property or ""

            dev = RelayChannelDevice(
                id=cfg["id"],
                name=cfg["name"],
                desc=cfg.get("desc", ""),
                tuya_device_id=cfg["tuya_device_id"],
                device_type=dtype,
                available=cfg.get("available", True),
                enabled=cfg.get("enabled", True),

                # управление / статус
                control_key=control_key,
                state_key=state_key,

                # электрика
                min_volt=cfg.get("min_volt", 0),
                max_volt=cfg.get("max_volt", 0),
                load_in_wt=cfg.get("load_in_wt", 0),
                coefficient=float(cfg.get("coefficient", 1.0)),
                has_energy_meter=bool(cfg.get("has_energy_meter", False)),

                # тайминги / приоритет
                priority=cfg.get("priority", 0),
                time_delay=cfg.get("time_delay", 10),

                # стартовый статус / extra
                status={},
                property_mapping=mapping,
                extra=cfg.get("extra", {}),
            )
            if mapping.mapping_validity == MappingValidity.INVALID:
                logging.warning(
                    "[CONFIG] Device %s (%s): %s",
                    dev.name, dev.id, mapping.safe_error or "invalid-mapping",
                )
            self.device_manager.add_device(dev)

        except Exception as exc:
            logging.error(f"[single_device] init {cfg.get('id')} failed: {exc}", exc_info=True)


    def reload(self) -> dict:
        """Hot-reload devices from config file into existing dev_mgr.

        Clears all current devices and re-initialises from YAML.
        Does NOT touch Tuya credentials or other config sections.
        Returns summary: {"added": N, "errors": [...]}
        """
        if not os.path.exists(self._config_path):
            raise DeviceConfigNotFoundError(
                f"Device config file not found: {self._config_path}"
            )

        with open(self._config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        # Clear existing devices
        for dev in list(self.device_manager.get_devices()):
            try:
                self.device_manager.remove_device(dev)
            except Exception:
                pass

        errors = []
        added = 0

        for dev_cfg in cfg.get("devices", []):
            try:
                if dev_cfg.get("device_type") == "multi_switch":
                    self._process_multi_switch(dev_cfg)
                else:
                    self._process_single_device(dev_cfg)
                added += 1
            except Exception as exc:
                errors.append(f"{dev_cfg.get('id', '?')}: {exc}")

        logging.info("[DeviceInitializer] Reloaded %d devices, %d errors", added, len(errors))
        return {"added": added, "errors": errors}

    @staticmethod
    def read_devices_config(config_path: str) -> list:
        """Read only devices block — never exposes credentials."""
        with _CONFIG_LOCK:
            with open(config_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f)
        return raw.get("devices", [])

    @staticmethod
    def normalize_charge_policy(policy: dict) -> dict:
        """Validate only known policy fields; omitted fields use safe defaults."""
        defaults = {
            "enabled": True,
            "emergency": {"grid_charge_below_v": 24.0, "release_above_v": 24.5},
            "seasonal_topup": {
                "enabled": True, "mode": "auto_long_night",
                "daylight_threshold_hours": 12.0, "start_time": "02:00",
                "end_time": "07:00", "target_voltage_v": 26.0,
            },
            "grid_charge": {"current_a": 2.0},
            "solar_takeover": {"enabled": True, "min_pv_w": 50.0},
        }

        def merge(value, template, path):
            if not isinstance(value, dict) or set(value) - set(template):
                raise ValueError(f"{path} must contain only supported fields")
            result = {}
            for key, default in template.items():
                field = f"{path}.{key}"
                item = value.get(key, default)
                if isinstance(default, dict):
                    item = merge(item, default, field)
                elif isinstance(default, bool):
                    if not isinstance(item, bool):
                        raise ValueError(f"{field} must be boolean")
                elif isinstance(default, float):
                    try:
                        valid = type(item) in (int, float) and math.isfinite(item)
                    except OverflowError:
                        valid = False
                    if not valid:
                        raise ValueError(f"{field} must be a finite number")
                    item = float(item)
                elif not isinstance(item, str):
                    raise ValueError(f"{field} must be text")
                result[key] = item
            return result

        result = merge(policy, defaults, "charge_policy")
        emergency, seasonal = result["emergency"], result["seasonal_topup"]
        on, release = emergency["grid_charge_below_v"], emergency["release_above_v"]
        target = seasonal["target_voltage_v"]
        if not 20 <= on < release < target <= 30:
            raise ValueError("Voltages must satisfy 20 <= emergency < release < target <= 30")
        if seasonal["mode"] not in ("auto_long_night", "always", "off"):
            raise ValueError("seasonal_topup.mode must be auto_long_night, always or off")
        if not 0 < seasonal["daylight_threshold_hours"] <= 24:
            raise ValueError("daylight_threshold_hours must be greater than 0 and at most 24")
        for key in ("start_time", "end_time"):
            if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", seasonal[key]):
                raise ValueError(f"seasonal_topup.{key} must be HH:MM")
        if seasonal["start_time"] == seasonal["end_time"]:
            raise ValueError("Recovery window start and end must differ")
        if result["grid_charge"]["current_a"] != 2.0:
            raise ValueError("Only the verified 2 A grid charging current is supported")
        if result["solar_takeover"]["min_pv_w"] < 0:
            raise ValueError("solar_takeover.min_pv_w must be nonnegative")
        return result

    @staticmethod
    def read_charge_policy_config(config_path: str) -> dict:
        with _CONFIG_LOCK:
            try:
                with open(config_path, encoding="utf-8") as f:
                    raw = yaml.safe_load(f)
            except yaml.YAMLError:
                raise ValueError("Device configuration contains invalid YAML") from None
        if not isinstance(raw, dict):
            raise ValueError("Device configuration must be a mapping")
        return DeviceInitializer.normalize_charge_policy(raw.get("charge_policy", {}))

    @staticmethod
    def write_charge_policy_config(config_path: str, charge_policy: dict) -> dict:
        policy = DeviceInitializer.normalize_charge_policy(charge_policy)
        DeviceInitializer._write_config_section(config_path, "charge_policy", policy)
        return policy

    @staticmethod
    def write_devices_config(config_path: str, devices: list) -> None:
        DeviceInitializer._write_config_section(config_path, "devices", devices)

    @staticmethod
    def _write_config_section(config_path: str, key: str, value) -> None:
        """Serialize section saves, back up first, preserve unrelated YAML values.

        Keep the existing direct-write strategy for mounted configuration files,
        where replacing the mount with an atomic rename is not supported.
        Serialize before opening the destination and fsync before releasing lock.
        """
        with _CONFIG_LOCK:
            with open(config_path, encoding="utf-8") as f:
                try:
                    raw = yaml.safe_load(f)
                except yaml.YAMLError:
                    raise ValueError("Device configuration contains invalid YAML") from None
            if not isinstance(raw, dict):
                raise ValueError("Device configuration must be a mapping")
            raw[key] = value
            serialized = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)
            backup_dir = os.path.join(os.path.dirname(config_path) or ".", "backups")
            os.makedirs(backup_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S.%f")
            fd, _backup_path = tempfile.mkstemp(
                prefix=f"devices.{timestamp}.", suffix=".yaml", dir=backup_dir,
            )
            try:
                with os.fdopen(fd, "wb") as backup, open(config_path, "rb") as original:
                    shutil.copyfileobj(original, backup)
                    backup.flush()
                    os.fsync(backup.fileno())
                with open(config_path, "w", encoding="utf-8") as f:
                    f.write(serialized)
                    f.flush()
                    os.fsync(f.fileno())
            except OSError:
                # Keep the backup for recovery; do not claim the save succeeded.
                raise OSError("Configuration save failed; check file permissions and backup") from None

    @property
    def device_controller(self) -> RelayDeviceManager:
        return self.device_manager

    def get_tuya_config(self):
        with open(self._config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        return config.get("tuya", {})
