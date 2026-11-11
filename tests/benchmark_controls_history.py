"""Reproducible Controls history benchmark using a disposable synthetic PostgreSQL DB.

There is no default DSN. Run it under ``tests/disposable_postgres.py run --``; every
connection is proven by ``tests/pg_isolation.py`` to be that run's disposable
database before any DROP/seed. Run with ``--seed`` before a baseline, then omit it
for after measurements (within the same provisioned run).
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import nullcontext
import json
import math
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg_isolation  # noqa: E402
from starlette.responses import JSONResponse

from app import web_routes
from app.web_routes import _transition_summary, build_observed_output_transitions


DSN = pg_isolation.configured_dsn()  # no fallback
SAMPLE_COUNT = 7 * 24 * 60
LAST_QUERY = None
LAST_QUERY_PARAMS = None
FIELDS = (
    "time", "working_mode", "charger_priority", "charger_priority_raw",
    "output_priority_raw", "mains_status", "battery_voltage", "battery_soc",
    "battery_current_chg", "battery_current_dis", "pv_power_w", "output_power_w",
    "output_voltage", "ac_input_voltage", "ac_output_load_pct",
)


def synthetic_records():
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)
    priorities = ("solar_only", "solar_priority", "solar_and_mains")
    records = []
    for index in range(SAMPLE_COUNT):
        daylight = max(0.0, math.sin((index % 1440 - 360) * math.pi / 720))
        mode = "Invert Mode" if (index // 60) % 2 else "Line Mode"
        records.append((
            start + timedelta(minutes=index), mode, priorities[(index // 20) % len(priorities)],
            "cloud-priority", f"priority-{(index // 9) % 4}", "present" if mode == "Line Mode" else "absent",
            25.2 + (index % 17) / 100, 74.0 - (index % 60) / 10,
            8.0 if mode == "Line Mode" else 0.0, 0.0 if mode == "Line Mode" else 13.0,
            80.0 + daylight * 1100.0, 250.0 + (index % 13) * 17.0,
            230.0 if mode == "Invert Mode" else 0.0,
            230.0 if mode == "Line Mode" else 0.0,
            min(100.0, 20.0 + ((index * 3) % 90)),
        ))
    return records


async def seed_database():
    conn = await pg_isolation.connect(DSN)
    try:
        await conn.execute("DROP TABLE IF EXISTS inverter_metrics")
        await conn.execute("""CREATE TABLE inverter_metrics (
            time TIMESTAMPTZ PRIMARY KEY,
            working_mode TEXT, charger_priority TEXT, charger_priority_raw TEXT,
            output_priority_raw TEXT, mains_status TEXT,
            battery_voltage DOUBLE PRECISION, battery_soc DOUBLE PRECISION,
            battery_current_chg DOUBLE PRECISION, battery_current_dis DOUBLE PRECISION,
            pv_power_w DOUBLE PRECISION, output_power_w DOUBLE PRECISION,
            output_voltage DOUBLE PRECISION, ac_input_voltage DOUBLE PRECISION,
            ac_output_load_pct DOUBLE PRECISION)""")
        await conn.copy_records_to_table("inverter_metrics", records=synthetic_records(), columns=FIELDS)
        await conn.execute("ANALYZE inverter_metrics")
        count = await conn.fetchval("SELECT count(*) FROM inverter_metrics")
        if count != SAMPLE_COUNT:
            raise RuntimeError(f"synthetic seed row count mismatch: {count}")
    finally:
        await conn.close()


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]


async def measure_endpoint_days(endpoint, days: int, repetitions: int, payload_dir: Path | None,
                                frozen: datetime | None, requires_auth: bool,
                                request_days: int | None = None):
    request_days = days if request_days is None else request_days
    samples = []
    last_body = b""
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen if tz else frozen.replace(tzinfo=None)

    auth_patch = patch.object(web_routes, "_check_auth", return_value=(True, "synthetic")) if requires_auth else nullcontext()
    clock_patch = patch.object(web_routes, "datetime", FixedDateTime) if frozen is not None else nullcontext()
    # App code opens raw asyncpg connections from DATABASE_URL: route them through the guard.
    with pg_isolation.app_connections(), auth_patch, clock_patch:
        original_connect = asyncpg.connect  # the guarded connect
        for _ in range(repetitions):
            query_ms = []
            async def traced_connect(*args, **kwargs):
                conn = await original_connect(*args, **kwargs)
                class TimedConnection:
                    async def fetch(self, *fetch_args, **fetch_kwargs):
                        global LAST_QUERY, LAST_QUERY_PARAMS
                        LAST_QUERY, LAST_QUERY_PARAMS = fetch_args[0], fetch_args[1:]
                        started = time.perf_counter()
                        try:
                            return await conn.fetch(*fetch_args, **fetch_kwargs)
                        finally:
                            query_ms.append((time.perf_counter() - started) * 1000)

                    async def fetchval(self, *fetch_args, **fetch_kwargs):
                        started = time.perf_counter()
                        try:
                            return await conn.fetchval(*fetch_args, **fetch_kwargs)
                        finally:
                            query_ms.append((time.perf_counter() - started) * 1000)

                    async def close(self):
                        await conn.close()
                return TimedConnection()

            started = time.perf_counter()
            with patch.object(asyncpg, "connect", side_effect=traced_connect):
                response = await endpoint(object(), days=request_days)
            total_ms = (time.perf_counter() - started) * 1000
            if response.status_code != 200:
                raise RuntimeError(f"history endpoint returned HTTP {response.status_code}: {response.body!r}")
            last_body = response.body
            payload = json.loads(last_body)
            samples.append({
                "query_ms": sum(query_ms),
                "query_count": len(query_ms),
                "endpoint_ms": total_ms,
                "json_bytes": len(last_body),
                "observations": len(payload["observations"]),
                "transitions": len(payload["transitions"]),
            })
    if payload_dir:
        payload_dir.mkdir(parents=True, exist_ok=True)
        (payload_dir / f"controls-history-{days}d.json").write_bytes(last_body)
    return {
        "days": days,
        "request_days": request_days,
        "repetitions": repetitions,
        "query_ms_median": round(statistics.median(item["query_ms"] for item in samples), 3),
        "query_count_median": statistics.median(item["query_count"] for item in samples),
        "endpoint_ms_median": round(statistics.median(item["endpoint_ms"] for item in samples), 3),
        "endpoint_ms_p95": round(percentile([item["endpoint_ms"] for item in samples], 0.95), 3),
        "json_bytes_median": int(statistics.median(item["json_bytes"] for item in samples)),
        "observations_median": int(statistics.median(item["observations"] for item in samples)),
        "transitions_median": int(statistics.median(item["transitions"] for item in samples)),
    }


async def legacy_endpoint(days: int, as_of: datetime):
    """Reproduce the pre-optimization route from master for a fixed clock."""
    conn = await pg_isolation.connect(DSN)
    try:
        verified_since = await conn.fetchval("SELECT MIN(time) FROM inverter_metrics")
        rows = await conn.fetch("""
            SELECT time, working_mode, charger_priority, charger_priority_raw,
                   output_priority_raw, mains_status,
                   battery_voltage, battery_soc, battery_current_chg,
                   battery_current_dis, pv_power_w, output_power_w,
                   output_voltage, ac_input_voltage, ac_output_load_pct
            FROM inverter_metrics
            WHERE time >= $1::timestamptz - ($2::int * INTERVAL '1 day') AND time <= $1::timestamptz
            ORDER BY time DESC
        """, as_of, days)
    finally:
        await conn.close()
    observations = []
    for row in rows:
        observation = dict(row)
        observation["time"] = row["time"].isoformat()
        observations.append(observation)
    cutoff_24h = as_of - timedelta(days=1)
    summary_rows = [row for row in rows if row["time"] >= cutoff_24h]
    transitions = build_observed_output_transitions(rows)
    summary = _transition_summary(summary_rows)
    for transition in transitions:
        transition["observed_at"] = transition["observed_at"].isoformat()
    return JSONResponse({
        "verified_since": verified_since.isoformat() if verified_since is not None else None,
        "observations": observations,
        "transitions": transitions,
        "summary_24h": summary,
        "observed_gap_threshold_seconds": web_routes.OBSERVED_TELEMETRY_GAP_SECONDS,
    })


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="store_true", help="replace the local table with the synthetic 7-day dataset")
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--payload-dir", type=Path)
    parser.add_argument("--implementation", choices=("legacy", "current"), default="current")
    parser.add_argument("--explain", action="store_true", help="include EXPLAIN ANALYZE for the optimized range query")
    args = parser.parse_args()
    if args.seed:
        await seed_database()
    conn = await pg_isolation.connect(DSN)
    try:
        count = await conn.fetchval("SELECT count(*) FROM inverter_metrics")
        as_of = await conn.fetchval("SELECT max(time) + INTERVAL '1 minute' FROM inverter_metrics")
        if count != SAMPLE_COUNT:
            raise RuntimeError("run with --seed first; refusing to benchmark an unexpected database")
    finally:
        await conn.close()
    frozen = as_of.astimezone(timezone.utc)
    current_endpoint = next(route.endpoint for route in web_routes.create_auth_router(None, None).routes
                            if route.path == "/api/charge/history")
    results = []
    for days in (1, 2, 7):
        if args.implementation == "legacy":
            endpoint = lambda _request, days: legacy_endpoint(days, frozen)
            request_days = 7  # The pre-fix Controls UI always requested seven days.
        else:
            endpoint = current_endpoint
            request_days = days
        result = await measure_endpoint_days(endpoint, days, args.repetitions, args.payload_dir,
                                             frozen if args.implementation == "current" else None,
                                             True, request_days)
        results.append(result)
    report = {"implementation": args.implementation, "synthetic_rows": SAMPLE_COUNT,
              "as_of": frozen.isoformat(), "measurements": results}
    if args.explain:
        if args.implementation != "current" or LAST_QUERY is None:
            raise RuntimeError("--explain requires --implementation current")
        conn = await pg_isolation.connect(DSN)
        try:
            query_plans = {}
            for days in (1, 2, 7):
                params = (frozen - timedelta(days=days), frozen)
                plan = await conn.fetch("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + LAST_QUERY,
                                        *params)
                query_plans[f"{days}d"] = [row["QUERY PLAN"] for row in plan]
        finally:
            await conn.close()
        report["query_plans"] = query_plans
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
