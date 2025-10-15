# app/ml/ml_data_collector.py - PERSISTENT (SQLite + Forecast)
"""
Сборщик данных для обучения ML-модели с расширенными метриками для пруда.
Надёжное хранение: SQLite (WAL) + восстановление состояния после рестарта.
Опционально: CSV/JSONL-экспорт.
"""
import json
import os
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
import csv

from shared_state.shared_state import shared_state


# ============================ ВСПОМОГАТЕЛЬНОЕ ================================
def _now_ts() -> int:
    return int(datetime.now().timestamp())


def _safe_get(d: Dict[str, Any], key: str, default=None):
    try:
        return d.get(key, default)
    except Exception:
        return default


def _mm_from_rain_entry(entry) -> float:
    if entry is None:
        return 0.0
    if isinstance(entry, (int, float)):
        return float(entry)
    if isinstance(entry, dict):
        if "1h" in entry:
            try:
                return float(entry["1h"])
            except Exception:
                return 0.0
        if "precip_mm" in entry:
            try:
                return float(entry["precip_mm"])
            except Exception:
                return 0.0
    return 0.0


def _pick_next_hour_forecast(hourly: List[Dict[str, Any]], now_ts: int) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not hourly:
        return None, [], []
    def norm_dt(item):
        for k in ("dt", "timestamp", "time", "ts"):
            v = item.get(k)
            if v is not None:
                try:
                    return int(v)
                except Exception:
                    pass
        return None
    enriched = []
    for it in hourly:
        dt = norm_dt(it)
        if dt is None:
            continue
        enriched.append((dt, it))
    if not enriched:
        return None, [], []
    enriched.sort(key=lambda x: x[0])
    next_items = [it for (dt, it) in enriched if dt > now_ts]
    next_item = next_items[0] if next_items else enriched[-1][1]
    def within(hours: int):
        end = now_ts + hours * 3600
        return [it for (dt, it) in enriched if now_ts < dt <= end]
    return next_item, within(3), within(6)


# ============================ ДАННЫЕ ТОЧКИ ===================================
@dataclass
class MLDataPoint:
    """Одна точка данных для обучения модели"""
    # ============= ВРЕМЕННЫЕ МЕТКИ =============
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    unix_ts: int = field(default_factory=_now_ts)
    hour: int = field(default_factory=lambda: datetime.now().hour)
    day_of_week: int = field(default_factory=lambda: datetime.now().weekday())
    month: int = field(default_factory=lambda: datetime.now().month)
    is_weekend: int = field(default_factory=lambda: 1 if datetime.now().weekday() >= 5 else 0)
    is_daytime: int = field(default_factory=lambda: 1 if 6 <= datetime.now().hour <= 20 else 0)
    is_night: int = field(default_factory=lambda: 1 if datetime.now().hour < 6 or datetime.now().hour >= 22 else 0)
    season: str = None  # winter/spring/summer/autumn

    # ============= ИНВЕРТОР =============
    battery_voltage: float = None
    battery_soc: float = None
    battery_current_chg: float = None
    battery_current_dis: float = None

    # ============= СОЛНЕЧНЫЕ ПАНЕЛИ =============
    pv1_voltage: float = None
    pv1_power: float = None
    pv2_voltage: float = None
    pv2_power: float = None
    pv_total_power: float = None

    # ============= ВЫХОД ИНВЕРТОРА =============
    output_voltage: float = None
    output_power: float = None
    output_apparent_power: float = None
    ac_output_load: float = None

    # ============= ВХОД ОТ СЕТИ =============
    ac_input_voltage: float = None
    ac_input_frequency: float = None

    # ============= РЕЖИМЫ РАБОТЫ =============
    working_mode: str = None
    mains_status: str = None
    inverter_on: bool = None

    # ============= ПОГОДА (ТЕКУЩЕЕ) =============
    ambient_temp: float = None
    water_temp: float = None
    humidity: float = None
    pressure_hpa: float = None
    wind_speed_mps: float = None

    # Вычисляемые погодные метрики
    temp_diff_air_water: float = None
    water_temp_trend: str = None
    equivalent_cooling_index: float = None

    # ============= ПРОГНОЗ (БЛИЖ. ЧАС И АГРЕГАТЫ) =============
    fc_source: str = None
    fc_dt: int = None
    fc_temp_c: float = None
    fc_clouds_pct: float = None
    fc_pop: float = None
    fc_rain_mm: float = None
    fc_wind_mps: float = None
    fc_uvi: float = None
    fc_solar_irradiance_wm2: float = None

    fc3h_temp_delta: float = None
    fc6h_temp_delta: float = None
    fc3h_max_pop: float = None
    fc6h_max_pop: float = None
    fc3h_total_rain_mm: float = None
    fc6h_total_rain_mm: float = None
    fc3h_mean_clouds: float = None
    fc6h_mean_clouds: float = None
    will_rain_next_3h: int = None
    will_rain_next_6h: int = None

    # ============= УСТРОЙСТВА =============
    total_load_watt: float = 0.0
    devices_on_count: int = 0
    pump_speed: int = None
    pump_mode: int = None
    pump_uptime_today_sec: int = None
    pump_current_uptime_sec: int = None

    # ============= ЭНЕРГИЯ =============
    energy_from_pv_wh: float = 0.0
    energy_from_grid_wh: float = 0.0
    energy_to_load_wh: float = 0.0
    energy_to_battery_wh: float = 0.0
    energy_from_battery_wh: float = 0.0

    # ============= ЦЕЛИ =============
    next_hour_pv_power: float = None
    optimal_pump_speed: int = None
    should_charge_battery: bool = None

    def __post_init__(self):
        if self.month in [12, 1, 2]:
            self.season = 'winter'
        elif self.month in [3, 4, 5]:
            self.season = 'spring'
        elif self.month in [6, 7, 8]:
            self.season = 'summer'
        else:
            self.season = 'autumn'

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def is_valid(self) -> bool:
        # if self.battery_voltage is None:
        #     return False
        return True

    def get_completeness_score(self) -> float:
        total_fields = 0
        filled_fields = 0
        for key, value in self.to_dict().items():
            if key in ['timestamp', 'unix_ts', 'hour', 'day_of_week', 'month',
                       'is_weekend', 'is_daytime', 'is_night', 'season']:
                continue
            total_fields += 1
            if value is not None and value != '':
                filled_fields += 1
        return (filled_fields / total_fields) if total_fields > 0 else 0.0


