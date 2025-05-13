import logging
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any, Dict

from app.devices.pump_power_map import PUMP_W_MAP

# Типы «аналоговых» устройств, для которых аптайм не считаем
ANALOG_TYPES = {"watertemp", "water_thermo", "thermo", "thermometer", "termo", "termo_sensor", "temp_sensor"}

@dataclass
class RelayChannelDevice:
    id: str
    name: str
    desc: str
    tuya_device_id: str
    device_type: str
    available: bool
    min_volt: float
    max_volt: float
    priority: int
    control_key: str
    time_delay: int = 120
    coefficient: float = 1.0
    api_key: str = None
    load_in_wt: int = 0
    status: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=lambda: {'switch_time': 10, 'min_trashhold': 0})
    is_healthy: bool = True
    inverter_is_on: bool = False
    inverter_voltage: float = 0.0
    state_key: str = None
    last_switched: int = field(default_factory=lambda: int(datetime.now().timestamp()))
    last_tick_ts: int = field(default_factory=lambda: int(datetime.now().timestamp()))
    today_run_sec: int = 0
    today_for_date: date = field(default_factory=date.today)
    logger: logging.Logger = field(
        init=False,  # не требует аргумента при создании объекта
        repr=False,  # не выводится в __repr__
        default=logging.getLogger("DeviceCore")
    )
    today_wh: float = 0.0
    def __post_init__(self):
        #  ← если в YAML ещё лежит api_key / api_sw
        if not self.control_key and self.api_key:
            self.control_key = self.api_key

        if self.state_key is None:  # если не задан — читаем из control_key
            self.state_key = self.control_key


    def get_min_volt(self) -> float:
        return float(self.min_volt)

    def get_max_volt(self) -> float:
        return float(self.max_volt)

    def can_switch(self) -> bool:
        delta = int(datetime.now().timestamp()) - self.last_switched
        if delta < self.time_delay:
            self.logger.info(
                "%s: cannot_switch wait=%ss",
                self.name, self.time_delay - delta,
                extra={"type": "device", "dev": self.name, "evt": "cannot_switch"},
            )
            return False
        return True

    def get_device_type(self):
        return self.device_type.upper()

    def to_bool(self, val) -> bool:
        if isinstance(val, bool):
            return val
        if isinstance(val, int):
            return val == 1
        if isinstance(val, str):
            return val.strip().lower() in ["1", "true", "yes", "on"]
        return False

    def is_device_on(self) -> bool:
        if self.state_key is None:  # always-ON датчик
            return True
        return self.to_bool(self.status.get(self.state_key))

    def update_status(self, new_status: Dict[str, Any]):
        self.status.update(new_status)

    def mark_switched(self):
        self.last_switched = int(datetime.now().timestamp())

    def ready_to_switch_on(self, inverter_voltage: float) -> bool:
        if self.is_device_on():
            logging.debug(f"[{self.name}] Already ON")
            return False
        if not self.can_switch():
            return False
        return inverter_voltage > self.max_volt

    def ready_to_switch_off(self, inverter_voltage: float, inverter_is_on: bool) -> bool:
        if not self.is_device_on():
            logging.debug(f"[{self.name}] Already OFF")
            return False
        if not inverter_is_on:
            return True
        if inverter_voltage < float(self.extra.get("min_trashhold", 0)):
            return True
        if not self.can_switch():
            return False
        return inverter_voltage < self.min_volt

    def extract_status(self, raw_status: list) -> Dict[str, Any]:
        parsed = {item['code']: item['value'] for item in raw_status}
        parsed.setdefault('switch_1', int(parsed.get('Power', 0)))
        parsed.update({
            'status': int(parsed.get('switch_1', 0)),
            't': int(datetime.now().timestamp()),
            'device_id': self.id,
            'success': True
        })
        return parsed

    @staticmethod
    def _format_duration(sec: int) -> str:
        """
        Форматирует секунды в человекочитаемый вид:
        h h m m s s
        """
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h {m}m"
        if m:
            return f"{m}m"
        return f"{s}s"
    def get_uptime_sec(self) -> int:
        """
        Возвращает число секунд, которое устройство было включено
        с момента последнего переключения ON.
        """
        if not self.is_device_on():
            return 0
        return int(datetime.now().timestamp()) - self.last_switched


    def uptime_str(self) -> str:
        """
        Строковое представление аптайма (или "OFF").
        """
        if not self.is_device_on():
            return "OFF"
        return self._format_duration(self.get_uptime_sec())

    def log_uptime(self, logger: logging.Logger) -> None:
        """
        Записывает в переданный logger аптайм этого устройства,
        пропуская аналоговые.
        """
        if self.device_type.lower() in ANALOG_TYPES:
            return
        logger.info(f"{self.name}: uptime={self.uptime_str()}")

    # ───────── RelayChannelDevice ──────────────────────────────
    def power_consumption(self) -> float:
        if self.device_type.lower() == "pump":
            p = self.status.get("P")
            if isinstance(p, (int, float)):
                return self._pump_w_from_p(p)
        return float(self.load_in_wt or 0)  # реле / датчик

    def _pump_w_from_p(self, p_val: float) -> float:
        """Интерполяция по таблице P→Вт (PUMP_W_MAP)."""
        p_int = int(round(p_val))
        if p_int in PUMP_W_MAP:
            return float(PUMP_W_MAP[p_int])

        keys = sorted(PUMP_W_MAP)  # [10, 20, … 100]
        idx = bisect_left(keys, p_int)

        if 0 < idx < len(keys):
            k1, k2 = keys[idx - 1], keys[idx]
            w1, w2 = PUMP_W_MAP[k1], PUMP_W_MAP[k2]
            return w1 + (w2 - w1) * (p_int - k1) / (k2 - k1)

        edge = keys[0] if idx == 0 else keys[-1]
        return float(PUMP_W_MAP[edge])

    def get_id(self) -> str:
        return self.id

    def get_name(self) -> str:
        return self.name

    def get_desc(self) -> str:
        return self.desc

    def get_api_sw(self) -> str:
        return self.api_key

    def get_coefficient(self) -> float:
        return self.coefficient

    def get_priority(self) -> int:
        return self.priority

    def get_status(self, key: str = None):
        if key is None:
            return self.status
        return self.status.get(key)

    def get_extra(self, key: str):
        return self.extra.get(key)

    def update_extra(self, key: str, value: Any):
        self.extra[key] = value

    def set_on(self):
        self.update_status({self.control_key: True})
        self.mark_switched()

    def set_off(self):
        self.update_status({self.control_key: False})
        self.mark_switched()

    def tuya_code_mode(self) -> str:
        # вернёт либо то, что пришло в extra, либо 'mode'
        return self.extra.get("mode_code", "mode")

    def tuya_code_speed(self) -> str:
        # вернёт либо то, что пришло в extra, либо 'P'
        return self.extra.get("p_code", "P")
    # ------------------------------------------------------------ helpers
    def _reset_daily_counters_if_needed(self, ts: int):
        now_d = datetime.fromtimestamp(ts).date()
        if now_d != self.today_for_date:  # новый день – обнуляем
            self.today_for_date = now_d
            self.today_run_sec = 0


    def _current_power_w(self) -> float:
        """
        Возвращает текущую мощность устройства, Вт.

        •  Насос → по таблице `PUMP_W_MAP` (интерполируем в _pump_w_from_p).
        •  Любое другое устройство → `load_in_wt` из YAML
          (если поле не задано — 0).
        """
        if self.device_type.lower() == "pump":
            p_val = self.status.get("P")
            if isinstance(p_val, (int, float)):
                return self._pump_w_from_p(p_val)

        # обычные реле / датчики
        return float(self.load_in_wt or 0.0)

    def tick(self, ts: int) -> None:
        # — обнуляем, если наступил новый день —
        if date.fromtimestamp(ts) != self.today_for_date:
            self.today_for_date = date.fromtimestamp(ts)
            self.today_wh = 0.0

        elapsed = ts - self.last_tick_ts
        if elapsed > 0 and self.is_device_on():
            # 1) берём мощность **на начало интервала**
            pw = self._current_power_w()
            self.today_wh += pw * (elapsed / 3600)  # Wh = P * t[h]
        self.logger.debug(  # ← DEBUG, чтобы легко выключить
            "energy_tick %s",  # строка‑шаблон
            self.name,  # ← подставляем имя устройства
            extra={
                "evt": "energy_tick",
                "dev": self.name,  # label для Loki/Grafana
                "type": self.device_type.lower(),
                "wh": self.today_wh
            },
        )
        self.last_tick_ts = ts

    @property
    def today_kwh(self) -> float:
        # Wh = P * t[h]; kWh = Wh / 1000
        return (self._current_power_w() * (self.today_run_sec / 3600)) / 1000
