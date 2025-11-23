import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from colorama import Fore, Style, init
from app.devices.pump_power_map import PRESET_DESCR
from app.devices.relay_channel_device import RelayChannelDevice
from app.logger import add_file_logger, loki_handler, get_loki_logger
from shared_state.shared_state import shared_state

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

    def _mk_logger(self, name: str, path: Path, level=logging.INFO) -> logging.Logger:
        """
        Возвращает логгер с 2‑мя хэндлерами:
          • rotating‑файл (читаемый для людей)
          • loki.log(logfmt для Grafana)
        """
        lg = add_file_logger(name=name,
                             path=path,
                             level=level,
                             fmt="%(asctime)s %(levelname)s: %(message)s")
        # Не дублируем, если Loki‑хэндлер уже подвешен
        if loki_handler() not in lg.handlers:
            lg.addHandler(loki_handler())

        return lg

    @staticmethod
    def _fmt_duration(sec: int) -> str:
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        if h: return f"{h} h {m} m"
        if m: return f"{m} m"
        return f"{s}s"

    @staticmethod
    def _format_duration(sec: int) -> str:
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h {m}m"
        if m:
            return f"{m}m"
        return f"{s}s"
    # ─────────────────── snapshot (вертикальная таблица) ──────────────────
    # -----------------------------------------------------------------
    def log_snapshot(self, devices: Sequence[RelayChannelDevice]) -> None:
        if not devices:
            return

        now  = int(datetime.now().timestamp())
        rows: list[str] = []

        # ───────────────────── «шапка» с данными инвертора ──────────────────
        inv_v   = shared_state.get("battery_voltage")   # None, если ещё нет
        inv_mode= shared_state.get("working_mode")
        pv_pow  = shared_state.get("pv_power")
        load_pr = shared_state.get("load_percent")

        head_parts: list[str] = []
        if inv_v      is not None: head_parts.append(f"Batt {inv_v:.1f} V")
        if inv_mode:             head_parts.append(inv_mode)
        if pv_pow     is not None: head_parts.append(f"PV {pv_pow:.0f} W")
        if load_pr    is not None: head_parts.append(f"Load {load_pr:.0f}%")

        if head_parts:
            rows.append(" / ".join(head_parts))

        # ───────────────────── перечень устройств ──────────────────────────
        for d in sorted(devices, key=lambda x: x.priority):
            dtype  = d.device_type.lower()
            energy = f"{d.today_kwh:.2f} kWh"

            # ---- дискретные (реле / насос) --------------------------------
            if dtype not in self.ANALOG_TYPES:
                state = "ON" if d.is_device_on() else "OFF"
                # Суточный аптайм (обнуляется каждый день)
                up = self._format_duration(d.today_run_sec) if d.today_run_sec else "—"
                rows.append(
                    f"{d.name:<15} | {state:<3} | run={self._format_duration(d.today_run_sec):<8} | "
                    f"cur-on={self._format_duration(now - d.last_switched) if d.is_device_on() else '—':<8} | "
                    f"day-energy={d.today_kwh:.2f} kWh"
                )
                continue

            # ---- аналоговые датчики ---------------------------------------
            temp_raw = d.status.get("temp_current")
            hum      = d.status.get("humidity_value")
            batt     = d.status.get("battery_percentage")
            amb = shared_state.get("ambient_temp")
            parts = []
            if temp_raw is not None: parts.append(f"T={temp_raw/10:.1f}°C")
            if hum      is not None: parts.append(f"H={hum}%")
            if batt     is not None: parts.append(f"B={batt}%")
            if amb     is not None: parts.append(f"ambient={amb}°C")

            rows.append(f"{d.name:<15} | {'; '.join(parts):<25} | day-energy={energy}")
        loki = get_loki_logger()
        now_ts = int(datetime.now().timestamp())

        for d in devices:
            if d.device_type.lower() in self.ANALOG_TYPES:
                continue
            uptime = now_ts - d.last_switched
            energy = d.today_kwh
            loki.info(
                "device_metrics",
                extra={
                    "type": "device_metrics",
                    "dev": d.name,
                    "run_sec": d.today_run_sec,  # Сколько работал за сутки (в секундах)
                    "uptime_sec": uptime,  # Сколько текущее включение (в секундах)
                    "day_kwh": round(d.today_kwh, 3),  # КВт⋅ч за день
                    "state": "ON" if d.is_device_on() else "OFF",
                }
            )

        # печатаем одним блоком — удобнее читать
        self._logger.info("\n" + "\n".join(rows))

    # ───────────────────────── детали + алармы ──────────────────────────
    def log_device_details(self, devices: Sequence[RelayChannelDevice]) -> None:
        for d in devices:
            dtype = d.device_type.lower()

            if dtype == "pump":
                self._handle_pump(d)
            elif dtype in self.ANALOG_TYPES:
                self._handle_thermo(d)
            else:
                # Логируем устройства с неизвестным типом для отладки
                if dtype and dtype not in ["relay", "switch", "inverter"]:
                    self._details_logger.debug(
                        f"[DEBUG] Unknown device type: {d.name} (type={dtype}), "
                        f"expected one of {self.ANALOG_TYPES}"
                    )

    # ---------------- PUMP ----------------
    def _handle_pump(self, d: RelayChannelDevice) -> None:
        power = "ON" if d.to_bool(d.status.get("Power")) else "OFF"
        p_val = d.status.get("P", "N/A")
        watts = d.status.get("power_show", "N/A")
        mode  = self._mode_name(d.status.get("mode"))
        preset = shared_state.get("pump_mode", 6)
        amb = shared_state.get("ambient_temp")
        descr = PRESET_DESCR.get(preset, "")
        self._details_logger.info(
            f"[PUMP] {d.name}: Power={power}, P={p_val}, Mode={preset} ({descr})"
        )
        state = (power, p_val, watts, mode)
        if self._last_pump_state.get(d.id) != state:
            msg = f"[PUMP] {d.name}: Power={power}, P={p_val}, W={watts}, Mode={mode}, AMB={amb}"
            self._details_logger.info(msg)
            if self._important.handlers:
                self._important.info(msg)
            self._last_pump_state[d.id] = state

    # --------------- THERMO ---------------
    def _handle_thermo(self, d: RelayChannelDevice) -> None:
        # Debug логирование через logger вместо print
        self._details_logger.debug(f"[THERMO] Processing device: {d.name} (id={d.id}, type={d.device_type})")

        raw_t = d.status.get("temp_current")

        if raw_t is None:
            # Температура не найдена - логируем доступные ключи
            self._details_logger.warning(
                f"[THERMO] {d.name}: temp_current not found! "
                f"Available keys: {list(d.status.keys())}"
            )

        temp  = raw_t/10 if raw_t is not None else None
        hum   = d.status.get("humidity_value")
        batt  = d.status.get("battery_percentage")
        # Различаем датчик воды и воздуха
        if d.get_name() in ["watertemp", "pondtemp"]:
            # Записываем только если значение не None (сохраняем последнее известное)
            if temp is not None:
                shared_state["water_temp"] = temp

            amb = shared_state.get("ambient_temp")
            msg = f"[THERMO] {d.name}: Water={temp}°C, Humidity={hum}%, Battery={batt}%, Ambient={amb}°C"
        else:
            # Аналогично для ambient
            if temp is not None:
                shared_state["ambient_temp"] = temp
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
