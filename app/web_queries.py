"""Database queries for the authenticated web surface."""


async def _fetch(database_url, sql, *args, **connect_options):
    import asyncpg
    conn = await asyncpg.connect(database_url, **connect_options)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def decision_journal(database_url, lower_bound, upper_bound, limit, after_time=None, after_id=None):
    """Best-effort keyset traversal: later inserts, updates and deletes may affect pages."""
    return await _fetch(database_url, """
        SELECT evaluation_id, evaluated_at, entry FROM adaptive_decision_journal
        WHERE evaluated_at >= $1 AND evaluated_at <= $2
          AND ($3::timestamptz IS NULL OR (evaluated_at, evaluation_id) < ($3, $4::text))
        ORDER BY evaluated_at DESC, evaluation_id DESC LIMIT $5
    """, lower_bound, upper_bound, after_time, after_id, limit, command_timeout=10)


async def latest_decision(database_url):
    rows = await _fetch(database_url, '''
        SELECT evaluation_id, evaluated_at, entry
        FROM adaptive_decision_journal
        ORDER BY evaluated_at DESC, evaluation_id DESC LIMIT 1
    ''', command_timeout=10)
    return rows[0] if rows else None


async def stream_decisions(database_url, days, chunk_size=500):
    import asyncpg
    conn = await asyncpg.connect(database_url, command_timeout=30)
    try:
        async with conn.transaction():
            cursor = await conn.cursor('''
                SELECT evaluation_id, evaluated_at, entry
                FROM adaptive_decision_journal
                WHERE evaluated_at >= now() - ($1::int * INTERVAL '1 day')
                ORDER BY evaluated_at DESC, evaluation_id DESC
            ''', days)
            while True:
                rows = await cursor.fetch(chunk_size)
                if not rows:
                    break
                for row in rows:
                    yield row
    finally:
        await conn.close()


async def charge_history(database_url, window_start, as_of):
    return await _fetch(database_url, """
                    WITH selected AS (
                        SELECT time, working_mode, charger_priority, charger_priority_raw, output_priority_raw, mains_status,
                               battery_voltage, battery_soc, battery_current_chg,
                               battery_current_dis, pv_power_w, output_power_w, output_voltage, ac_input_voltage, ac_output_load_pct,
                               FALSE AS is_context
                        FROM inverter_metrics
                        WHERE time >= $1 AND time <= $2
                    ), predecessor AS (
                        SELECT time, working_mode, charger_priority, charger_priority_raw, output_priority_raw, mains_status,
                               battery_voltage, battery_soc, battery_current_chg,
                               battery_current_dis, pv_power_w, output_power_w, output_voltage, ac_input_voltage, ac_output_load_pct,
                               TRUE AS is_context
                        FROM inverter_metrics
                        WHERE time < $1
                        ORDER BY time DESC
                        LIMIT 1
                    )
                    SELECT history.*, metadata.verified_since
                    FROM (SELECT MIN(time) AS verified_since FROM inverter_metrics) AS metadata
                    LEFT JOIN (
                        SELECT * FROM selected
                        UNION ALL
                        SELECT * FROM predecessor
                    ) AS history ON TRUE
                """, window_start, as_of)


async def energy_daily_balance(database_url, value):
    return await _fetch(database_url, """
                    SELECT day::date AS day, pv_kwh, grid_kwh,
                           battery_load_kwh, total_load_kwh, self_pct
                    FROM daily_energy_balance
                    WHERE day >= CURRENT_DATE - ($1::int - 1)
                    ORDER BY day DESC
                """, value)


async def inverter_metrics(database_url):
    return await _fetch(database_url, """
                SELECT time, working_mode, pv_power_w, battery_voltage,
                       battery_soc, battery_current_dis, output_power_w,
                       total_load_w, ac_output_load_pct
                FROM inverter_metrics
                ORDER BY time DESC
                LIMIT 50
            """)


