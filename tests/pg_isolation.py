"""Fail-closed isolation guard for every PostgreSQL-backed test and benchmark.

PostgreSQL tests are opt-in (each suite keeps its own ``*_POSTGRES=1`` flag) and may
only touch a database that ``tests/disposable_postgres.py`` provisioned for the
current run. There is no default DSN: a missing or unproven target raises
``UnsafeTestDatabase`` before any connection is opened, and every connection is
re-verified on the server before the caller can run SQL on it.

Run capability (issued by the provisioner, never by the test process)
--------------------------------------------------------------------
For each run the provisioner generates a run id, a random database password and a
256-bit capability token. The child's environment carries only

  * ``DESSMONITOR_PG_TEST_DSN``        -- a DSN *without* password,
  * ``DESSMONITOR_PG_TEST_RUN_ID``     -- 16 hex characters,
  * ``DESSMONITOR_PG_TEST_CAPABILITY`` -- the path of a private capability file.

The password and the token exist only in that file (mode 0600, in a 0700 temporary
directory owned by the same user, outside the repository, deleted at the end of the
run) and in memory. They are never printed, logged or put into an exception message.

Proof required before any connection (``authorize``; no socket is opened):
  1. Static DSN checks: exactly the provisioned DSN; one literal loopback host
     (127.0.0.1 or ::1); explicit, non-reserved port (not 5432/55439/55440); explicit
     user; no password and no query string (asyncpg honours ``?host=``/``?port=``
     overrides); database exactly ``dm_disposable_<run id>`` (reserved names such as
     ``easun_test`` are rejected; a ``_test`` suffix is never proof).
  2. The capability file is a regular, non-symlinked file owned by this user, mode
     0600 inside a 0700 directory, and names the same run id and DSN.
  3. The provisioner process recorded in the capability (pid and start time) is this
     process or one of its ancestors: only the provisioner and its child processes
     are authorized. A process outside that tree is refused even if it copied the
     environment and the file path.
  4. Docker reports the container recorded in the capability as running, labelled
     with this run id and with the token's digest, and bound only to
     ``127.0.0.1:<DSN port>``.
  5. Caller connection options pass a strict allowlist (timeouts and pool sizes);
     endpoint, credential, server-setting, connection-class/factory and SSL
     overrides are rejected (they could redirect a validated DSN).

Proof required on every new connection (``verify_connection``; read-only):
  6. ``current_database()`` is ``dm_disposable_<run id>``; the server setting
     ``dessmonitor.disposable_run_id`` equals the run id *and* appears in the server's
     own command line (``postmaster.opts`` in its data directory), so a client cannot
     forge it through startup options; the database carries the provisioner's marker.
  7. The ownership record ``dm_disposable_owner.run_owner`` holds exactly one row for
     this run whose token digest and provisioner pid/start time match the
     capability (a forged marker or a copied DSN/run id without the token fails).
  8. Every other client session on the server carries this run's session tag
     (an HMAC of the token, set by this guard as ``application_name``). Any foreign
     session -- an independent competing runner, a raw client, a second
     provisioner -- makes the guard fail closed.
  9. The server holds no database other than the system databases and
     ``dm_disposable_*`` databases, so it is not a shared server.

Threat boundary
---------------
This prevents accidental or careless use of the wrong database: stale defaults,
copied DSNs and run ids, environment leakage into unrelated processes, competing
test runners, endpoint redirection through connection options, forged markers and
pre-existing shared containers such as ``easun-test-pg``. It does NOT protect
against an attacker with unrestricted access to this user account, the provisioning
process, its memory or capability file, or the Docker daemon: such a party can read
the token and password and impersonate the run. The session-tag check is a
detection of concurrent use, not authentication (pg_stat_activity is readable by
the run's superuser role).
"""
import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import re
import stat
import subprocess
from urllib.parse import unquote, urlsplit

