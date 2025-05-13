# app/logger.py

import logging
import time
from logging import Handler, Logger
from logging.handlers import RotatingFileHandler
from pathlib import Path
from logfmter import Logfmter
from concurrent_log_handler import ConcurrentRotatingFileHandler as RFH

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
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} [{self._ch_name}] {msg}"
        print(line)
        self._file.write(line + "\n")

    def close(self):
        self._file.close()
        super().close()

_loki_handler = None

def setup_logging(
        full_log_path: Path = Path("logs/full.log"),
        important_log_path: Path = Path("logs/important.log"),
        console_log_path: Path = Path("logs/application.log"),
        loki_log_path: Path = Path("logs/loki.log"),
        max_bytes: int = 5 * 1024 * 1024,
        backup_count: int = 3,
):
    root = logging.getLogger()

    if root.handlers:  # ➜ уже настроено, второй раз не трогаем
        return logging.getLogger("FULL"), logging.getLogger("IMPORTANT")

    root.setLevel(logging.DEBUG)

    loki_h = RFH(str(loki_log_path), maxBytes=max_bytes,
                 backupCount=backup_count, encoding="utf-8")
    loki_fmt = Logfmter(
        keys=["ts", "level", "logger", "msg"],
        mapping={"ts":"asctime", "level":"levelname",
                 "logger":"name", "msg":"message"}
    )
    loki_h.setFormatter(loki_fmt)



    # --- console + application.log ---
    console_h = logging.StreamHandler()
    console_fmt = "%(asctime)s [FULL] %(levelname)s: %(message)s"
    console_h.setFormatter(logging.Formatter(console_fmt, "%Y-%m-%d %H:%M:%S"))
    root.addHandler(console_h)
    root.addHandler(loki_h)
    app_h = RotatingFileHandler(console_log_path, maxBytes=max_bytes,
                                backupCount=backup_count, encoding="utf-8")
    app_h.setFormatter(logging.Formatter(console_fmt, "%Y-%m-%d %H:%M:%S"))
    root.addHandler(app_h)

    # --- FULL ---
    full_h = RotatingFileHandler(full_log_path, maxBytes=max_bytes,
                                 backupCount=backup_count, encoding="utf-8")
    full_h.setFormatter(logging.Formatter(console_fmt, "%Y-%m-%d %H:%M:%S"))

    full_logger = logging.getLogger("FULL")
    full_logger.setLevel(logging.DEBUG)
    full_logger.addHandler(full_h)  # пишем напрямую
    full_logger.addHandler(loki_h)
    # --- IMPORTANT ---
    imp_h = RotatingFileHandler(important_log_path, maxBytes=max_bytes,
                                backupCount=backup_count, encoding="utf-8")
    imp_h.setFormatter(logging.Formatter(
        "%(asctime)s [IMPORTANT] %(message)s", "%Y-%m-%d %H:%M:%S"
    ))

    important_logger = logging.getLogger("IMPORTANT")
    important_logger.setLevel(logging.INFO)
    important_logger.addHandler(imp_h)
    important_logger.propagate = False

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

def get_loki_logger(
    path: Path = Path("logs/loki.log"),
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> Logger:
    """
    Логгер с logfmt‑форматом для отдачи в Loki/Promtail.
    """
    lg = logging.getLogger("LOKI")
    if lg.handlers:
        return lg                        # уже настроен

    lg.setLevel(logging.INFO)

    # 1. формат logfmt: ts=… level=… logger=… msg="…"
    fmt = Logfmter(
        keys=["ts", "level", "logger", "msg"],
        mapping={
            "ts":     "asctime",
            "level":  "levelname",
            "logger": "name",
            "msg":    "message",
        }
    )

    # 2. многопроцессная ротация
    h = RFH(str(path), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    h.setFormatter(fmt)
    lg.addHandler(h)

    # Чтобы не дублировать в root
    lg.propagate = False
    return lg



def loki_handler() -> logging.Handler:
    global _loki_handler
    if _loki_handler is None:
        from logfmter import Logfmter
        from concurrent_log_handler import ConcurrentRotatingFileHandler as RFH

        fmt = Logfmter(keys=["ts", "level", "logger", "msg"],
                       mapping={"ts": "asctime",
                                "level": "levelname",
                                "logger": "name",
                                "msg": "message"})
        _loki_handler = RFH(str(Path("logs/loki.log")),
                            maxBytes=5*1024*1024, backupCount=3,
                            encoding="utf-8")
        _loki_handler.setFormatter(fmt)
    return _loki_handler