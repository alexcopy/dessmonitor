# app/service/inverter_monitor.py
import asyncio, logging
from app.api import DessAPI, DeviceData
from app.monitoring.inverter_logger import InverterLogger
from shared_state.shared_state import shared_state


class InverterMonitor:
    def __init__(self, dess_api: DessAPI, poll_sec: int = 60):
        self.api       = dess_api
        self.interval  = poll_sec
        self.logger    = InverterLogger()
        self._stop     = asyncio.Event()

    async def run(self) -> None:
        """
        Периодически (каждые `self.interval` сек) опрашивает Dess-инвертор
        и складывает результат в InverterLogger.
        Завершается, когда в `_stop` установят событие.
        """
        loop = asyncio.get_running_loop()

        while not self._stop.is_set():
            started = loop.time()  # момент начала итерации

            # ───────── попытка опросить и залогировать ─────────
            try:
                dd = await asyncio.to_thread(self.api.fetch_device_data)
                self.logger.log(dd)  # logs/inverter.log
                self._process_business_metrics(dd)  # ваши доп-метрики
            except Exception as exc:
                logging.getLogger("IMPORTANT").warning(
                    f"[INV_MON] fetch failed: {exc}"
                )

            # ───────── досыпаем до полного интервала ───────────
            elapsed = loop.time() - started
            remaining = self.interval - elapsed
            if remaining > 0:
                try:
                    # ждём либо наступления remaining, либо _stop.set()
                    await asyncio.wait_for(self._stop.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    # время вышло – начинаем новую итерацию
                    pass

    def stop(self):
        self._stop.set()

    def _process_business_metrics(self, dd: DeviceData) -> None:
        """
        Сохраняем актуальные значения в shared_state.
        Теперь ими могут пользоваться другие подсистемы
        (например, логика включения реле по напряжению батареи).
        """
        shared_state.update(
            battery_voltage=dd.battery_voltage,
            battery_soc=dd.battery_capacity,
            battery_current_chg=dd.battery_charging_current,
            battery_current_dis=dd.battery_discharging_current,
            pv1_voltage=dd.pv1_voltage,
            pv1_power=dd.pv1_power,
            pv2_voltage=dd.pv2_voltage,
            pv2_power=dd.pv2_power,
            pv_total_power=dd.pv_total_power,
            output_voltage=dd.output_voltage,
            output_power=dd.output_power,
            ac_input_voltage=dd.ac_input_voltage,
            ac_input_frequency=dd.ac_input_frequency,
            ac_output_load=dd.ac_output_load,
            working_mode=dd.working_state,
            mains_status=dd.mains_status,
            timestamp=dd.timestamp,
            inverter_summary=dd.summary(),  # красивая строка для UI
        )
