# app/weather/openweather_service.py
"""
Сервис погоды через OpenWeatherMap API.
Пишет данные в shared_state для использования другими модулями.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import aiohttp

from shared_state.shared_state import shared_state


class OpenWeatherService:
    """
    Получает текущую погоду и почасовой прогноз через OpenWeatherMap API.
    Записывает данные в shared_state (dict-like).
    """

    def __init__(
            self,
            api_key: str,
            lat: float,
            lon: float,
            update_interval: int = 600,
            timeout: int = 30
    ):
        self.api_key = api_key
        self.lat = lat
        self.lon = lon
        self.update_interval = update_interval
        self.timeout = timeout

        # OneCall API 3.0
        self.base_url = "https://api.openweathermap.org/data/3.0/onecall"

        self.logger = logging.getLogger("OpenWeather")
        self._last_update: Optional[datetime] = None
        self._fetch_count = 0
        self._error_count = 0
        self._session: Optional[aiohttp.ClientSession] = None

    async def fetch_weather(self) -> bool:
        """Получить погоду и прогноз, записать в shared_state"""
        params = {
            "lat": self.lat,
            "lon": self.lon,
            "appid": self.api_key,
            "units": "metric",
            "exclude": "minutely,alerts"
        }

        try:
            if not self._session:
                self.logger.warning("ClientSession not initialized, creating temporary one")
                async with aiohttp.ClientSession() as session:
                    return await self._do_fetch(session, params)

            return await self._do_fetch(self._session, params)

        except asyncio.TimeoutError:
            self.logger.error(f"❌ Weather API timeout after {self.timeout}s")
            self._error_count += 1
            return False

        except Exception as e:
            self.logger.error(f"❌ Weather fetch failed: {e}", exc_info=True)
            self._error_count += 1
            return False

    async def _do_fetch(self, session: aiohttp.ClientSession, params: dict) -> bool:
        """Внутренний метод для выполнения HTTP запроса"""
        try:
            async with session.get(
                    self.base_url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self._update_shared_state(data)
                        self._fetch_count += 1
                        self._last_update = datetime.now()

                        self.logger.info(
                            f"✅ Weather updated: "
                            f"{shared_state.get('ambient_temp')}°C, "
                            f"humidity {shared_state.get('humidity')}%, "
                            f"forecast {len(shared_state.get('forecast_hourly', []))} hours"
                        )
                        return True

                    elif resp.status == 401:
                        self.logger.error("❌ Invalid API key! Check your OpenWeatherMap API key")
                        return False

                    else:
                        self.logger.error(f"❌ Weather API error: {resp.status}")
                        self._error_count += 1
                        return False

        except Exception as e:
            self.logger.error(f"❌ HTTP request error: {e}", exc_info=True)
            self._error_count += 1
            return False

    def _update_shared_state(self, data: dict) -> None:
        """Записать данные в shared_state (dict-like)"""
        tz_offset_sec = int(data.get("timezone_offset", 0) or 0)
        tz = timezone(timedelta(seconds=tz_offset_sec))

        # ========== ТЕКУЩЕЕ СОСТОЯНИЕ ==========
        current = data.get("current", {})

        # ✅ Dict-like синтаксис вместо .set()
        shared_state["ambient_temp"] = current.get("temp")
        shared_state["humidity"] = current.get("humidity")
        shared_state["pressure_hpa"] = current.get("pressure")
        shared_state["wind_speed_mps"] = current.get("wind_speed")
        shared_state["clouds"] = current.get("clouds")
        shared_state["uvi"] = current.get("uvi")
        shared_state["sunrise_hour"] = self._to_local_hour(current.get("sunrise"), tz)
        shared_state["sunset_hour"] = self._to_local_hour(current.get("sunset"), tz)

        # Weather description
        weather_list = current.get("weather", [])
        if weather_list:
            shared_state["weather_description"] = weather_list[0].get("description")

        # ========== ПРОГНОЗ HOURLY ==========
        hourly = data.get("hourly", [])
        shared_state["forecast_hourly"] = hourly
        shared_state["forecast_source"] = "OpenWeatherMap"
        shared_state["forecast_clouds_pct"] = (
            hourly[0].get("clouds") if hourly else None
        )

        # ========== ПРОГНОЗ DAILY ==========
        daily = data.get("daily", [])
        if daily:
            today = daily[0]
            temp = today.get("temp", {})
            shared_state["daily_temp_min"] = temp.get("min")
            shared_state["daily_temp_max"] = temp.get("max")
            shared_state["daily_pop"] = today.get("pop")
            shared_state["sunrise_hour"] = (
                self._to_local_hour(today.get("sunrise"), tz)
                or shared_state.get("sunrise_hour")
            )
            shared_state["sunset_hour"] = (
                self._to_local_hour(today.get("sunset"), tz)
                or shared_state.get("sunset_hour")
            )

    @staticmethod
    def _to_local_hour(unix_ts: int | None, tz: timezone) -> int | None:
        if unix_ts is None:
            return None
        try:
            return datetime.fromtimestamp(int(unix_ts), tz=tz).hour
        except (TypeError, ValueError, OSError):
            return None

    async def run(self, stop_event: asyncio.Event) -> None:
        """Основной цикл обновления погоды"""
        self.logger.info(
            f"🌤️  Weather service started: "
            f"lat={self.lat}, lon={self.lon}, "
            f"update_interval={self.update_interval}s"
        )

        # Создаем переиспользуемую сессию
        self._session = aiohttp.ClientSession()

        try:
            # Первый запрос сразу
            await self.fetch_weather()

            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self.update_interval
                    )
                except asyncio.TimeoutError:
                    await self.fetch_weather()

        finally:
            # Закрываем сессию при завершении
            if self._session and not self._session.closed:
                await self._session.close()
                self._session = None

            self.logger.info(
                f"🌤️  Weather service stopped. "
                f"Fetches: {self._fetch_count}, Errors: {self._error_count}"
            )

    def get_statistics(self) -> dict:
        """Статистика работы сервиса"""
        return {
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "fetch_count": self._fetch_count,
            "error_count": self._error_count,
            "current_temp": shared_state.get("ambient_temp"),
            "forecast_hours": len(shared_state.get("forecast_hourly", [])),
        }
