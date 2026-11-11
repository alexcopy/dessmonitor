#!/usr/bin/env python3
"""Operator tool for Adaptive Decision Journal v2 (migration 002 must be applied).

    DATABASE_URL=... python scripts/adaptive_journal_v2.py status
    DATABASE_URL=... python scripts/adaptive_journal_v2.py cutover --confirm [--allow-empty-state]
    DATABASE_URL=... python scripts/adaptive_journal_v2.py rollback --confirm

Cutover and rollback take the learner's advisory lock, so they wait for any
in-flight evaluation and never interleave with worker writes. Neither touches
inverter settings or adaptive-learning enablement. The DSN is read from the
environment only and is never printed.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import web_queries  # noqa: E402
from app.control import adaptive_journal_v2  # noqa: E402


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('status', 'cutover', 'rollback'))
    parser.add_argument('--confirm', action='store_true', help='required for cutover and rollback')
    parser.add_argument('--allow-empty-state', action='store_true',
                        help='cutover only: start from an empty learner state when no v1 state exists')
    parser.add_argument('--note', default='', help='short operator note stored with the mode change')
    args = parser.parse_args(argv)
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        parser.error('DATABASE_URL is not set')
    if args.command == 'status':
        status = await web_queries.journal_v2_status(database_url, events_limit=10)
        status.pop('latest', None)
        print(json.dumps(status, default=str, indent=2))
        return 0
    if not args.confirm:
        parser.error('%s changes the journal writer; rerun with --confirm' % args.command)
    import asyncpg
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1, command_timeout=30)
    try:
        now = datetime.now(timezone.utc)
        if args.command == 'cutover':
            result = await adaptive_journal_v2.cutover_to_v2(pool, now, allow_empty_state=args.allow_empty_state,
                                                             note=args.note)
        else:
            result = await adaptive_journal_v2.rollback_to_v1(pool, now, note=args.note)
    finally:
        await pool.close()
    print(json.dumps(result, default=str, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