DSN_ENV = 'DESSMONITOR_PG_TEST_DSN'
RUN_ID_ENV = 'DESSMONITOR_PG_TEST_RUN_ID'
CAPABILITY_ENV = 'DESSMONITOR_PG_TEST_CAPABILITY'
RUN_ID_SETTING = 'dessmonitor.disposable_run_id'
DATABASE_PREFIX = 'dm_disposable_'
OWNER_SCHEMA = 'dm_disposable_owner'
OWNER_TABLE = OWNER_SCHEMA + '.run_owner'
CONTAINER_PREFIX = 'dm-disposable-pg-'
RUN_LABEL = 'dessmonitor.disposable-test-run'
OWNER_LABEL = 'dessmonitor.disposable-test-owner'
RESERVED_PORTS = frozenset({5432, 55439, 55440})
RESERVED_DATABASES = frozenset({'easun_test', 'dessmonitor_test', 'controls_perf_test', 'dessmonitor', 'easun',
                                'postgres', 'template0', 'template1'})
SYSTEM_DATABASES = frozenset({'postgres', 'template0', 'template1'})
LOOPBACK_HOSTS = frozenset({'127.0.0.1', '::1'})
CAPABILITY_VERSION = 1
_RUN_ID = re.compile(r'^[0-9a-f]{16}$')
_TOKEN = re.compile(r'^[0-9a-f]{64}$')
_DISPOSABLE_DATABASE = re.compile(r'^dm_disposable_[0-9a-f]{16}$')


class UnsafeTestDatabase(RuntimeError):
    """Raised when isolation cannot be proven. Never contains the DSN, password or token."""


# ----------------------------------------------------------------------------------------
# Connection-option allowlist (B-01)
# ----------------------------------------------------------------------------------------

def _non_negative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive_int(value):
    return _non_negative_int(value) and value > 0


def _positive_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _optional_positive_number(value):
    return value is None or _positive_number(value)


def _non_negative_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


CONNECT_OPTIONS = {
    'timeout': _positive_number,
    'command_timeout': _optional_positive_number,
    'statement_cache_size': _non_negative_int,
}
POOL_OPTIONS = dict(CONNECT_OPTIONS, **{
    'min_size': _non_negative_int,
    'max_size': _positive_int,
    'max_queries': _positive_int,
    'max_inactive_connection_lifetime': _non_negative_number,
    'init': callable,
})
# Named only to give a precise reason; anything not in the allowlist is rejected anyway.
FORBIDDEN_OPTIONS = frozenset({
    'dsn', 'host', 'hostaddr', 'port', 'database', 'dbname', 'service', 'target_session_attrs',  # endpoint
    'user', 'password', 'passfile', 'krbsrvname', 'gsslib', 'sslkey', 'sslcert', 'sslpassword',  # credentials
    'ssl', 'direct_tls', 'sslmode', 'sslrootcert',  # transport / trust
    'server_settings', 'options',  # could forge dessmonitor.disposable_run_id or application_name
    'connection_class', 'record_class', 'connect', 'setup', 'reset', 'loop',  # factories / bypasses
})


def check_options(options, allowed):
    """Reject every caller option that is not explicitly allowed (before any connection)."""
    for name in sorted(options):
        if name not in allowed:
            if name in FORBIDDEN_OPTIONS:
                raise UnsafeTestDatabase('connection option %r is forbidden: it can redirect or re-authenticate '
                                         'a validated connection' % name)
            raise UnsafeTestDatabase('connection option %r is not on the allowlist' % name)
        if not allowed[name](options[name]):
            raise UnsafeTestDatabase('connection option %r has an invalid value' % name)


def _single_dsn(args, kwargs):
    """Accept the DSN either positionally or as ``dsn=``, never both or anything else."""
    if len(args) > 1:
        raise UnsafeTestDatabase('only the DSN may be passed positionally')
    if args and 'dsn' in kwargs:
        raise UnsafeTestDatabase('DSN passed both positionally and as dsn=; ambiguous')
    if args:
        return args[0]
    return kwargs.pop('dsn', None)


