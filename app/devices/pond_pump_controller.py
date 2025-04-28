import asyncio, logging, time
from datetime import datetime
from typing   import Optional

from app.devices.relay_channel_device import RelayChannelDevice


class PondPumpController:
    """Считает целевую скорость насоса (поле `P`) без суточных коррекций."""

    def __init__(self):
        # кеш погоды – чтобы не бомбить API
        self._weather     = {}          # town -> dict(...)
        self._weather_ts  = {}          # town -> unix-ts
        self._weather_ttl = 30 * 60     # 30 мин

    # ───────────────────────────── погодный минимум ────────────────── #
    async def _fetch_weather(self, town: str) -> dict:
        import python_weather
        async with python_weather.Client(format=python_weather.METRIC) as client:
            w = await client.get(town)
        return {
            "temperature": int(w.current.temperature),
            "is_valid":   True
        }

    async def _get_weather(self, town: str) -> dict:
        now = int(time.time())
        if town in self._weather and now - self._weather_ts[town] < self._weather_ttl:
            return self._weather[town]

        try:
            data = await self._fetch_weather(town)
        except Exception as e:
            logging.warning(f"[Pump] weather for '{town}' failed: {e}")
            data = {"temperature": 10, "is_valid": False}

        self._weather[town]    = data
        self._weather_ts[town] = now
        return data

    async def calc_min_speed(self, dev: RelayChannelDevice) -> int:
        """Минимально допустимая скорость из таблицы temp→speed."""
        table = dev.extra.get("weather", {})          # { "10": 20, … }
        town  = dev.extra.get("weather_town", "")
        w     = await self._get_weather(town)

        if not w.get("is_valid"):
            return dev.extra.get("min_speed", 20)

        t_curr   = w["temperature"]
        prev_spd = dev.extra.get("min_speed", 20)
        for t_lim in sorted(map(int, table.keys())):
            if t_curr < t_lim:
                return prev_spd
            prev_spd = int(table[str(t_lim)])
        return prev_spd

    # ───────────────────────────── speed helpers ───────────────────── #
    @staticmethod
    def _round_to_step(value: int, step: int) -> int:
        """Гарантируем кратность шагу."""
        return max(step, round(value / step) * step)

    def _inc(self, cur: int, step: int, max_s: int) -> int:
        return min(cur + step, max_s)

    def _dec(self, cur: int, step: int, min_s: int) -> int:
        return max(cur - step, min_s)

    # ───────────────────────────── публичный API ───────────────────── #
    async def deside_speed(
        self,
        dev: RelayChannelDevice,
        inverter_voltage: float,
        inverter_on: bool
    ) -> Optional[int]:
        """
        Возвращает **целевое** значение `P` либо None (оставить как есть).
        """
        cur       = int(dev.status.get("P", 0))
        step      = dev.extra.get("speed_step", 5)
        min_v     = dev.min_volt
        max_v     = dev.max_volt
        min_speed = await self.calc_min_speed(dev)
        max_speed = dev.extra.get("max_speed", 100)

        # Если инвертор выключен → минималка
        if not inverter_on:
            return min_speed if cur != min_speed else None

        # Напряжение в зеленой зоне → ничего не меняем
        if min_v <= inverter_voltage <= max_v:
            return None

        # Высокое напряжение → ускоряем
        if inverter_voltage > max_v and cur < max_speed:
            return self._inc(cur, step, max_speed)

        # Низкое напряжение → замедляем, но не ниже погодного минимума
        if inverter_voltage < min_v and cur > min_speed:
            return self._dec(cur, step, min_speed)

        return None
