# app/devices/pond_pump_controller.py
import asyncio
import logging
import time
from bisect import bisect_left
from typing import Optional

from shared_state.shared_state import shared_state
from app.devices.relay_channel_device import RelayChannelDevice


class PondPumpController:
    """
    Определяет целевую скорость насоса (код Tuya `P`).

    ① Сначала берём температуру из shared_state['ambient_temp']
       (кладётся апдейтером статусов с термодатчика).
    ② Если её нет – используем python_weather как резерв.
    ③ По таблице temp→speed подбираем минимальную допустимую
       скорость и дальше уже «шагаем» вверх/вниз в зависимости
       от напряжения аккумулятора.
    """

    _WEATHER_TTL = 30 * 60          # 30 мин  для fallback-погоды

    def __init__(self) -> None:
        self._cache: dict[str, tuple[dict, int]] = {}   # town -> (data, ts)

    # ─────────────────────── Weather fallback (резерв) ──────────────────────
    async def _fetch_weather(self, town: str) -> dict:
        import python_weather
        async with python_weather.Client(format=python_weather.METRIC) as client:
            w = await client.get(town)
        return {"temperature": int(w.current.temperature), "is_valid": True}

    async def _external_temp(self, town: str) -> Optional[float]:
        if not town:
            return None

        now = int(time.time())
        if town in self._cache and now - self._cache[town][1] < self._WEATHER_TTL:
            return self._cache[town][0]["temperature"]

        try:
            data = await self._fetch_weather(town)
            self._cache[town] = (data, now)
            return float(data["temperature"])
        except Exception as e:
            logging.warning(f"[Pump] fallback weather for '{town}' failed: {e}")
            return None

    # ───────────────────── минимальная скорость ──────────────────────
    async def calc_min_speed(self, dev: RelayChannelDevice) -> int:
        """Возвращает минимально допустимый P-speed по температуре."""
        tbl: dict[str, int] = dev.extra.get("weather", {})  # {'-4':0, '12':20…}
        default_min = dev.extra.get("min_speed", 20)

        # ① сначала пробуем собственный датчик
        temp: Optional[float] = shared_state.get("ambient_temp")

        # ② если его нет — внешний прогноз
        if temp is None:
            temp = await self._external_temp(dev.extra.get("weather_town", ""))

        if temp is None:  # совсем нет данных
            return default_min

        # ---------- готовим таблицу ----------
        keys = sorted(float(k) for k in tbl)  # [-40.0, -4.0, …]
        speeds = {float(k): int(v) for k, v in tbl.items()}

        # ниже минимума / выше максимума
        if temp <= keys[0]:
            return speeds[keys[0]]
        if temp >= keys[-1]:
            return speeds[keys[-1]]

        # ищем позицию и берём предыдущий узел
        idx = bisect_left(keys, temp)
        return speeds[keys[idx - 1]]

    # ─────────────────────── helpers для изменения P ───────────────────────
    @staticmethod
    def _inc(cur: int, step: int, max_s: int) -> int:
        return min(cur + step, max_s)

    @staticmethod
    def _dec(cur: int, step: int, min_s: int) -> int:
        return max(cur - step, min_s)

    # ─────────────────────── основной публичный метод ──────────────────────
    async def deside_speed(
        self,
        dev: RelayChannelDevice,
        inverter_voltage: float,
        inverter_on: bool
    ) -> Optional[int]:
        """
        Возвращает целевое значение `P` или None (ничего не менять).
        """
        cur        = int(dev.status.get("P", 0))
        step       = dev.extra.get("speed_step", 5)
        min_v, max_v = dev.min_volt, dev.max_volt
        min_speed  = await self.calc_min_speed(dev)
        max_speed  = dev.extra.get("max_speed", 100)

        # Инвертор питается от сети – ставим минималку
        if not inverter_on:
            return min_speed if cur != min_speed else None

        if min_v <= inverter_voltage <= max_v:
            return None                         # «зелёная» зона

        if inverter_voltage > max_v and cur < max_speed:
            return self._inc(cur, step, max_speed)

        if inverter_voltage < min_v and cur > min_speed:
            return self._dec(cur, step, min_speed)

        return None
