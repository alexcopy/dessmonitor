import asyncio
import logging
import time
from datetime import datetime

from app.devices.relay_channel_device import RelayChannelDevice

DAY_TIME_COMPENSATE = 1.5


class PondPumpController:
    def __init__(self):
        self.weather = None
        self._min_speed = {'min_speed': 10, 'timestamp': int(time.time())}

    def update_weather(self, weather_town: str):
        try:
            self.weather = self._get_weather_data(weather_town)
        except Exception as e:
            logging.error(f"[PondPumpController] Ошибка обновления погоды: {str(e)}")

    def setup_minimum_pump_speed(self, device: RelayChannelDevice) -> int:
        min_speed = 20
        try:
            weather_table = device.get_extra("weather")
            town = device.get_extra("weather_town")
            self.update_weather(town)

            if not self.weather.get("is_valid"):
                return device.get_extra("min_speed")

            temp = self.weather["temperature"]
            prev_speed = min_speed
            for t in sorted(weather_table.keys()):
                if temp < int(t):
                    return prev_speed
                prev_speed = int(weather_table[t])
            return prev_speed
        except Exception as e:
            logging.error(f"Ошибка расчета минимальной скорости насоса: {e}")
            return min_speed

    def _get_weather_data(self, town):
        try:
            import python_weather
            async def _fetch():
                async with python_weather.Client(format=python_weather.METRIC) as client:
                    return await client.get(town)
            weather = asyncio.run(_fetch())
            return {
                'temperature': int(weather.current.temperature),
                'wind_speed': int(weather.current.wind_speed),
                'visibility': int(weather.current.visibility),
                'uv_index': int(weather.current.uv_index),
                'humidity': int(weather.current.humidity),
                'precipitation': float(weather.current.precipitation),
                'type': str(weather.current.type),
                'wind_direction': str(weather.current.wind_direction),
                'feels_like': int(weather.current.feels_like),
                'description': str(weather.current.description),
                'pressure': float(weather.current.pressure),
                'timestamp': int(time.time()),
                'town': town,
                'is_valid': True
            }
        except Exception as e:
            logging.error("Ошибка получения погодных данных")
            logging.error(e)
            return {'temperature': 10, 'is_valid': False}

    def check_pump_speed(self, device: RelayChannelDevice) -> int:
        flow_speed = int(device.get_status("P"))
        step = int(device.get_extra("speed_step"))
        if flow_speed % step != 0:
            rounded = round(flow_speed / step) * step
            return max(rounded, step)
        return flow_speed

    def _increase_speed(self, device: RelayChannelDevice) -> int:
        current = int(device.get_status("P"))
        max_speed = int(device.get_extra("max_speed"))
        step = int(device.get_extra("speed_step"))
        next_speed = current + step
        return min(next_speed, max_speed)

    def _decrease_speed(self, device: RelayChannelDevice) -> int:
        current = int(device.get_status("P"))
        min_speed = int(device.get_extra("min_speed"))
        step = int(device.get_extra("speed_step"))
        next_speed = current - step
        return max(next_speed, min_speed)

    def adjust_speed(self, device: RelayChannelDevice, inv_status: int, inv_voltage: float) -> int:
        current = int(device.get_status("P"))
        step = int(device.get_extra("speed_step"))
        min_v = device.get_min_volt()
        max_v = device.get_max_volt()

        max_v, min_v = self._adjust_daylight(max_v, min_v)

        if inv_status == 0:
            return device.get_extra("min_speed")

        if not step:
            raise ValueError("Некорректный шаг скорости")

        if min_v < inv_voltage < max_v:
            return current

        if inv_voltage > max_v and current < int(device.get_extra("max_speed")):
            return self._increase_speed(device)

        if inv_voltage < min_v and current > int(device.get_extra("min_speed")):
            return self._decrease_speed(device)

        return current

    def _adjust_daylight(self, max_v, min_v):
        hour = int(datetime.now().strftime("%H"))
        if hour >= 18:
            return max_v + DAY_TIME_COMPENSATE, min_v + DAY_TIME_COMPENSATE
        elif 6 < hour < 16:
            return max_v - DAY_TIME_COMPENSATE, min_v - DAY_TIME_COMPENSATE
        return max_v, min_v