# ----------------------------------------------------------------------------------------
# Static DSN validation
# ----------------------------------------------------------------------------------------

def database_marker(run_id):
    return 'dessmonitor disposable test database; run=%s' % run_id


def token_digest(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def session_tag(run_id, token):
    proof = hmac.new(token.encode('ascii'), ('session:' + run_id).encode('ascii'), hashlib.sha256).hexdigest()
    return 'dm-disposable/%s/%s' % (run_id, proof[:16])


def server_option(run_id):
    """How the run id appears in the server's own ``postmaster.opts`` (its command line)."""
    return '"-c" "%s=%s"' % (RUN_ID_SETTING, run_id)


def configured_dsn(environ=None):
    """The DSN from the environment, or '' (no fallback). Validation happens on use."""
    return (os.environ if environ is None else environ).get(DSN_ENV, '')


def _run_id(environ):
    run_id = environ.get(RUN_ID_ENV, '')
    if not _RUN_ID.match(run_id):
        raise UnsafeTestDatabase('%s missing or malformed; provision a database with '
                                 'tests/disposable_postgres.py' % RUN_ID_ENV)
    return run_id


def disposable_dsn(dsn=None, environ=None):
    """Validate the DSN statically and return it; raise before any connection otherwise."""
    environ = os.environ if environ is None else environ
    dsn = configured_dsn(environ) if dsn is None else dsn
    if not dsn:
        raise UnsafeTestDatabase('%s is not set; PostgreSQL tests have no default database' % DSN_ENV)
    if not isinstance(dsn, str) or dsn != environ.get(DSN_ENV):
        raise UnsafeTestDatabase('DSN differs from %s; only the provisioned DSN may be used' % DSN_ENV)
    run_id = _run_id(environ)
    try:
        parts = urlsplit(dsn)
        port = parts.port
        hostname = parts.hostname
        password = parts.password
    except ValueError as error:
        raise UnsafeTestDatabase('unparseable DSN (%s)' % type(error).__name__) from None
    if parts.scheme not in ('postgresql', 'postgres'):
        raise UnsafeTestDatabase('DSN scheme must be postgresql')
    if parts.query or parts.fragment:
        raise UnsafeTestDatabase('DSN query parameters are not allowed (they can override host/port/database)')
    netloc_host = parts.netloc.rsplit('@', 1)[-1]
    if ',' in netloc_host:
        raise UnsafeTestDatabase('DSN must name exactly one host')
    if hostname not in LOOPBACK_HOSTS or not ipaddress.ip_address(hostname).is_loopback:
        raise UnsafeTestDatabase('DSN host must be a literal loopback address (127.0.0.1 or ::1)')
    if port is None:
        raise UnsafeTestDatabase('DSN must name an explicit port')
    if port in RESERVED_PORTS:
        raise UnsafeTestDatabase('port %d is reserved for an existing shared database' % port)
    if not parts.username:
        raise UnsafeTestDatabase('DSN must name an explicit user')
    database = unquote(parts.path[1:]) if parts.path.startswith('/') else ''
    if database in RESERVED_DATABASES:
        raise UnsafeTestDatabase('database %r is reserved for an existing shared database' % database)
    if database != DATABASE_PREFIX + run_id:
        raise UnsafeTestDatabase('database must be %s<run id> for this run' % DATABASE_PREFIX)
    if password is not None:
        raise UnsafeTestDatabase('DSN must not carry a password; credentials come only from the run capability')
    return dsn


# ----------------------------------------------------------------------------------------
# Run capability (B-02)
# ----------------------------------------------------------------------------------------

class Capability:
    """Provisioner-issued proof of ownership. ``repr`` never shows the secrets."""
    __slots__ = ('run_id', 'dsn', 'password', 'token', 'provisioner_pid', 'provisioner_started',
                 'container', 'port')

    def __init__(self, run_id, dsn, password, token, provisioner_pid, provisioner_started, container, port):
        self.run_id, self.dsn, self.password, self.token = run_id, dsn, password, token
        self.provisioner_pid, self.provisioner_started = provisioner_pid, provisioner_started
        self.container, self.port = container, port

    def __repr__(self):
        return '<Capability run=%s container=%s>' % (self.run_id, self.container)

    @property
    def digest(self):
        return token_digest(self.token)

    @property
    def tag(self):
        return session_tag(self.run_id, self.token)

    def to_json(self):
        return json.dumps({'version': CAPABILITY_VERSION, 'run_id': self.run_id, 'dsn': self.dsn,
                           'password': self.password, 'token': self.token,
                           'provisioner_pid': self.provisioner_pid,
                           'provisioner_started': self.provisioner_started,
                           'container': self.container, 'port': self.port})


def _read_capability_file(path):
    if not isinstance(path, str) or not os.path.isabs(path):
        raise UnsafeTestDatabase('%s must be an absolute path to the provisioner capability file' % CAPABILITY_ENV)
    directory = os.path.dirname(path)
    try:
        dir_stat = os.lstat(directory)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except OSError:
        raise UnsafeTestDatabase('run capability file is missing or unreadable') from None
    try:
        file_stat = os.fstat(fd)
        uid = os.getuid()
        if not stat.S_ISDIR(dir_stat.st_mode) or dir_stat.st_uid != uid or dir_stat.st_mode & 0o077:
            raise UnsafeTestDatabase('run capability directory must be a private (0700) directory of this user')
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_uid != uid or file_stat.st_mode & 0o077:
            raise UnsafeTestDatabase('run capability file must be a private (0600) regular file of this user')
        with os.fdopen(fd, 'r', encoding='utf-8') as handle:
            fd = None
            return handle.read(65536)
    finally:
        if fd is not None:
            os.close(fd)


def load_capability(environ=None):
    """Read and validate the capability named by the environment (no network)."""
    environ = os.environ if environ is None else environ
    dsn = disposable_dsn(None, environ)
    run_id = _run_id(environ)
    path = environ.get(CAPABILITY_ENV, '')
    if not path:
        raise UnsafeTestDatabase('%s is not set; only processes started by tests/disposable_postgres.py '
                                 'may use PostgreSQL' % CAPABILITY_ENV)
    try:
        data = json.loads(_read_capability_file(path))
    except ValueError:
        raise UnsafeTestDatabase('run capability file is malformed') from None
    if not isinstance(data, dict) or data.get('version') != CAPABILITY_VERSION:
        raise UnsafeTestDatabase('run capability file has an unsupported version')
    if data.get('run_id') != run_id or data.get('dsn') != dsn:
        raise UnsafeTestDatabase('run capability belongs to a different run')
    token, password = data.get('token'), data.get('password')
    pid, started = data.get('provisioner_pid'), data.get('provisioner_started')
    container, port = data.get('container'), data.get('port')
    if not (isinstance(token, str) and _TOKEN.match(token)):
        raise UnsafeTestDatabase('run capability token is malformed')
    if not (isinstance(password, str) and len(password) >= 16):
        raise UnsafeTestDatabase('run capability credentials are malformed')
    if not (_positive_int(pid) and isinstance(started, str) and started):
        raise UnsafeTestDatabase('run capability provisioner identity is malformed')
    if container != CONTAINER_PREFIX + run_id or port != urlsplit(dsn).port:
        raise UnsafeTestDatabase('run capability container does not match the DSN')
    return Capability(run_id, dsn, password, token, pid, started, container, port)


def _process_info(pid):
    """(parent pid, start time) of ``pid``, or None if it does not exist."""
    result = subprocess.run(['ps', '-o', 'ppid=', '-o', 'lstart=', '-p', str(pid)],
                            capture_output=True, text=True, env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                                                                 'LC_ALL': 'C'})
    line = result.stdout.strip()
    if result.returncode != 0 or not line:
        return None
    ppid, _, started = line.partition(' ')
    return int(ppid), ' '.join(started.split())


