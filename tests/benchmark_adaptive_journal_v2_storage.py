"""Measure Journal v1 vs v2 storage for one synthetic day (disposable PostgreSQL only).

    python tests/disposable_postgres.py run -- python tests/benchmark_adaptive_journal_v2_storage.py

There is no default DSN; tests/pg_isolation.py proves every connection is the
provisioned run's disposable database before any DROP or migration.

Workload: 1,440 evaluations at one per minute. Telemetry arrives each minute, but
only two of every five payloads change (the rest are exact cached repeats); the
reported mode alternates every 30 minutes; two 15-minute telemetry outages make the
latest collection stale. All runs use identical telemetry and the unchanged
evaluation; only the persistence differs.

Measurement modes (each for v1 and v2):
  * ``periodic`` -- a manual ``VACUUM`` of the journal tables after every 60th
    evaluation (stands in for autovacuum over a real day);
  * ``none``     -- no manual VACUUM at all.
In both modes autovacuum is disabled on the measured tables (reloption
``autovacuum_enabled = false``) and the vacuum counters are checked afterwards, so a
``none`` result is never silently shrunk by autovacuum; the run fails if any vacuum
happened. Sizes are measured as they are -- no VACUUM or ANALYZE at measurement time.
On-access HOT pruning still happens in every mode (it is not a VACUUM).
Optionally, a separately labelled ``after_final_vacuum`` measurement follows one
final manual VACUUM.
"""
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))


from app.control import adaptive_journal_v2 as v2  # noqa: E402
from app.control import online_output_learning as ol  # noqa: E402
import pg_isolation  # noqa: E402

DSN = pg_isolation.configured_dsn()  # no fallback
START = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
MINUTES = 1440
OUTAGES = (range(600, 615), range(1200, 1215))
COLUMNS = ('time', 'working_mode', 'battery_voltage', 'battery_soc', 'battery_current_chg',
           'battery_current_dis', 'pv_power_w', 'output_power_w')
V2_TABLES = ('adaptive_learning_state', 'adaptive_latest_evaluation', 'adaptive_decision_events',
             'adaptive_state_repetitions', 'adaptive_worker_heartbeat', 'adaptive_journal_control')


