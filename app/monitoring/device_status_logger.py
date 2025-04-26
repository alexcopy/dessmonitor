import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from colorama import Fore, Style, init
from app.devices.relay_channel_device import RelayChannelDevice

init(autoreset=True)

class DeviceStatusLogger:
    LOG_PATH         = Path("logs/device_status.log")
    DETAILS_LOG_PATH = Path("logs/device_details.log")
    WARNING_LOG_PATH = Path("logs/device_warnings.log")

    # все типы, которые считаем «аналоговыми» (у них нет ON/OFF)
    ANALOG_TYPES = {
        "thermo", "thermometer", "termo", "termo_sensor", "temp_sensor",
        "watertemp", "water_thermo",
    }

    def __init__(self) -> None:
        self._logger         = self._mk_logger("DeviceStatus",        self.LOG_PATH)
        self._details_logger = self._mk_logger("DeviceStatusDetails", self.DETAILS_LOG_PATH)
        self._warning_logger = self._mk_logger("DeviceWarnings",      self.WARNING_LOG_PATH,
                                               level=logging.WARNING)
        self._important = logging.getLogger("IMPORTANT")

        self._last_pump_state:   dict[str, tuple] = {}
        self._last_thermo_state: dict[str, tuple] = {}

    # ───────────────────────── helpers ─────────────────────────
    @staticmethod
    def _mk_logger(name: str, path: Path, level=logging.INFO) -> logging.Logger:
        lg = logging.getLogger(name)
        lg.setLevel(level)
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s",
                                          "%Y-%m-%d %H:%M:%S"))
        lg.addHandler(fh)
        return lg

    @staticmethod
    def _fmt_duration(sec: int) -> str:
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        if h: return f"{h} h {m} m"
        if m: return f"{m} m"
        return f"{s}s"

    # ─────────────────── snapshot (вертикальная таблица) ──────────────────
    def log_snapshot(self, devices: Sequence[RelayChannelDevice]) -> None:
        if not devices:
            return

        now  = int(datetime.now().timestamp())
        rows = []

        for d in sorted(devices, key=lambda x: x.priority):
            dtype = d.device_type.lower()
            energy = f"{d.today_kwh:.2f} kWh"

            # ----- дискретные -----
            if dtype not in self.ANALOG_TYPES:
                state  = "ON" if d.is_device_on() else "OFF"
                up     = self._fmt_duration(now - d.last_switched) if d.is_device_on() else "—"
                rows.append(f"{d.name:<15} | {state:<3} | run={up:<8} | day-energy={energy}")
                continue

            # ----- аналоговые -----
            temp_raw = d.status.get("temp_current")
            hum      = d.status.get("humidity_value")
            batt     = d.status.get("battery_percentage")

            parts = []
            if temp_raw is not None: parts.append(f"T={temp_raw/10:.1f}°C")
            if hum      is not None: parts.append(f"H={hum}%")
            if batt     is not None: parts.append(f"B={batt}%")

            rows.append(f"{d.name:<15} | {'; '.join(parts):<25} | day-energy={energy}")

        self._logger.info("\n" + "\n".join(rows))

    # ───────────────────────── детали + алармы ──────────────────────────
    def log_device_details(self, devices: Sequence[RelayChannelDevice]) -> None:
        for d in devices:
            dtype = d.device_type.lower()

            if dtype == "pump":
                self._handle_pump(d)
            elif dtype in self.ANALOG_TYPES:
                self._handle_thermo(d)

    # ---------------- PUMP ----------------
    def _handle_pump(self, d: RelayChannelDevice) -> None:
        power = "ON" if d.to_bool(d.status.get("Power")) else "OFF"
        p_val = d.status.get("P", "N/A")
        watts = d.status.get("power_show", "N/A")
        mode  = self._mode_name(d.status.get("mode"))

        state = (power, p_val, watts, mode)
        if self._last_pump_state.get(d.id) != state:
            msg = f"[PUMP] {d.name}: Power={power}, P={p_val}, W={watts}, Mode={mode}"
            self._details_logger.info(msg)
            if self._important.handlers:
                self._important.info(msg)
            self._last_pump_state[d.id] = state

    # --------------- THERMO ---------------
    def _handle_thermo(self, d: RelayChannelDevice) -> None:
        raw_t = d.status.get("temp_current")
        temp  = raw_t/10 if raw_t is not None else None
        hum   = d.status.get("humidity_value")
        batt  = d.status.get("battery_percentage")

        msg = f"[THERMO] {d.name}: Temp={temp}°C, Humidity={hum}%, Battery={batt}%"
        self._details_logger.info(msg)

        state = (temp, hum, batt)
        if self._last_thermo_state.get(d.id) != state:
            if self._important.handlers:
                self._important.info(msg)
            self._last_thermo_state[d.id] = state

        # предупреждения
        if temp is not None and temp > 30:
            w = f"[{d.name}] ⚠ Температура высокая: {temp}°C"
            self._warning_logger.warning(w)
            print(Fore.RED + w + Style.RESET_ALL)

        if isinstance(batt, int) and batt < 20:
            w = f"[{d.name}] 🔋 Батарея низкая: {batt}%"
            self._warning_logger.warning(w)
            print(Fore.YELLOW + w + Style.RESET_ALL)

    # ---------------- helpers ----------------
    @staticmethod
    def _mode_name(code: Any) -> str:
        return {
            "0": "manual", "1": "custom", "2": "day_night",
            "3": "spring_autumn", "4": "summer", "5": "winter", "6": "manual"
        }.get(str(code), str(code))