def process_start_time(pid):
    info = _process_info(pid)
    if info is None:
        raise UnsafeTestDatabase('cannot determine process start time')
    return info[1]


def _require_provisioner_ancestor(capability):
    pid = os.getpid()
    for _ in range(128):
        if pid <= 1:
            break
        info = _process_info(pid)
        if info is None:
            break
        if pid == capability.provisioner_pid:
            if info[1] != capability.provisioner_started:
                raise UnsafeTestDatabase('the provisioner process recorded in the capability is gone '
                                         '(pid reused); refusing')
            return
        pid = info[0]
    raise UnsafeTestDatabase('this process is not the provisioner or one of its child processes; a copied '
                             'DSN, run id or capability is not authorization')


def _docker_inspect(name):
    result = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)[0]


def _require_container(capability):
    info = _docker_inspect(capability.container)
    if info is None:
        raise UnsafeTestDatabase('the run container does not exist')
    labels = (info.get('Config') or {}).get('Labels') or {}
    if info.get('Name', '').lstrip('/') != capability.container or labels.get(RUN_LABEL) != capability.run_id:
        raise UnsafeTestDatabase('the run container does not carry this run label')
    if labels.get(OWNER_LABEL) != capability.digest:
        raise UnsafeTestDatabase('the run container was not created for this capability')
    if not (info.get('State') or {}).get('Running'):
        raise UnsafeTestDatabase('the run container is not running')
    bindings = ((info.get('HostConfig') or {}).get('PortBindings') or {}).get('5432/tcp') or []
    if bindings != [{'HostIp': '127.0.0.1', 'HostPort': str(capability.port)}]:
        raise UnsafeTestDatabase('the run container is not bound only to 127.0.0.1 on the DSN port')


