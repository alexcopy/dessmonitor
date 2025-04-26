from app.device_initializer import DeviceInitializer
from app.devices.pond_pump_controller import PondPumpController
from app.devices.relay_device_manager import RelayDeviceManager
from app.tuya.relay_tuya_controller import RelayTuyaController
# from app.tuya.tuya_authorisation import TuyaAuthorisation
import time
import threading
import logging

class SmartHomeService:
    def __init__(self):
        # self.authorisation = TuyaAuthorisation()
        self.device_initializer = DeviceInitializer()

        self.device_manager: RelayDeviceManager = self.device_initializer.get_manager()
        self.controller = RelayTuyaController(self.authorisation)
        self.pump_controller = PondPumpController()

        self._stop_event = threading.Event()

    def get_devices(self):
        return self.device_manager.get_devices()

    def run_switch_monitoring(self, interval=120):
        def monitor():
            while not self._stop_event.is_set():
                devices = self.get_devices()
                logging.info("[SwitchMonitor] Обновление статусов устройств...")
                self.controller.update_devices_status(devices)
                time.sleep(interval)

        t = threading.Thread(target=monitor, daemon=True)
        t.start()

    def run_inverter_monitoring(self, inverter_name="inverter", interval=30):
        def monitor():
            while not self._stop_event.is_set():
                inverter = next((d for d in self.get_devices() if d.name.lower() == inverter_name), None)
                if inverter:
                    voltage = inverter.inverter_voltage
                    inverter_on = inverter.is_device_on()
                    logging.info(f"[InverterMonitor] Inverter status: ON={inverter_on}, Voltage={voltage}")
                time.sleep(interval)

        t = threading.Thread(target=monitor, daemon=True)
        t.start()

    def run_pump_adjustment_loop(self, interval=60):
        def pump_loop():
            while not self._stop_event.is_set():
                devices = self.get_devices()
                pumps = [d for d in devices if d.get_device_type().upper() == "PUMP"]

                for pump in pumps:
                    try:
                        inv_status = 1 if pump.inverter_is_on else 0
                        inv_voltage = pump.inverter_voltage

                        new_speed = self.pump_controller.adjust_speed(pump, inv_status, inv_voltage)
                        current_speed = int(pump.get_status("P"))

                        if new_speed != current_speed:
                            logging.info(f"[PumpAdjust] {pump.name}: {current_speed} → {new_speed}")

                            # специальный метод для вариаторных устройств (не bool)
                            command = {"devId": pump.get_id(), "commands": [{"code": pump.get_api_sw(), "value": new_speed}]}
                            response = self.authorisation.device_manager.send_commands(command["devId"], command['commands'])
                            success = response.get("success", False)

                            if success:
                                pump.update_status({"P": new_speed})
                                pump.mark_switched()
                            else:
                                logging.error(f"[PumpAdjust] Не удалось изменить скорость насоса {pump.name}")

                    except Exception as e:
                        logging.error(f"[PumpAdjust] Ошибка обработки насоса {pump.name}: {e}")

                time.sleep(interval)

        threading.Thread(target=pump_loop, daemon=True).start()

    def stop_all(self):
        self._stop_event.set()