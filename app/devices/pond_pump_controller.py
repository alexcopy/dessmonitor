# app/devices/pond_pump_controller.py
import logging
from bisect import bisect_left
from pathlib import Path
from typing import Optional

from app.devices.relay_channel_device import RelayChannelDevice
from app.logger import add_file_logger
from shared_state.shared_state import shared_state


class PondPumpController:
    """
    Определяет целевую скорость насоса по температуре и напряжению инвертора.

    Температура берётся из shared_state['ambient_temp'] (датчик или OpenWeatherMap).
    """

    def __init__(self) -> None:
        log_path = Path("logs/pump_controller.log")
        self._logger = add_file_logger(
            name="PondPumpController",
            path=log_path,
            level=logging.DEBUG,
            fmt="%(asctime)s %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        self._logger.info("=== PondPumpController initialized ===")

    # ───────────────────── минимальная скорость ──────────────────────
    async def calc_min_speed(self, dev: RelayChannelDevice) -> int:
        """
        По таблице temperature→min_speed выбираем минималку.
        dev.extra['weather'] должно быть { -4:0, 12:20, … }.
        Температура из shared_state['ambient_temp'].
        """
        tbl: dict = dev.extra.get("weather", {})
        default_min = int(dev.extra.get("min_speed", 20))

        # Берём температуру из shared_state (OpenWeatherService пишет туда)
        temp: Optional[float] = shared_state.get("ambient_temp")

        if temp is None:
            self._logger.warning(
                f"[MinSpeed] No ambient_temp in shared_state → default {default_min}%"
            )
            return default_min

        self._logger.info(f"[Temp] ambient_temp → {temp}°C")

        # Готовим sorted-список узлов
        keys = sorted(int(k) for k in tbl.keys())
        speeds = {int(k): int(v) for k, v in tbl.items()}

        # Если ниже минимума
        if temp <= keys[0]:
            ms = speeds[keys[0]]
            self._logger.info(f"[MinSpeed] {dev.name}: T={temp:.1f}°C ≤ {keys[0]} → {ms}%")
            return ms

        # Если выше максимума
        if temp >= keys[-1]:
            ms = speeds[keys[-1]]
            self._logger.info(f"[MinSpeed] {dev.name}: T={temp:.1f}°C ≥ {keys[-1]} → {ms}%")
            return ms

        # Иначе ищем предыдущий узел
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
        """
        Определяет целевую скорость насоса.

        Returns:
            None - не менять, int - новая скорость
        """
        # Выравниваем текущий P по шагу
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

        # 1) Инвертор от сети → min_speed
        if not inverter_on:
            target = min_speed if cur != min_speed else None
            self._logger.info(f"[Decide] inverter_on=False → target={target}")
            return target

        # 2) Зелёная зона → ничего не делаем
        if min_v <= inverter_voltage <= max_v:
            self._logger.info("[Decide] in green zone → no change")
            return None

        # 3) Высокое напряжение → inc
        if inverter_voltage > max_v and cur < max_speed:
            new_p = self._inc(cur, step, max_speed)
            self._logger.info(f"[Decide] high Vbat → {cur}% → {new_p}%")
            return new_p

        # 4) Низкое напряжение → dec
        if inverter_voltage < min_v and cur > min_speed:
            new_p = self._dec(cur, step, min_speed)
            self._logger.info(f"[Decide] low Vbat → {cur}% → {new_p}%")
            return new_p

        self._logger.info("[Decide] no rule matched → no change")
        return None

    @staticmethod
    def _round_to_step(value: int, step: int) -> int:
        """Округляет value к ближайшему кратному step (минимум = step)."""
        if step <= 0:
            return value
        rounded = round(value / step) * step
        return max(step, int(rounded))