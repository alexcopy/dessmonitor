# app/monitoring/inverter_logger.py
import logging
from pathlib import Path

from app.api import DeviceData


class InverterLogger:
    """
    Пишет каждую выборку DeviceData в logs/inverter.log
    (отдельно от FULL/IMPORTANT – не засоряем общий лог).
    """
    LOG_PATH = Path("logs/inverter.log")

    def __init__(self) -> None:
        # ── убеждаемся, что каталог logs/ существует ───────────────────
        self.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        self._logger = logging.getLogger("Inverter")

        # чтобы при повторных инициализациях не вешать второй файловый handler
        if not any(isinstance(h, logging.FileHandler) and h.baseFilename == str(self.LOG_PATH)
                   for h in self._logger.handlers):

            self._logger.setLevel(logging.INFO)

            fh = logging.FileHandler(self.LOG_PATH, encoding="utf-8")
            fh.setFormatter(
                logging.Formatter(
                    fmt="%(asctime)s %(levelname)s: %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S"        # <── исправлено
                )
            )
            self._logger.addHandler(fh)

        # не отдаём сообщения выше, чтобы не дублить
        self._logger.propagate = False

    # ─────────────────────────────────────────────────────────────
    def log(self, dd: DeviceData) -> None:
        """
        DeviceData.summary() уже возвращает красивую строку,
        её и пишем в файл.
        """
        if not isinstance(dd, DeviceData):
            self._logger.warning("InverterLogger: получили не DeviceData: %s", dd)
            return

        self._logger.info(dd.summary())