async def energy_devices_today(database_url):
    return await _fetch(database_url, """
                WITH power_stats AS (
                    SELECT
                        device_name,
                        COUNT(*) FILTER (WHERE is_on = true) as on_count,
                        ROUND((COUNT(*) FILTER (WHERE is_on = true) * 2.0 / 60.0)::numeric, 2) as on_hours,
                        ROUND(AVG(power_watts) FILTER (WHERE is_on = true AND power_watts IS NOT NULL)::numeric, 1) as avg_power_w,
                        ROUND((SUM(power_watts) FILTER (WHERE is_on = true AND power_watts IS NOT NULL) * 2.0 / 60.0)::numeric, 1) as real_wh
                    FROM device_metrics
                    WHERE time >= DATE_TRUNC('day', NOW())
                    GROUP BY device_name
                ),
                energy_counters AS (
                    SELECT
                        device_name,
                        ROUND((MAX(add_ele_kwh) - MIN(add_ele_kwh))::numeric, 3) as delta_kwh,
                        COUNT(*) as counter_records
                    FROM device_energy_counters
                    WHERE time >= DATE_TRUNC('day', NOW())
                    GROUP BY device_name
                )
                SELECT
                    p.device_name,
                    p.on_count,
                    p.on_hours,
                    p.avg_power_w,
                    p.real_wh,
                    e.delta_kwh,
                    e.counter_records > 0 as has_energy_meter
                FROM power_stats p
                LEFT JOIN energy_counters e USING (device_name)
                ORDER BY p.device_name
            """)


async def energy_meter_daily(database_url, value):
    return await _fetch(database_url, """
                SELECT
                    day::date as day,
                    device_name,
                    ROUND(consumed_kwh::numeric, 3) as consumed_kwh,
                    ROUND(end_kwh::numeric, 3) as end_kwh,
                    samples
                FROM device_daily_energy
                WHERE day >= NOW() - ($1 || ' days')::interval
                ORDER BY day DESC, device_name
            """, value)


async def charts_inverter(database_url, value):
    return await _fetch(database_url, """
                SELECT time, pv_power_w, battery_voltage, output_power_w, working_mode
                FROM inverter_metrics
                WHERE time > NOW() - ($1 || ' hours')::interval
                ORDER BY time
            """, value)


async def charts_hourly_load(database_url, value):
    return await _fetch(database_url, """
                SELECT time_bucket('1 hour', time) as hour,
                       ROUND(AVG(power_watts)::numeric, 1) as avg_power_w,
                       COUNT(DISTINCT device_name) as device_count
                FROM device_metrics
                WHERE time > NOW() - ($1 || ' hours')::interval
                  AND power_watts IS NOT NULL
                GROUP BY hour ORDER BY hour
            """, value)


async def charts_weather(database_url, value):
    return await _fetch(database_url, """
                SELECT time, ambient_temp, humidity
                FROM weather_data
                WHERE time > NOW() - ($1 || ' hours')::interval
                ORDER BY time
            """, value)


def _sqlite_energy_daily(days):
    import os
    import sqlite3
    from pathlib import Path

    candidates = [os.getenv("ML_SQLITE_PATH"), "/app/ml_data/data.sqlite",
                  "ml_data/data.sqlite", "/srv/dessmonitor/ml/data.sqlite"]
    db_path = next((p for p in candidates if p and Path(p).exists()), None)
    if db_path is None:
        raise FileNotFoundError("SQLite DB not found")
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                WITH intervals AS (
                    SELECT
                        substr(timestamp, 1, 10) as day,
                        CAST(json_extract(data_json, '$.energy_from_pv_wh') AS REAL) as pv_wh,
                        CAST(json_extract(data_json, '$.energy_from_battery_wh') AS REAL) as batt_wh,
                        CAST(json_extract(data_json, '$.energy_from_grid_wh') AS REAL) as grid_wh,
                        CAST(json_extract(data_json, '$.energy_to_battery_wh') AS REAL) as chg_wh,
                        CAST(json_extract(data_json, '$.total_load_watt') AS REAL) as load_w,
                        CAST(json_extract(data_json, '$.unix_ts') AS INTEGER) as ts,
                        LAG(CAST(json_extract(data_json, '$.unix_ts') AS INTEGER))
                            OVER (ORDER BY unix_ts) as prev_ts
                    FROM ml_points
                    WHERE timestamp >= date('now', '-' || ? || ' days')
                )
                SELECT
                    day,
                    ROUND(SUM(pv_wh), 1),
                    ROUND(MAX(0, SUM(batt_wh) - SUM(chg_wh)), 1),
                    ROUND(SUM(grid_wh), 1),
                    ROUND(SUM(chg_wh), 1),
                    ROUND(SUM(
                        CASE
                            WHEN prev_ts IS NOT NULL AND (ts - prev_ts) BETWEEN 60 AND 7200
                            THEN load_w * (ts - prev_ts) / 3600.0
                            ELSE load_w * 5.0 / 60.0
                        END
                    ), 1)
                FROM intervals
                GROUP BY day
                ORDER BY day
            """, (days,))
            return cursor.fetchall()
        finally:
            cursor.close()
    finally:
        conn.close()


async def sqlite_energy_daily(days):
    import asyncio
    return await asyncio.to_thread(_sqlite_energy_daily, days)