# ============================ ХРАНИЛИЩЕ (SQLite) =============================
class SQLiteStorage:
    """
    Простое и надёжное хранилище:
    - Таблица ml_points: id, unix_ts, timestamp, data_json (вся точка как JSON)
    - Индексы по времени
    - WAL + synchronous=NORMAL (можно выставить FULL при жестких требованиях)
    """
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = Path(os.getenv("ML_SQLITE_PATH", "ml_data/data.sqlite"))
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self):
        conn = self._connect()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ml_points (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    unix_ts INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_points_ts ON ml_points(unix_ts);")
            conn.commit()
        finally:
            conn.close()

    def insert_point(self, point: MLDataPoint):
        payload = json.dumps(point.to_dict(), ensure_ascii=False)
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO ml_points (unix_ts, timestamp, data_json) VALUES (?, ?, ?)",
                (point.unix_ts, point.timestamp, payload)
            )
            conn.commit()
            try:
                os.fsync(conn.execute("PRAGMA wal_checkpoint(PASSIVE);").connection._handle)
            except Exception:
                pass
        finally:
            conn.close()

    def last_point(self) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            cur = conn.execute("SELECT data_json FROM ml_points ORDER BY unix_ts DESC LIMIT 1;")
            row = cur.fetchone()
            if not row:
                return None
            return json.loads(row[0])
        finally:
            conn.close()

    def stats(self) -> Dict[str, Any]:
        """Статистика хранилища"""
        conn = self._connect()
        try:
            # Общее количество
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM ml_points")
            total = cur.fetchone()[0]

            # Первая запись
            cur.execute("SELECT data_json FROM ml_points ORDER BY unix_ts ASC LIMIT 1")
            first_row = cur.fetchone()
            first = json.loads(first_row[0]) if first_row else {}

            # Последняя запись
            cur.execute("SELECT data_json FROM ml_points ORDER BY unix_ts DESC LIMIT 1")
            last_row = cur.fetchone()
            last = json.loads(last_row[0]) if last_row else {}

            return {
                "total_records": total,
                "first_record": first.get("timestamp"),
                "last_record": last.get("timestamp"),
            }
        except Exception as e:
            self.logger.error(f"Error getting stats: {e}")
            return {
                "total_records": 0,
                "error": str(e)
            }
        finally:
            conn.close()


