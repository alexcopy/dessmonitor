import logging
import time
from pathlib import Path
from logging import Handler, Logger


class CustomLogHandler(Handler):
    """
    Общий handler: печать в консоль + запись в файл.
    Имя канала пишется в квадратных скобках.
    """
    def __init__(self, path: Path, ch_name: str):
        super().__init__()
        self._file = open(path, "a", buffering=1, encoding="utf-8")
        self._ch_name = ch_name.upper()

    # ──────────────────────────────────────────────────────────
    def emit(self, record):
        msg = self.format(record)
        ts  = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} [{self._ch_name}] {msg}"
        print(line)
        self._file.write(line + "\n")

    def close(self):
        self._file.close()
        super().close()


# ──────────────────────────────────────────────────────────────
def setup_logging(log_path: str = "logs/application.log"
                  ) -> tuple[Logger, Logger]:
    """
    • Настраиваем корневой логгер (DEBUG) → файл+stdout
    • Создаём два дочерних логгера: FULL и IMPORTANT
      - FULL пишет ВСЁ (DEBUG+)
      - IMPORTANT пишет только INFO+ и не засоряет stdout библиотеки Tuya
    • Возвращаем именно **логгеры**, не handlers!
    """
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # общий handler
    common_h = CustomLogHandler(Path(log_path), "FULL")
    common_h.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(common_h)

    # — FULL —
    full_logger = logging.getLogger("FULL")
    full_logger.propagate = True     # bubbling в root

    # — IMPORTANT —
    important_logger = logging.getLogger("IMPORTANT")
    important_logger.setLevel(logging.INFO)
    important_logger.propagate = True

    return full_logger, important_logger

def add_file_logger(name: str, path: str or Path,
                    level: int = logging.INFO,
                    fmt: str = "%(asctime)s %(message)s",
                    datefmt: str = "%Y-%m-%d %H:%M:%S") -> logging.Logger:
    """
    Создаёт (или возвращает уже созданный) файловый логгер `name`,
    пишет в `path`. Используется всеми «служебными» логгерами проекта.
    """
    path = Path(path)
    path.parent.mkdir(exist_ok=True)

    lg = logging.getLogger(name)
    if lg.handlers:                           # уже настроен – вернём как есть
        return lg

    lg.setLevel(level)
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(logging.Formatter(fmt, datefmt))
    lg.addHandler(fh)
    return lg