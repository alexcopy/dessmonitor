from app.logger import add_file_logger, get_loki_logger
from pathlib import Path
import logging
from app.api import DeviceData

class InverterLogger:
    LOG_PATH = Path("logs/inverter.log")

    def __init__(self) -> None:
        """
        ▸ гарантируем, что *хотя бы один* Rotating-file-handler
          ссылается на logs/inverter.log, даже если логгер существовал раньше.
        ▸ Loki-handler добавляем отдельно и *только* во время log().
        """
        self._logger = logging.getLogger("Inverter")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

        # ── file-handler, если его ещё нет ──────────────────────────
        if not any(
            isinstance(h, logging.handlers.RotatingFileHandler)
            and Path(h.baseFilename) == self.LOG_PATH
            for h in self._logger.handlers
        ):
            add_file_logger(               # создаст и добавит handler
                name="Inverter",
                path=self.LOG_PATH,
                level=logging.INFO,
                fmt="%(asctime)s %(levelname)s: %(message)s",
            )

    # ────────────────────────────────────────────────────────────────
    def log(self, dd: DeviceData) -> None:
        if not isinstance(dd, DeviceData):
            self._logger.warning("InverterLogger: получили не DeviceData: %s", dd)
            return

        # 1. многострочный блок для людей
        self._logger.info("\n" + dd.summary())

        # 2. однострочная метрика для Loki
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
