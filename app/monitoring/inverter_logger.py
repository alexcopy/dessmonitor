# app/monitoring/inverter_logger.py
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.api import DeviceData


class InverterLogger:
    """
    Пишет каждую выборку DeviceData в logs/inverter.log
    (отдельно от FULL/IMPORTANT – не засоряем общий лог).
    """
    LOG_PATH = Path("logs/inverter.log")
    MAX_BYTES = 5 * 1024 * 1024
    BACKUP_COUNT = 3

    def __init__(self) -> None:
        # Убедимся, что каталог logs/ существует
        self.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        self._logger = logging.getLogger("Inverter")
        self._logger.setLevel(logging.INFO)
        # Не даём сообщению подниматься выше и дублироваться
        self._logger.propagate = False

        # Если у нас уже есть наш handler — не добавляем заново
        for h in self._logger.handlers:
            if isinstance(h, RotatingFileHandler) and h.baseFilename == str(self.LOG_PATH):
                break
        else:
            fh = RotatingFileHandler(
                filename=self.LOG_PATH,
                maxBytes=self.MAX_BYTES,
                backupCount=self.BACKUP_COUNT,
                encoding="utf-8"
            )
            fh.setFormatter(
                logging.Formatter(
                    fmt="%(asctime)s %(levelname)s: %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S"
                )
            )
            self._logger.addHandler(fh)

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
