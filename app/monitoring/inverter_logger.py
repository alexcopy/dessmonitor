from app.logger import add_file_logger, loki_handler, get_loki_logger
from pathlib import Path
import logging
from app.api import DeviceData

class InverterLogger:
    LOG_PATH = Path("logs/inverter.log")

    def __init__(self) -> None:
        self._logger = add_file_logger(
            name="Inverter",
            path=self.LOG_PATH,
            level=logging.INFO,
            fmt="%(asctime)s %(levelname)s: %(message)s",
        )

        lh = loki_handler()
        if lh not in self._logger.handlers:
            self._logger.addHandler(lh)

        self._logger.propagate = False

    # ————————————————————————————
    def log(self, dd: DeviceData) -> None:
        if not isinstance(dd, DeviceData):
            self._logger.warning("InverterLogger: получили не DeviceData: %s", dd)
            return

        loki = get_loki_logger()
        loki.info(
            "inverter_metrics",
            extra={
                "type":      "inverter",
                "ts":        dd.timestamp,
                "mode":      dd.working_state,
                "batt_v":    dd.battery_voltage,
                "batt_pct":  dd.battery_capacity,
                "pv_w":      dd.pv_total_power,
                "out_w":     dd.output_power,
                "load_pct":  dd.ac_output_load,
            },
        )
