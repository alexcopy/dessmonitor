from app.logger import add_file_logger, loki_handler
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

        # Loki получит key=value        ↓
        self._logger.info(dd.summary(), extra={"type": "inverter"})
