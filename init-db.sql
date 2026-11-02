-- init-db.sql
-- Включаем TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- ============================================================================
-- Таблица метрик устройств
-- ============================================================================
CREATE TABLE IF NOT EXISTS device_metrics (
    time TIMESTAMPTZ NOT NULL,
    device_name TEXT NOT NULL,
    device_type TEXT,
    is_on BOOLEAN,
    power_watts DOUBLE PRECISION,
    temperature_celsius DOUBLE PRECISION,
    humidity_percent DOUBLE PRECISION,
    voltage DOUBLE PRECISION,
    current_amps DOUBLE PRECISION,
    power_mode TEXT,
    metadata JSONB,
    PRIMARY KEY (time, device_name)
);

SELECT create_hypertable(
    'device_metrics',
    'time',
    if_not_exists => TRUE,
    chunk_time_interval => INTERVAL '1 day'
);

CREATE INDEX IF NOT EXISTS idx_device_metrics_device_name
ON device_metrics (device_name, time DESC);

CREATE INDEX IF NOT EXISTS idx_device_metrics_mode
ON device_metrics (power_mode, time DESC);

-- ============================================================================
-- Таблица событий переключения режимов
-- ============================================================================
CREATE TABLE IF NOT EXISTS power_mode_events (
    time TIMESTAMPTZ NOT NULL PRIMARY KEY,
    from_mode TEXT NOT NULL,
    to_mode TEXT NOT NULL,
    inverter_power DOUBLE PRECISION,
    grid_power DOUBLE PRECISION,
    battery_soc DOUBLE PRECISION,
    duration_seconds INTEGER,
    metadata JSONB
);

SELECT create_hypertable(
    'power_mode_events',
    'time',
    if_not_exists => TRUE,
    chunk_time_interval => INTERVAL '7 days'
);

CREATE INDEX IF NOT EXISTS idx_power_mode_events_mode
ON power_mode_events (to_mode, time DESC);

-- ============================================================================
-- Raw inverter observations used by the controls diagnostics
-- ============================================================================
CREATE TABLE IF NOT EXISTS inverter_metrics (
    time                TIMESTAMPTZ NOT NULL,
    battery_voltage     DOUBLE PRECISION,
    battery_soc         DOUBLE PRECISION,
    battery_current_dis DOUBLE PRECISION,
    battery_current_chg DOUBLE PRECISION,
    pv_power_w          DOUBLE PRECISION,
    output_power_w      DOUBLE PRECISION,
    output_voltage      DOUBLE PRECISION,
    ac_input_voltage    DOUBLE PRECISION,
    ac_input_frequency  DOUBLE PRECISION,
    ac_output_load_pct  DOUBLE PRECISION,
    total_load_w        DOUBLE PRECISION,
    working_mode        TEXT,
    charger_priority    TEXT,
    charger_priority_raw TEXT,
    output_priority_raw TEXT,
    mains_status        TEXT,
    PRIMARY KEY (time)
);

SELECT create_hypertable(
    'inverter_metrics',
    'time',
    if_not_exists => TRUE,
    chunk_time_interval => INTERVAL '1 day'
);

ALTER TABLE inverter_metrics
    ADD COLUMN IF NOT EXISTS output_priority_raw TEXT;
ALTER TABLE inverter_metrics
    ADD COLUMN IF NOT EXISTS mains_status TEXT;

CREATE INDEX IF NOT EXISTS idx_inverter_metrics_mode
ON inverter_metrics (working_mode, time DESC);

-- ============================================================================
-- 🆕 Таблица погоды
-- ============================================================================
CREATE TABLE IF NOT EXISTS weather_data (
    time TIMESTAMPTZ NOT NULL PRIMARY KEY,

    -- Текущее состояние
    ambient_temp DOUBLE PRECISION,
    humidity DOUBLE PRECISION,
    pressure_hpa DOUBLE PRECISION,
    wind_speed_mps DOUBLE PRECISION,
    clouds_pct DOUBLE PRECISION,
    uvi DOUBLE PRECISION,
    weather_description TEXT,

    -- Прогноз на следующий час
    forecast_temp DOUBLE PRECISION,
    forecast_rain_mm DOUBLE PRECISION,
    forecast_clouds_pct DOUBLE PRECISION,
    forecast_pop DOUBLE PRECISION,
    forecast_wind_mps DOUBLE PRECISION,

    -- Агрегаты (3h, 6h)
    forecast_3h_rain_mm DOUBLE PRECISION,
    forecast_6h_rain_mm DOUBLE PRECISION,
    forecast_3h_temp_delta DOUBLE PRECISION,
    forecast_6h_temp_delta DOUBLE PRECISION,
    will_rain_next_3h BOOLEAN,
    will_rain_next_6h BOOLEAN,

    -- Метаданные
    source TEXT,
    metadata JSONB
);