_AUTHORIZED = {}


def authorize(dsn=None, environ=None):
    """All pre-connection proofs (1-4). Returns the run capability; opens no database socket."""
    environ = os.environ if environ is None else environ
    if dsn is not None:
        disposable_dsn(dsn, environ)
    capability = load_capability(environ)
    key = (os.getpid(), capability.run_id, capability.digest, capability.provisioner_pid)
    if key not in _AUTHORIZED:
        _require_provisioner_ancestor(capability)
        _require_container(capability)
        _AUTHORIZED[key] = True
    return capability


# ----------------------------------------------------------------------------------------
# Post-connection verification
# ----------------------------------------------------------------------------------------

async def verify_connection(conn, capability=None):
    """Prove on ``conn`` that it is this run's server, database and owner (6-9). Read-only."""
    capability = authorize() if capability is None else capability
    run_id = capability.run_id
    expected = DATABASE_PREFIX + run_id
    row = await conn.fetchrow(
        '''SELECT current_database() AS database,
                  current_setting($1, true) AS run_setting,
                  pg_read_file('postmaster.opts') AS postmaster_opts,
                  shobj_description(d.oid, 'pg_database') AS marker
           FROM pg_database d WHERE d.datname = current_database()''', RUN_ID_SETTING)
    if row is None or row['database'] != expected:
        raise UnsafeTestDatabase('connected database is not %s' % expected)
    if row['run_setting'] != run_id or server_option(run_id) not in (row['postmaster_opts'] or ''):
        raise UnsafeTestDatabase('server was not started for this run (%s mismatch)' % RUN_ID_SETTING)
    if row['marker'] != database_marker(run_id):
        raise UnsafeTestDatabase('database marker for this run is missing')
    try:
        owners = await conn.fetch('SELECT run_id, token_sha256, provisioner_pid, provisioner_started FROM '
                                  + OWNER_TABLE)
    except Exception:  # undefined table/schema, permission: no proof of ownership
        raise UnsafeTestDatabase('ownership record for this run is missing') from None
    if len(owners) != 1:
        raise UnsafeTestDatabase('ownership record is missing or ambiguous; competing runner suspected')
    owner = owners[0]
    if owner['run_id'] != run_id or not hmac.compare_digest(owner['token_sha256'], capability.digest):
        raise UnsafeTestDatabase('ownership record belongs to a different capability; refusing')
    if owner['provisioner_pid'] != capability.provisioner_pid or \
            owner['provisioner_started'] != capability.provisioner_started:
        raise UnsafeTestDatabase('ownership record names a different provisioner; competing runner suspected')
    foreign_sessions = await conn.fetchval(
        '''SELECT count(*) FROM pg_stat_activity
           WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()
             AND application_name IS DISTINCT FROM $1''', capability.tag)
    if foreign_sessions:
        raise UnsafeTestDatabase('%d foreign client session(s) on the run server; competing runner suspected'
                                 % foreign_sessions)
    names = await conn.fetchval('SELECT array_agg(datname::text ORDER BY datname) FROM pg_database')
    foreign = [name for name in names or () if name not in SYSTEM_DATABASES and not _DISPOSABLE_DATABASE.match(name)]
    if foreign:
        raise UnsafeTestDatabase('server holds non-disposable databases %s; refusing a shared server' % foreign)
    return True


