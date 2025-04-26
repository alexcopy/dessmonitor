import logging
import os

import yaml

from app.devices.relay_channel_device import RelayChannelDevice
from app.devices.relay_device_manager import RelayDeviceManager

DEVICE_CONFIG = "devices.yaml"


class InitiateDevices:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "initialized") and self.initialized:
            return

        self.device_manager = RelayDeviceManager()

        if not os.path.exists(DEVICE_CONFIG):
            raise FileNotFoundError(f"Config file '{DEVICE_CONFIG}' not found")

        with open(DEVICE_CONFIG, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        devices_config = config.get('devices', [])
        for device_config in devices_config:
            try:
                if device_config.get('device_type') == 'multi_switch':
                    self._process_multi_switch_device(device_config)
                else:
                    self._process_single_device(device_config)
            except Exception as e:
                logging.error(f"Exception is: Error initializing device {device_config.get('id')}: {e}")

        self.initialized = True

    def _process_multi_switch_device(self, device_config):
        base_params = {
            'id': device_config['id'],
            'tuya_device_id': device_config['tuya_device_id'],
            'available': device_config.get('available', False),
            'coefficient': device_config.get('coefficient', 1.0),
            'device_type': 'switch',
        }

        for key in device_config:
            if key.startswith('switch_'):
                switch_num = key.split('_')[1]
                switch_config = device_config[key]

                channel_id = f"{base_params['id']}-{switch_num}"
                channel_params = {
                    'id': channel_id,
                    'name': switch_config.get('name', f"{device_config['name']}_{switch_num}"),
                    'desc': switch_config.get('desc', ''),
                    'min_volt': switch_config.get('min_volt', device_config.get('min_volt', 0)),
                    'max_volt': switch_config.get('max_volt', device_config.get('max_volt', 0)),
                    'priority': switch_config.get('priority', device_config.get('priority', 1)),
                    'time_delay': switch_config.get('time_delay', 120),
                    'available': switch_config.get('available', base_params['available']),
                    'api_key': f"{base_params['tuya_device_id']}_{switch_num}",
                    'coefficient': switch_config.get('coefficient', base_params['coefficient']),
                    'tuya_device_id': base_params['tuya_device_id'],
                }
                channel_params.update(base_params)
                device = RelayChannelDevice(**channel_params)
                self.device_manager.add_device(device)

    def _process_single_device(self, device_config):
        device_params = {
            'id': device_config['id'],
            'name': device_config['name'],
            'desc': device_config.get('desc', ''),
            'tuya_device_id': device_config['tuya_device_id'],
            'device_type': device_config.get('device_type', 'switch'),
            'available': device_config.get('available', False),
            'min_volt': device_config.get('min_volt', 0),
            'max_volt': device_config.get('max_volt', 0),
            'priority': device_config.get('priority', 1),
            'time_delay': device_config.get('time_delay', 120),
            'coefficient': device_config.get('coefficient', 1.0),
            'api_key': device_config.get('tuya_device_id'),
        }
        if 'channel' in device_config:
            device_params['api_key'] = device_config['channel']
        device = RelayChannelDevice(**device_params)
        self.device_manager.add_device(device)

    @property
    def device_controller(self) -> RelayDeviceManager:
        return self.device_manager
