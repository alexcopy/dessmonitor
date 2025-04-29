# app/logger.py

import logging
import time
from logging import Handler, Logger
from logging.handlers import RotatingFileHandler
from pathlib import Path

class CustomLogHandler(Handler):
    """
    Общий handler: печать в консоль + запись в файл.
    Имя канала пишется в квадратных скобках.
    """
    def __init__(self, path: Path, ch_name: str):
        super().__init__()
        self._file_path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(path, "a", buffering=1, encoding="utf-8")
        self._ch_name = ch_name.upper()

    def emit(self, record):
        msg = self.format(record)
        ts  = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} [{self._ch_name}] {msg}"
        print(line)
        self._file.write(line + "\n")

    def close(self):
        self._file.close()
        super().close()


def setup_logging(
    full_log_path: Path = Path("logs/full.log"),
    important_log_path: Path = Path("logs/important.log"),
    console_log_path: Path = Path("logs/application.log"),
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> tuple[Logger, Logger]:
    """
    • Настраиваем корневой логгер (DEBUG) → console + full file.
    • Создаём два именованных логгера:
        - FULL      (DEBUG+)   — записывает в full.log + console
        - IMPORTANT (INFO+)    — записывает в important.log
    • Возвращаем именно **логгеры**, а не handler'ы.
    """
    # 1) убеждаемся, что директории есть
    for p in (full_log_path, important_log_path, console_log_path):
        p.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # 2) Console-handler (печать всех DEBUG+ сообщений)
    console_h = CustomLogHandler(console_log_path, "FULL")
    console_h.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console_h)

    # 3) Full-file handler (DEBUG+ с ротацией)
    fh_full = RotatingFileHandler(
        full_log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    fh_full.setLevel(logging.DEBUG)
    fh_full.setFormatter(logging.Formatter(
        "%(asctime)s [FULL] %(levelname)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    ))
    root.addHandler(fh_full)

    # 4) IMPORTANT-logger (INFO+ в отдельный файл с ротацией)
    important_logger = logging.getLogger("IMPORTANT")
    important_logger.setLevel(logging.INFO)
    # не будем пропагировать в root, иначе дублируется
    important_logger.propagate = False

    fh_imp = RotatingFileHandler(
        important_log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    fh_imp.setLevel(logging.INFO)
    fh_imp.setFormatter(logging.Formatter(
        "%(asctime)s [IMPORTANT] %(message)s", "%Y-%m-%d %H:%M:%S"
    ))
    important_logger.addHandler(fh_imp)

    # 5) FULL-logger просто делает propagate → root
    full_logger = logging.getLogger("FULL")
    full_logger.setLevel(logging.DEBUG)
    full_logger.propagate = True

    return full_logger, important_logger


def add_file_logger(
    name: str,
    path: Path,
    level: int = logging.INFO,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
    fmt: str = "%(asctime)s %(levelname)s: %(message)s",
    datefmt: str = "%Y-%m-%d %H:%M:%S",
) -> Logger:
    """
    Создаёт (или возвращает уже созданный) файловый логгер `name`,
    пишет в `path` с ротацией.
    Используется для InverterLogger, DeviceStatusLogger и пр.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    lg = logging.getLogger(name)
    if lg.handlers:
        return lg

    lg.setLevel(level)
    # вместо обычного FileHandler — ротация
    fh = RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(fmt, datefmt))
    lg.addHandler(fh)
    # не сливаться в root
    lg.propagate = False
    return lg