SELECT create_hypertable(
    'weather_data',
    'time',
    if_not_exists => TRUE,
    chunk_time_interval => INTERVAL '1 day'
);

CREATE INDEX IF NOT EXISTS idx_weather_time
ON weather_data (time DESC);

CREATE INDEX IF NOT EXISTS idx_weather_source
ON weather_data (source, time DESC);


-- ============================================================================
-- 🆕 ML Training View: Tigo + Weather + Time Features
-- ============================================================================
-- Материализованное представление для быстрого доступа к обучающим данным
CREATE MATERIALIZED VIEW IF NOT EXISTS ml_solar_training_data AS
SELECT
    -- Time features
    DATE_TRUNC('minute', t.time) as time,
    EXTRACT(HOUR FROM t.time) as hour,
    EXTRACT(DOW FROM t.time) as day_of_week,
    EXTRACT(MONTH FROM t.time) as month,
    CASE
        WHEN EXTRACT(MONTH FROM t.time) IN (12, 1, 2) THEN 'winter'
        WHEN EXTRACT(MONTH FROM t.time) IN (3, 4, 5) THEN 'spring'
        WHEN EXTRACT(MONTH FROM t.time) IN (6, 7, 8) THEN 'summer'
        ELSE 'autumn'
    END as season,
    CASE WHEN EXTRACT(HOUR FROM t.time) BETWEEN 6 AND 20 THEN 1 ELSE 0 END as is_daytime,

    -- Tigo system metrics (actual production)
    t.system_id,
    t.current_power_w as actual_power_w,
    t.today_energy_kwh,
    t.size_kw as system_size_kw,
    t.modules_online,
    t.efficiency_pct,

    -- Weather (current)
    w.ambient_temp,
    w.humidity,
    w.clouds_pct,
    w.wind_speed_mps,
    w.uvi,
    w.pressure_hpa,

    -- Weather (forecast for next hour)
    w.forecast_temp,
    w.forecast_clouds_pct,
    w.forecast_pop,
    w.forecast_wind_mps,

    -- Weather (3h/6h aggregates)
    w.forecast_3h_temp_delta,
    w.forecast_3h_mean_clouds,
    w.will_rain_next_3h,
    w.forecast_6h_temp_delta,
    w.forecast_6h_mean_clouds,
    w.will_rain_next_6h,

    -- Target: power in 1 hour (for training)
    LEAD(t.current_power_w, 1) OVER (
        PARTITION BY t.system_id
        ORDER BY t.time
    ) as target_power_1h,

    -- Target: energy in next hour (for training)
    LEAD(t.today_energy_kwh, 1) OVER (
        PARTITION BY t.system_id
        ORDER BY t.time
    ) - t.today_energy_kwh as target_energy_1h

FROM tigo_system_metrics t
LEFT JOIN weather_data w ON DATE_TRUNC('minute', t.time) = DATE_TRUNC('minute', w.time)
WHERE t.time > NOW() - INTERVAL '90 days'  -- Последние 90 дней
ORDER BY t.time DESC;

-- Индекс для быстрого доступа
CREATE INDEX IF NOT EXISTS idx_ml_solar_time ON ml_solar_training_data (time DESC);

-- Автоматическое обновление каждый час
CREATE OR REPLACE FUNCTION refresh_ml_solar_training_data()
RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY ml_solar_training_data;
END;
$$ LANGUAGE plpgsql;

-- Периодическое обновление (через pg_cron или вручную)
-- SELECT cron.schedule('refresh-ml-solar', '0 * * * *', 'SELECT refresh_ml_solar_training_data()');

-- Готово!
\echo '✅ TimescaleDB tables created successfully!'