def _asyncpg():
    import asyncpg
    return asyncpg


_ORIGINAL_CONNECT = []  # real asyncpg.connect while app_connections() has replaced it


def _raw_connect():
    return _ORIGINAL_CONNECT[-1] if _ORIGINAL_CONNECT else _asyncpg().connect


def _guard_args(capability):
    return {'password': capability.password, 'server_settings': {'application_name': capability.tag}}


async def connect(*args, **kwargs):
    """asyncpg.connect to the verified disposable database (closed again if verification fails)."""
    dsn = _single_dsn(args, kwargs)
    check_options(kwargs, CONNECT_OPTIONS)
    return await _connect_verified(authorize(dsn), kwargs)


async def _connect_verified(capability, kwargs):
    conn = await _raw_connect()(capability.dsn, **kwargs, **_guard_args(capability))
    try:
        await verify_connection(conn, capability)
    except BaseException:
        await conn.close()
        raise
    return conn


async def create_pool(*args, **kwargs):
    """asyncpg.create_pool whose every new connection is verified before first use."""
    dsn = _single_dsn(args, kwargs)
    check_options(kwargs, POOL_OPTIONS)
    capability = authorize(dsn)
    caller_init = kwargs.pop('init', None)

    async def init(conn):
        await verify_connection(conn, capability)
        if caller_init is not None:
            await caller_init(conn)
    return await _asyncpg().create_pool(capability.dsn, init=init, **kwargs, **_guard_args(capability))


@contextlib.contextmanager
def app_connections():
    """Route application code that calls ``asyncpg.connect(DATABASE_URL, ...)`` through the guard.

    The capability is proven once on entry (so code inside may replace ``os.environ``).
    Inside the block, ``asyncpg.connect`` accepts only the provisioned DSN and allowlisted
    options and returns a verified connection; ``DATABASE_URL`` is the password-less DSN.
    """
    from unittest import mock
    asyncpg = _asyncpg()
    capability = authorize()

    async def guarded(*args, **kwargs):
        dsn = _single_dsn(args, kwargs)
        check_options(kwargs, CONNECT_OPTIONS)
        if dsn != capability.dsn:
            raise UnsafeTestDatabase('DSN differs from %s; only the provisioned DSN may be used' % DSN_ENV)
        return await _connect_verified(capability, kwargs)
    _ORIGINAL_CONNECT.append(_raw_connect())
    try:
        with mock.patch.object(asyncpg, 'connect', guarded), \
                mock.patch.dict(os.environ, {'DATABASE_URL': capability.dsn}):
            yield capability.dsn
    finally:
        _ORIGINAL_CONNECT.pop()
