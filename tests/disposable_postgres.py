"""Run a command against a brand-new, uniquely named, disposable PostgreSQL container.

    python tests/disposable_postgres.py run -- python -m unittest discover -s tests

For each invocation this creates a new Docker container ``dm-disposable-pg-<run id>``
(tmpfs data directory, bound to 127.0.0.1 on a free non-reserved port, random password)
with database ``dm_disposable_<run id>``, and issues a run capability (see
``tests/pg_isolation.py``): a random 256-bit token whose SHA-256 digest is put on the
container label and into the ownership record ``dm_disposable_owner.run_owner``, which
the provisioner claims atomically (an existing record means a competing runner and is
refused). The server is started with ``-c dessmonitor.disposable_run_id=<run id>`` and
the database carries the run marker.

The command runs with ``DESSMONITOR_PG_TEST_DSN`` (no password), ``DESSMONITOR_PG_TEST_RUN_ID``
and ``DESSMONITOR_PG_TEST_CAPABILITY``, the path of a 0600 file in a private 0700
temporary directory that holds the password and token. Inherited ``DATABASE_URL`` and
libpq ``PG*`` variables are removed so nothing can fall back to another database.
Afterwards exactly that container is removed (its name and both labels are checked
first) and the capability directory is deleted; pre-existing containers are never
inspected for reuse, modified or removed. The DSN, password and token are never printed.
"""
import argparse
import asyncio
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg_isolation  # noqa: E402

IMAGE = 'postgres:16-alpine'
LABEL = 'dessmonitor.disposable-test-run'
CONTAINER_PREFIX = 'dm-disposable-pg-'
LIBPQ_ENV = re.compile(r'^PG[A-Z]+$')  # PGHOST, PGPORT, PGDATABASE, PGSERVICE, PGPASSWORD, ...
SCRUBBED_ENV = ('DATABASE_URL',)


def log(message):
    print('[disposable-postgres] %s' % message, file=sys.stderr, flush=True)


def docker(*args, check=True):
    return subprocess.run(('docker',) + args, check=check, capture_output=True, text=True)


def free_port():
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        if port not in pg_isolation.RESERVED_PORTS:
            return port
    raise RuntimeError('no free non-reserved port')


def inspect(name):
    result = docker('inspect', name, check=False)
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)[0]


async def claim_database(capability, deadline):
    """Wait for the server, prove it is this run's container, then claim ownership atomically."""
    import asyncpg
    while True:
        try:
            conn = await asyncpg.connect(capability.dsn, password=capability.password, timeout=3,
                                         server_settings={'application_name': capability.tag})
            break
        except (OSError, asyncpg.PostgresError, asyncio.TimeoutError, ConnectionError):
            if time.monotonic() > deadline:
                raise
            await asyncio.sleep(0.5)
    try:
        # The server identity must already be this run's before anything is written.
        setting = await conn.fetchrow("SELECT current_setting($1, true) AS value, "
                                      "pg_read_file('postmaster.opts') AS opts", pg_isolation.RUN_ID_SETTING)
        if setting['value'] != capability.run_id or \
                pg_isolation.server_option(capability.run_id) not in (setting['opts'] or ''):
            raise pg_isolation.UnsafeTestDatabase('server is not this run\'s container')
        database = pg_isolation.DATABASE_PREFIX + capability.run_id
        if await conn.fetchval('SELECT current_database()') != database:
            raise pg_isolation.UnsafeTestDatabase('unexpected database')
        async with conn.transaction():
            # CREATE SCHEMA without IF NOT EXISTS: an existing claim (competing runner) aborts.
            try:
                await conn.execute('CREATE SCHEMA %s' % pg_isolation.OWNER_SCHEMA)
            except asyncpg.DuplicateSchemaError:
                raise pg_isolation.UnsafeTestDatabase('database already claimed; competing runner') from None
            await conn.execute('''CREATE TABLE %s (run_id TEXT PRIMARY KEY, token_sha256 TEXT NOT NULL,
                provisioner_pid INTEGER NOT NULL, provisioner_started TEXT NOT NULL,
                claimed_at TIMESTAMPTZ NOT NULL DEFAULT now())''' % pg_isolation.OWNER_TABLE)
            await conn.execute('INSERT INTO %s (run_id, token_sha256, provisioner_pid, provisioner_started) '
                               'VALUES ($1, $2, $3, $4)' % pg_isolation.OWNER_TABLE, capability.run_id,
                               capability.digest, capability.provisioner_pid, capability.provisioner_started)
            await conn.execute("COMMENT ON DATABASE %s IS '%s'" % (database,
                                                                  pg_isolation.database_marker(capability.run_id)))
        await pg_isolation.verify_connection(conn, capability)
    finally:
        await conn.close()


