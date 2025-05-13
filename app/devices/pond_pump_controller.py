# app/devices/pond_pump_controller.py
import logging
import time
from bisect import bisect_left
from pathlib import Path
from typing import Optional, Dict, Tuple

from app.devices.relay_channel_device import RelayChannelDevice
from app.logger import add_file_logger
from shared_state.shared_state import shared_state


class PondPumpController:
    """
    Определяет целевую скорость насоса (код Tuya `P`) и логирует все шаги.

    1) Сначала пробует температуру из shared_state['ambient_temp'] (датчик).
    2) Если её нет — падает на внешний прогноз (python_weather) с TTL=30 мин.
    3) По таблице temp→speed подбирает минималку, а дальше «шагает»
       вверх/вниз в зависимости от напряжения инвертора.
    """

    _WEATHER_TTL = 30 * 60  # 30 минут

    def __init__(self) -> None:
        # кеш для внешней погоды: town -> (data_dict, timestamp)
        self._cache: Dict[str, Tuple[dict, int]] = {}

        # свой файловый лог
        log_path = Path("logs/pump_controller.log")
        self._logger = add_file_logger(
            name="PondPumpController",
            path=log_path,
            level=logging.DEBUG,
            fmt="%(asctime)s %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        self._logger.info("=== PondPumpController initialized ===")

    # ─────────────────────── Weather fallback ────────────────────────
    async def _fetch_weather(self, town: str) -> dict:
        import python_weather
        async with python_weather.Client(format=python_weather.METRIC) as client:
            w = await client.get(town)
        return {"temperature": float(w.current.temperature), "is_valid": True}

    async def _external_temp(self, town: str) -> Optional[float]:
        """Если в shared_state нет, пытаемся внешний API с кешем."""
        if not town:
            return None

        now = int(time.time())
        if town in self._cache and now - self._cache[town][1] < self._WEATHER_TTL:
            temp = self._cache[town][0]["temperature"]
            self._logger.info(f"[Weather-cache] {town} → {temp}°C")
            return temp

        try:
            data = await self._fetch_weather(town)
            self._cache[town] = (data, now)
            self._logger.info(f"[Weather] fetched {town} → {data['temperature']}°C")
            return data["temperature"]
        except Exception as e:
            self._logger.warning(f"[Weather] fallback for '{town}' failed: {e}")
            return None

    # ───────────────────── минимальная скорость ──────────────────────
    async def calc_min_speed(self, dev: RelayChannelDevice) -> int:
        """
        По таблице temperature→min_speed выбираем минималку.
        dev.extra['weather'] должно быть что-то типа { -4:0, 12:20, … }.
        """
        tbl: dict = dev.extra.get("weather", {})
        default_min = int(dev.extra.get("min_speed", 20))

        # 1) датчик:
        temp: Optional[float] = shared_state.get("ambient_temp")
        if temp is not None:
            self._logger.info(f"[Temp] sensor → {temp}°C")
        else:
            # 2) fallback
            temp = await self._external_temp(dev.extra.get("weather_town", ""))
        if temp is None:
            self._logger.warning(f"[MinSpeed] no temp data → default {default_min}")
            return default_min

        # готовим sorted-список узлов:
        # ключи могут быть str или int
        keys = sorted(int(k) for k in tbl.keys())
        speeds = {int(k): int(v) for k, v in tbl.items()}

        # если ниже минимума:
        if temp <= keys[0]:
            ms = speeds[keys[0]]
            self._logger.info(f"[MinSpeed] {dev.name}: T={temp:.1f}°C ≤ {keys[0]} → {ms}%")
            return ms
        # если выше максимума:
        if temp >= keys[-1]:
            ms = speeds[keys[-1]]
            self._logger.info(f"[MinSpeed] {dev.name}: T={temp:.1f}°C ≥ {keys[-1]} → {ms}%")
            return ms

        # иначе ищем предыдущий узел
        idx = bisect_left(keys, int(temp))
        prev_k = keys[idx - 1]
        ms = speeds[prev_k]
        self._logger.info(
            f"[MinSpeed] {dev.name}: {prev_k}°C < T={temp:.1f}°C < {keys[idx]}°C → {ms}%"
        )
        return ms

    # ─────────────────────── helpers для P ─────────────────────────
    @staticmethod
    def _inc(cur: int, step: int, max_s: int) -> int:
        return min(cur + step, max_s)

    @staticmethod
    def _dec(cur: int, step: int, min_s: int) -> int:
        return max(cur - step, min_s)

    async def deside_speed(
            self,
            dev: RelayChannelDevice,
            inverter_voltage: float,
            inverter_on: bool
    ) -> Optional[int]:
        # 0) выравниваем текущий P по шагу
        raw_cur = int(dev.status.get("P", 0))
        step = int(dev.extra.get("speed_step", 5))
        cur = self._round_to_step(raw_cur, step)
        if cur != raw_cur:
            self._logger.info(f"[Round] {dev.name}: {raw_cur}% → {cur}% (step={step})")

        min_v = float(dev.min_volt)
        max_v = float(dev.max_volt)
        min_speed = await self.calc_min_speed(dev)
        max_speed = int(dev.extra.get("max_speed", 100))

        self._logger.info(
            f"[Decide] {dev.name}: cur={cur}%, step={step}%, "
            f"Vbat={inverter_voltage:.2f}V, inv_on={inverter_on}, "
            f"zone=[{min_v:.2f}–{max_v:.2f}], min_sp={min_speed}%, max_sp={max_speed}%"
        )

        # дальше — та же логика, только вместо raw_cur используем cur
        # 1) инвертор от сети → min_speed
        if not inverter_on:
            target = min_speed if cur != min_speed else None
            self._logger.info(f"[Decide] inverter_on=False → target={target}")
            return target

        # 2) зелёная зона → ничего не делаем
        if min_v <= inverter_voltage <= max_v:
            self._logger.info("[Decide] in green zone → no change")
            return None

        # 3) высокое напряжение → inc
        if inverter_voltage > max_v and cur < max_speed:
            new_p = self._inc(cur, step, max_speed)
            self._logger.info(f"[Decide] high Vbat → {cur}% → {new_p}%")
            return new_p

        # 4) низкое напряжение → dec
        if inverter_voltage < min_v and cur > min_speed:
            new_p = self._dec(cur, step, min_speed)
            self._logger.info(f"[Decide] low Vbat → {cur}% → {new_p}%")
            return new_p

        self._logger.info("[Decide] no rule matched → no change")
        return None

    @staticmethod
    def _round_to_step(value: int, step: int) -> int:
        """
        Округляет value к ближайшему кратному step (минимум = step).
        Пример: value=38, step=10 → 40; value=38, step=5 → 40; value=12, step=5 → 10.
        """
        if step <= 0:
            return value
        # ближайшее целое: round(value/step)*step
        rounded = round(value / step) * step
        # не опускаемся ниже одного шага
        return max(step, int(rounded))