# ============================ ОСНОВНОЙ КОЛЛЕКТОР ============================
class MLDataCollector:
    """
    Расширенный сборщик данных с прогнозом и устойчивым хранением.
    По умолчанию: SQLite в ml_data/data.sqlite.
    При желании можно дополнительно включить CSV/JSONL-экспорт.
    """
    def __init__(
        self,
        # Персистентное хранилище:
        db_path: Optional[Path] = None,
        # Необязательные экспорт-файлы (off по умолчанию):
        csv_path: Optional[Path] = None,
        json_path: Optional[Path] = None,
        csv_export_enabled: Optional[bool] = None,
        jsonl_export_enabled: Optional[bool] = None,
        # Тайминги/поведение:
        collect_interval: Optional[int] = None,
        skip_invalid: bool = True,
        wait_for_first_data: int = 60,
    ):
        # Хранилище
        if db_path is None:
            db_path = Path(os.getenv("ML_SQLITE_PATH", "ml_data/data.sqlite"))
        self.store = SQLiteStorage(db_path)

        # Экспорт
        if csv_path is None:
            csv_path = Path(os.getenv("ML_CSV_PATH", "ml_data/training_data.csv"))
        if json_path is None:
            json_path = Path(os.getenv("ML_JSONL_PATH", "ml_data/training_data.jsonl"))
        self.csv_path = csv_path
        self.json_path = json_path

        if csv_export_enabled is None:
            csv_export_enabled = os.getenv("ML_CSV_EXPORT", "false").lower() in ("true", "1", "yes")
        if jsonl_export_enabled is None:
            jsonl_export_enabled = os.getenv("ML_JSONL_EXPORT", "false").lower() in ("true", "1", "yes")

        self.csv_export_enabled = csv_export_enabled
        self.jsonl_export_enabled = jsonl_export_enabled

        if collect_interval is None:
            collect_interval = int(os.getenv("ML_COLLECT_INTERVAL", "300"))

        self.collect_interval = collect_interval
        self.skip_invalid = skip_invalid
        self.wait_for_first_data = wait_for_first_data

        self.logger = logging.getLogger("MLDataCollector")

        # Буферы (будут восстановлены из БД, если есть записи)
        self._last_collection: Optional[MLDataPoint] = None
        self._last_water_temp: Optional[float] = None

        # Статистика
        self._total_collected = 0
        self._total_skipped = 0
        self._missing_water_temp_count = 0
        self._missing_forecast_count = 0

        # CSV хедер при необходимости
        if self.csv_export_enabled:
            self._init_csv_header()

        # Восстановление предыдущего состояния
        self._restore_last_state()

    # --------- восстановление из БД ---------
    def _restore_last_state(self):
        try:
            last = self.store.last_point()
            if last:
                # восстановим "предыдущую" точку для корректной энергии/тренда
                self._last_collection = MLDataPoint(**{k: last.get(k) for k in MLDataPoint().__dict__.keys() if not k.startswith('_')})
                self._last_water_temp = last.get("water_temp")
                self.logger.info("Restored last state from SQLite (energy/trend continuity will be correct).")
        except Exception as e:
            self.logger.warning(f"Failed to restore last state: {e}")

    # --------- CSV header ---------
    def _init_csv_header(self):
        sample = MLDataPoint()
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        need_header = True
        if self.csv_path.exists():
            try:
                with open(self.csv_path, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    header = next(reader, None)
                need_header = (header is None) or (set(header) != set(sample.to_dict().keys()))
            except Exception:
                need_header = True
        if need_header:
            with open(self.csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=sample.to_dict().keys())
                writer.writeheader()
            self.logger.info(f"Created/updated CSV header: {self.csv_path}")

    # -------------------------- ПРОГНОЗ: ЧТЕНИЕ --------------------------------
    def _read_forecast_hourly(self) -> List[Dict[str, Any]]:
        candidates = ["forecast_hourly", "weather_forecast_hourly", "forecast", "hourly_forecast", "owm_hourly"]
        for key in candidates:
            data = shared_state.get(key)
            if isinstance(data, list) and data:
                return data
            if isinstance(data, dict) and isinstance(data.get("hourly"), list):
                return data["hourly"]
        return []

    def _fill_forecast_fields(self, point: MLDataPoint):
        hourly = self._read_forecast_hourly()
        if not hourly:
            self._missing_forecast_count += 1
            if self._missing_forecast_count % 10 == 1:
                self.logger.warning(
                    f"⚠️  Weather forecast hourly not available "
                    f"(missing {self._missing_forecast_count} times)."
                )
            return
        now_ts = point.unix_ts or _now_ts()
        next_item, win3, win6 = _pick_next_hour_forecast(hourly, now_ts)
        if next_item:
            point.fc_dt = int(_safe_get(next_item, "dt", now_ts))
            point.fc_temp_c = _safe_get(next_item, "temp")
            point.fc_clouds_pct = _safe_get(next_item, "clouds")
            point.fc_pop = _safe_get(next_item, "pop")
            point.fc_rain_mm = _mm_from_rain_entry(_safe_get(next_item, "rain"))
            point.fc_wind_mps = _safe_get(next_item, "wind_speed")
            point.fc_uvi = _safe_get(next_item, "uvi")
            point.fc_solar_irradiance_wm2 = _safe_get(next_item, "solar_irradiance")
            point.fc_source = shared_state.get("forecast_source")

        def agg(win: List[Dict[str, Any]]):
            if not win:
                return None, None, None, None, None
            temps = [it.get("temp") for it in win if it.get("temp") is not None]
            pops = [it.get("pop") for it in win if it.get("pop") is not None]
            clouds = [it.get("clouds") for it in win if it.get("clouds") is not None]
            rains = [_mm_from_rain_entry(it.get("rain")) for it in win]
            d_temp = (win[-1].get("temp") - win[0].get("temp")) if len(temps) >= 2 else None
            return d_temp, (max(pops) if pops else None), (sum(rains) if rains else None), (sum(clouds)/len(clouds) if clouds else None), None

        d3, pop3, rain3, cl3, _ = agg(win3)
        d6, pop6, rain6, cl6, _ = agg(win6)
        point.fc3h_temp_delta = d3
        point.fc6h_temp_delta = d6
        point.fc3h_max_pop = pop3
        point.fc6h_max_pop = pop6
        point.fc3h_total_rain_mm = rain3
        point.fc6h_total_rain_mm = rain6
        point.fc3h_mean_clouds = cl3
        point.fc6h_mean_clouds = cl6
        point.will_rain_next_3h = 1 if (point.fc3h_total_rain_mm or 0) > 0 else 0
        point.will_rain_next_6h = 1 if (point.fc6h_total_rain_mm or 0) > 0 else 0

    # --------------------------- ОСНОВНОЙ СБОР --------------------------------
    def collect(self, devices: List[Any] = None) -> Optional[MLDataPoint]:
        point = MLDataPoint()

        # ============= ИНВЕРТОР =============
        point.battery_voltage = shared_state.get("battery_voltage")
        point.battery_soc = shared_state.get("battery_soc")
        point.battery_current_chg = shared_state.get("battery_current_chg")
        point.battery_current_dis = shared_state.get("battery_current_dis")

        point.pv1_voltage = shared_state.get("pv1_voltage")
        point.pv1_power = shared_state.get("pv1_power")
        point.pv2_voltage = shared_state.get("pv2_voltage")
        point.pv2_power = shared_state.get("pv2_power")
        point.pv_total_power = shared_state.get("pv_total_power")

        point.output_voltage = shared_state.get("output_voltage")
        point.output_power = shared_state.get("output_power")
        point.output_apparent_power = shared_state.get("output_apparent_power")
        point.ac_output_load = shared_state.get("ac_output_load")

        point.ac_input_voltage = shared_state.get("ac_input_voltage")
        point.ac_input_frequency = shared_state.get("ac_input_frequency")

        point.working_mode = shared_state.get("working_mode")
        point.mains_status = shared_state.get("mains_status")
        point.inverter_on = point.working_mode != "LINE MODE" if point.working_mode else None

        # ============= ПОГОДА/ПРУД (текущее) =============
        point.ambient_temp = shared_state.get("ambient_temp")
        point.pressure_hpa = shared_state.get("pressure_hpa")
        point.wind_speed_mps = shared_state.get("wind_speed_mps") or shared_state.get("wind_speed")

        point.water_temp = shared_state.get("water_temp")
        if point.water_temp is None:
            point.water_temp = shared_state.get("pondtemp")
            if point.water_temp is None:
                temp_raw = shared_state.get("temp_current")
                if isinstance(temp_raw, (int, float)):
                    point.water_temp = round(float(temp_raw) / 10.0, 1)

        if point.water_temp is None:
            self._missing_water_temp_count += 1
            if self._missing_water_temp_count % 10 == 1:
                self.logger.warning(
                    f"⚠️  Water temperature not available "
                    f"(missing {self._missing_water_temp_count} times). "
                    "Check watertemp sensor!"
                )

        point.humidity = shared_state.get("humidity")

        if point.ambient_temp is not None and point.water_temp is not None:
            point.temp_diff_air_water = point.ambient_temp - point.water_temp

        if point.water_temp is not None and self._last_water_temp is not None:
            diff = float(point.water_temp) - float(self._last_water_temp)
            point.water_temp_trend = 'stable' if abs(diff) < 0.1 else ('warming' if diff > 0 else 'cooling')
        if point.water_temp is not None:
            self._last_water_temp = point.water_temp

        try:
            if (point.ambient_temp is not None) and (point.wind_speed_mps is not None):
                point.equivalent_cooling_index = max(0.0, (20.0 - float(point.ambient_temp))) * float(point.wind_speed_mps)
        except Exception:
            pass

        # ============= ПРОГНОЗ =============
        self._fill_forecast_fields(point)

        # ============= УСТРОЙСТВА =============
        if devices:
            try:
                point.devices_on_count = sum(1 for d in devices if d.is_device_on())
            except Exception:
                point.devices_on_count = None
            try:
                point.total_load_watt = sum((d.power_consumption() or 0) for d in devices if getattr(d, "is_device_on", lambda: False)())
            except Exception:
                pass
            pumps = [d for d in devices if getattr(d, "device_type", "").lower() == "pump"]
            if pumps:
                pump = pumps[0]
                try:
                    point.pump_speed = int(pump.status.get("P")) if pump.status and pump.status.get("P") is not None else None
                except Exception:
                    point.pump_speed = None
                point.pump_mode = shared_state.get("pump_mode")
                point.pump_uptime_today_sec = getattr(pump, "today_run_sec", None)
                try:
                    if getattr(pump, "is_device_on", lambda: False)():
                        point.pump_current_uptime_sec = getattr(pump, "get_uptime_sec", lambda: None)()
                except Exception:
                    pass

        # ============= ВАЛИДАЦИЯ =============
        if not point.is_valid():
            completeness = point.get_completeness_score()
            self.logger.warning(
                f"INVALID data: completeness={completeness:.1%}, "
                f"battery_voltage={point.battery_voltage}, "
                f"working_mode={point.working_mode}"
            )
            return None

        # ============= ЭНЕРГИЯ (дельта) =============
        if self._last_collection:
            time_delta_h = (point.unix_ts - self._last_collection.unix_ts) / 3600.0
            if time_delta_h > 0:
                pv_now = point.pv_total_power or 0.0
                pv_prev = self._last_collection.pv_total_power or 0.0
                point.energy_from_pv_wh = ((pv_now + pv_prev) / 2.0) * time_delta_h

                out_now = point.output_power or 0.0
                out_prev = self._last_collection.output_power or 0.0
                point.energy_to_load_wh = ((out_now + out_prev) / 2.0) * time_delta_h

                v_now = point.battery_voltage or self._last_collection.battery_voltage
                v_prev = self._last_collection.battery_voltage or point.battery_voltage
                avg_v = ((v_now or 0.0) + (v_prev or 0.0)) / 2.0

                chg_now = point.battery_current_chg or 0.0
                chg_prev = self._last_collection.battery_current_chg or 0.0
                point.energy_to_battery_wh = ((chg_now + chg_prev) / 2.0) * avg_v * time_delta_h

                dis_now = point.battery_current_dis or 0.0
                dis_prev = self._last_collection.battery_current_dis or 0.0
                point.energy_from_battery_wh = ((dis_now + dis_prev) / 2.0) * avg_v * time_delta_h

                if (str(point.working_mode).upper().startswith("LINE") or
                    str(getattr(self._last_collection, "working_mode", "")).upper().startswith("LINE")):
                    point.energy_from_grid_wh = point.energy_to_load_wh

        self._last_collection = point
        return point

    # --------------------------- СОХРАНЕНИЕ -----------------------------------
    def _export_csv(self, point: MLDataPoint):
        if not self.csv_export_enabled:
            return
        with open(self.csv_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=point.to_dict().keys())
            writer.writerow(point.to_dict())
            f.flush()
            os.fsync(f.fileno())

    def _export_jsonl(self, point: MLDataPoint):
        if not self.jsonl_export_enabled:
            return
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.json_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(point.to_dict(), ensure_ascii=False) + '\n')
            f.flush()
            os.fsync(f.fileno())

    def save(self, point: MLDataPoint):
        """Сначала — БД (персистентно), затем — опциональные экспорт-файлы"""
        self.store.insert_point(point)
        self._export_csv(point)
        self._export_jsonl(point)
        self._total_collected += 1
        self.logger.debug(f"Saved: {point.timestamp} (completeness: {point.get_completeness_score():.1%})")

    # ----------------------- ЦИКЛ СБОР И ЛОГИРОВАНИЕ --------------------------
    def collect_and_save(self, devices: List[Any] = None):
        try:
            point = self.collect(devices)
            if point is None:
                self._total_skipped += 1
                if self.skip_invalid:
                    return
            if point:
                self.save(point)
                water_info = f"Water={point.water_temp}°C" if point.water_temp is not None else "Water=N/A⚠️"
                air_info = f"Air={point.ambient_temp}°C" if point.ambient_temp is not None else "Air=N/A"
                pump_info = f"Pump={point.pump_speed}%" if point.pump_speed is not None else "Pump=OFF"
                trend = f", Trend={point.water_temp_trend}" if point.water_temp_trend else ""
                fc = (f", Fc {int(point.fc_temp_c)}°C, clouds {int(point.fc_clouds_pct)}%, "
                      f"pop {round(point.fc_pop,2) if point.fc_pop is not None else 'n/a'}") if point.fc_temp_c is not None else ", Fc=n/a"
                self.logger.info(
                    f"✅ Collected: {water_info}{trend}, {air_info}{fc}, "
                    f"PV={point.pv_total_power}W, Batt={point.battery_voltage}V, "
                    f"{pump_info}, Mode={point.working_mode}"
                )
        except Exception as e:
            self.logger.error(f"Collection error: {e}", exc_info=True)

    def get_statistics(self) -> Dict[str, Any]:
        stats = self.store.stats()
        stats.update({
            "total_collected": self._total_collected,
            "total_skipped": self._total_skipped,
            "missing_water_temp_count": self._missing_water_temp_count,
            "missing_forecast_count": self._missing_forecast_count,
            "storage": str(self.store.db_path),
            "csv_export": str(self.csv_path) if self.csv_export_enabled else "disabled",
            "jsonl_export": str(self.json_path) if self.jsonl_export_enabled else "disabled",
            "db_file_size_mb": (Path(self.store.db_path).stat().st_size / 1024 / 1024) if Path(self.store.db_path).exists() else 0.0,
        })
        return stats


