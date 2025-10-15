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

-- Готово!
\echo '✅ TimescaleDB tables created successfully!'