def telemetry():
    rows, payload = [], None
    for minute in range(-180, MINUTES):
        if any(minute in outage for outage in OUTAGES):
            continue
        line = (minute // 30) % 2 == 0
        if payload is None or minute % 5 in (0, 2):
            # Distinct payloads (no recurrence inside the 3 h window), so they count as new evidence.
            k = minute % 37
            payload = ('Line Mode' if line else 'Invert Mode', round((26.9 if line else 26.3) + k * 0.01, 2),
                       60.0, 0.0, 0.0 if line else 10.0 + minute % 11, 400.0 + minute % 113, 600.0 + minute % 97)
        rows.append((START + timedelta(minutes=minute),) + payload)
    return rows


async def reset(conn):
    await pg_isolation.verify_connection(conn)  # again, immediately before the destructive reset
    await conn.execute('''DROP TABLE IF EXISTS adaptive_worker_heartbeat, adaptive_state_repetitions,
        adaptive_decision_events, adaptive_latest_evaluation, adaptive_learning_state, adaptive_journal_control,
        adaptive_decision_journal, inverter_metrics CASCADE;
        DROP FUNCTION IF EXISTS adaptive_decision_journal_v2_write_guard() CASCADE''')
    await conn.execute((ROOT / 'migrations' / '001_adaptive_decision_journal.sql').read_text())
    await conn.execute((ROOT / 'migrations' / '002_adaptive_decision_journal_v2.sql').read_text())
    await conn.execute('''CREATE TABLE inverter_metrics (time TIMESTAMPTZ PRIMARY KEY, working_mode TEXT,
        battery_voltage DOUBLE PRECISION, battery_soc DOUBLE PRECISION, battery_current_chg DOUBLE PRECISION,
        battery_current_dis DOUBLE PRECISION, pv_power_w DOUBLE PRECISION, output_power_w DOUBLE PRECISION)''')
    await conn.copy_records_to_table('inverter_metrics', records=telemetry(), columns=COLUMNS)
    await conn.execute('ANALYZE inverter_metrics')


VACUUM_MODES = ('periodic', 'none')
PERIODIC_VACUUM_EVERY = 60


def journal_tables(mode):
    return ['adaptive_decision_journal'] + (list(V2_TABLES) if mode == 'v2' else [])


async def disable_autovacuum(conn, tables):
    for table in tables:
        await conn.execute('ALTER TABLE %s SET (autovacuum_enabled = false, toast.autovacuum_enabled = false)'
                           % table)


async def sizes(conn, tables):
    """Measure as-is: row counts and on-disk bytes. Never VACUUMs or ANALYZEs."""
    result = {}
    for table in tables:
        result[table] = {'rows': await conn.fetchval('SELECT count(*) FROM %s' % table),
                         'total_relation_bytes': await conn.fetchval('SELECT pg_total_relation_size($1::regclass)', table)}
    return result


async def vacuum_activity(conn, tables):
    rows = await conn.fetch('''SELECT relname::text AS relname, vacuum_count, autovacuum_count
        FROM pg_stat_user_tables WHERE relname = ANY($1::text[])''', tables)
    return {'manual_vacuum_count': sum(r['vacuum_count'] for r in rows),
            'autovacuum_count': sum(r['autovacuum_count'] for r in rows),
            'tables_reported': len(rows)}


def total_bytes(measurement):
    return sum(t['total_relation_bytes'] for t in measurement.values())


async def run(mode, vacuum_mode, final_vacuum=False):
    if mode not in ('v1', 'v2') or vacuum_mode not in VACUUM_MODES:
        raise ValueError('unknown benchmark mode')
    conn = await pg_isolation.connect(DSN)
    pool = await pg_isolation.create_pool(DSN, min_size=1, max_size=2)
    try:
        await reset(conn)
        tables = journal_tables(mode)
        await disable_autovacuum(conn, tables)
        vacuum_sql = 'VACUUM ' + ', '.join(tables)
        periodic_vacuums = 0
        for minute in range(MINUTES):
            now = START + timedelta(minutes=minute, seconds=30)
            if mode == 'v1' or minute == 0:
                assert await ol.run_evaluation(pool, now)
                if mode == 'v2':
                    await v2.cutover_to_v2(pool, now + timedelta(seconds=1))
            else:
                assert await v2.run_evaluation_v2(pool, now, worker_id='bench')
            if vacuum_mode == 'periodic' and minute % PERIODIC_VACUUM_EVERY == PERIODIC_VACUUM_EVERY - 1:
                await conn.execute(vacuum_sql)
                periodic_vacuums += 1
        measured = await sizes(conn, tables)
        activity = await vacuum_activity(conn, tables)
        if activity['autovacuum_count']:
            raise RuntimeError('autovacuum ran during the measurement; result discarded')
        if vacuum_mode == 'none' and activity['manual_vacuum_count']:
            raise RuntimeError('a VACUUM ran during a no-VACUUM measurement; result discarded')
        report = {'mode': mode, 'vacuum_mode': vacuum_mode, 'periodic_manual_vacuums': periodic_vacuums,
                  'autovacuum_disabled_on_measured_tables': True, 'vacuum_activity': activity,
                  'measured': measured, 'measured_total_relation_bytes': total_bytes(measured)}
        report['v1_entry_bytes'] = await conn.fetchval('SELECT sum(pg_column_size(entry)) FROM adaptive_decision_journal')
        if mode == 'v2':
            report['v2_live_value_bytes'] = await conn.fetchval('''SELECT
                (SELECT coalesce(sum(pg_column_size(entry)), 0) FROM adaptive_latest_evaluation)
              + (SELECT coalesce(sum(pg_column_size(payload)), 0) FROM adaptive_decision_events)
              + (SELECT coalesce(sum(pg_column_size(outcome)), 0) FROM adaptive_state_repetitions)
              + (SELECT coalesce(sum(pg_column_size(proposal_state) + pg_column_size(last_outcome)), 0)
                 FROM adaptive_learning_state)''')
            report['events_by_type'] = dict(await conn.fetch('''SELECT event_type, count(*) FROM adaptive_decision_events
                GROUP BY event_type ORDER BY event_type'''))
            report['repetitions'] = [dict(r) for r in await conn.fetch('''SELECT outcome->>'decision' AS decision,
                outcome->>'stability' AS stability, evaluation_count, new_evidence_count, repeated_evidence_count
                FROM adaptive_state_repetitions ORDER BY evaluation_count DESC''')]
        if final_vacuum:
            # Separately labelled: one manual VACUUM after the day, then measured again.
            await conn.execute(vacuum_sql)
            after = await sizes(conn, tables)
            report['after_final_vacuum'] = {'measured': after, 'total_relation_bytes': total_bytes(after)}
        return report
    finally:
        await pool.close()
        await conn.close()


async def main(final_vacuum=True):
    pg_isolation.authorize(DSN)  # fail closed before any connection
    results = {}
    for mode in ('v1', 'v2'):
        for vacuum_mode in VACUUM_MODES:
            results['%s_%s_vacuum' % (mode, vacuum_mode)] = await run(mode, vacuum_mode, final_vacuum)
    summary = {}
    for vacuum_mode in VACUUM_MODES:
        v1 = results['v1_%s_vacuum' % vacuum_mode]
        v2_report = results['v2_%s_vacuum' % vacuum_mode]
        summary['%s_vacuum' % vacuum_mode] = {
            'v1_bytes': v1['measured_total_relation_bytes'], 'v2_bytes': v2_report['measured_total_relation_bytes'],
            'reduction_factor': round(v1['measured_total_relation_bytes'] / v2_report['measured_total_relation_bytes'], 1)}
        if final_vacuum:
            summary['%s_vacuum_then_final_vacuum' % vacuum_mode] = {
                'v1_bytes': v1['after_final_vacuum']['total_relation_bytes'],
                'v2_bytes': v2_report['after_final_vacuum']['total_relation_bytes'],
                'reduction_factor': round(v1['after_final_vacuum']['total_relation_bytes']
                                          / v2_report['after_final_vacuum']['total_relation_bytes'], 1)}
    v2_live = results['v2_periodic_vacuum']['v2_live_value_bytes']
    summary['live_data'] = {'v1_entry_bytes': results['v1_periodic_vacuum']['v1_entry_bytes'],
                            'v2_live_value_bytes': v2_live,
                            'reduction_factor': round(results['v1_periodic_vacuum']['v1_entry_bytes'] / v2_live, 1)}
    print(json.dumps({'evaluations': MINUTES, 'periodic_vacuum_every': PERIODIC_VACUUM_EVERY,
                      'results': results, 'summary': summary}, indent=1, default=str))


if __name__ == '__main__':
    asyncio.run(main(final_vacuum='--no-final-vacuum' not in sys.argv[1:]))