# ============================================================================
# Асинхронный цикл
# ============================================================================
async def ml_collection_loop(collector: MLDataCollector, dev_mgr, stop_event):
    """Асинхронный цикл сбора данных"""
    import asyncio
    logger = logging.getLogger("MLCollectionLoop")
    logger.info("ML data collection started (PERSISTENT SQLite + Forecast)")

    # Ждём первые данные
    if collector.wait_for_first_data > 0:
        logger.info(f"Waiting {collector.wait_for_first_data}s for first data...")
        waited = 0
        while waited < collector.wait_for_first_data:
            if shared_state.get("battery_voltage") is not None:
                logger.info(f"✅ First data received after {waited}s")
                break
            if stop_event.is_set():
                return
            await asyncio.sleep(5)
            waited += 5
        if waited >= collector.wait_for_first_data:
            logger.warning(f"⚠️  No data after {waited}s. Starting anyway.")

    # Основной цикл
    while not stop_event.is_set():
        try:
            devices = dev_mgr.get_devices()
            collector.collect_and_save(devices)
        except Exception as e:
            logger.error(f"Collection error: {e}", exc_info=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=collector.collect_interval)
        except asyncio.TimeoutError:
            pass

    stats = collector.get_statistics()
    logger.info(f"ML collection stopped. Stats: {stats}")