def remove(name, run_id, digest):
    info = inspect(name)
    if info is None:
        return
    labels = info['Config']['Labels'] or {}
    if labels.get(LABEL) != run_id or labels.get(pg_isolation.OWNER_LABEL) != digest or \
            not name.startswith(CONTAINER_PREFIX):
        log('NOT removing %s: labels do not match this run' % name)
        return
    docker('rm', '-f', '-v', name)
    log('removed container %s' % name)


def write_capability(capability):
    """Private directory (0700) + file (0600, created exclusively); returns (directory, path)."""
    directory = tempfile.mkdtemp(prefix='dm-disposable-cap-')
    os.chmod(directory, 0o700)
    path = os.path.join(directory, 'capability.json')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(capability.to_json())
    return directory, path


def run(command, startup_timeout):
    run_id = secrets.token_hex(8)
    name = CONTAINER_PREFIX + run_id
    database = pg_isolation.DATABASE_PREFIX + run_id
    port = free_port()
    dsn = 'postgresql://postgres@127.0.0.1:%d/%s' % (port, database)  # password only in the capability
    capability = pg_isolation.Capability(
        run_id, dsn, secrets.token_hex(16), secrets.token_hex(32), os.getpid(),
        pg_isolation.process_start_time(os.getpid()), name, port)
    if inspect(name) is not None:
        raise RuntimeError('container name collision; refusing to touch %s' % name)
    log('starting %s (%s) on 127.0.0.1:%d, database %s' % (name, IMAGE, port, database))
    created = False
    capability_dir = None
    try:
        docker('run', '-d', '--name', name, '--label', '%s=%s' % (LABEL, run_id),
               '--label', '%s=%s' % (pg_isolation.OWNER_LABEL, capability.digest),
               '--tmpfs', '/var/lib/postgresql/data', '-p', '127.0.0.1:%d:5432' % port,
               '-e', 'POSTGRES_PASSWORD=%s' % capability.password, '-e', 'POSTGRES_DB=%s' % database,
               IMAGE, '-c', '%s=%s' % (pg_isolation.RUN_ID_SETTING, run_id), '-c', 'fsync=off')
        created = True
        pg_isolation._require_container(capability)
        asyncio.run(claim_database(capability, time.monotonic() + startup_timeout))
        capability_dir, capability_path = write_capability(capability)
        env = {key: value for key, value in os.environ.items()
               if key not in SCRUBBED_ENV and not LIBPQ_ENV.match(key)}
        env[pg_isolation.DSN_ENV] = dsn
        env[pg_isolation.RUN_ID_ENV] = run_id
        env[pg_isolation.CAPABILITY_ENV] = capability_path
        pg_isolation.load_capability(env)
        log('verified identity: container labels, loopback binding, server run setting, database marker, '
            'ownership record')
        log('running: %s' % ' '.join(command))
        return subprocess.run(command, env=env).returncode
    finally:
        if created:
            remove(name, run_id, capability.digest)
        if capability_dir is not None:
            shutil.rmtree(capability_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='action', required=True)
    run_parser = sub.add_parser('run', help='provision, run the command, then remove the container')
    run_parser.add_argument('--startup-timeout', type=float, default=60)
    run_parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('a command is required after --')
    sys.exit(run(command, args.startup_timeout))


if __name__ == '__main__':
    main()
