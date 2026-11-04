"""Disconnected EASUN lifecycle. Only the exact in-memory fake can be used.

PostgreSQL advisory locks protect cooperating processes, not the mobile app.
No transaction remains open during the fake external call. No production
transport or environment-controlled enablement is supplied.
"""
import asyncio
from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

from app.control.easun_output_mapping import OutputField, parse_reported, validate_catalogue_value


class PostgresJournal:
    """Use one pinned asyncpg connection per lifecycle, with autocommit writes."""
    LOCK_ID = 5276389241085171
    SCHEMA = """CREATE TABLE IF NOT EXISTS easun_output_command_journal (
        id TEXT PRIMARY KEY, field TEXT NOT NULL, old_value TEXT NOT NULL,
        new_value TEXT NOT NULL, command_key TEXT NOT NULL, status TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS easun_output_reconciliation_events (
        id TEXT PRIMARY KEY, command_id TEXT NOT NULL REFERENCES easun_output_command_journal(id),
        kind TEXT NOT NULL, observed_value TEXT, detail TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE OR REPLACE FUNCTION easun_reconciliation_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'reconciliation events are append-only';
    END $$;
    DROP TRIGGER IF EXISTS easun_reconciliation_append_only_trigger ON easun_output_reconciliation_events;
    CREATE TRIGGER easun_reconciliation_append_only_trigger
        BEFORE UPDATE OR DELETE ON easun_output_reconciliation_events
        FOR EACH ROW EXECUTE FUNCTION easun_reconciliation_append_only()"""

    def __init__(self, pool):
        self.pool = pool
        self.conn = None
        self.locked = False

    async def acquire(self):
        if self.conn is not None:
            raise RuntimeError('journal already in use')
        self.conn = await self.pool.acquire()
        try:
            self.locked = bool(await self.conn.fetchval('SELECT pg_try_advisory_lock($1)', self.LOCK_ID))
            if not self.locked:
                conn, self.conn = self.conn, None
                await self._return_connection(conn)
            return self.locked
        except BaseException:
            conn, self.conn = self.conn, None
            self.locked = False
            if conn is not None:
                await self._return_connection(conn)
            raise

    async def available(self):
        if not self.locked or self.conn is None or self.conn.is_closed():
            raise RuntimeError('shared lock unavailable')
        # Table must be provisioned separately; never create production schema here.
        owned = await self.conn.fetchval("""SELECT EXISTS (
            SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
            AND granted AND objsubid=1 AND classid=(($1::bigint >> 32) & 4294967295)::oid
            AND objid=($1::bigint & 4294967295)::oid)""", self.LOCK_ID)
        if not owned:
            raise RuntimeError('shared lock lost')
        await self.conn.fetchval('SELECT count(*) FROM easun_output_command_journal')

    async def unresolved(self):
        return bool(await self.conn.fetchval("SELECT EXISTS (SELECT 1 FROM easun_output_command_journal WHERE status IS DISTINCT FROM 'VERIFIED')"))

    async def intent(self, command_id, field, old, new, key):
        await self.conn.execute("INSERT INTO easun_output_command_journal (id,field,old_value,new_value,command_key,status) VALUES ($1,$2,$3,$4,$5,'INTENT')", command_id, field, old, new, key)

    async def finish(self, command_id, status):
        if status not in ('VERIFIED', 'MISMATCH', 'UNKNOWN'):
            raise ValueError('invalid status')
        await self.conn.execute('UPDATE easun_output_command_journal SET status=$2, updated_at=now() WHERE id=$1', command_id, status)

    async def get_unresolved(self, command_id):
        return await self.conn.fetchrow("SELECT id,field,old_value,new_value,status FROM easun_output_command_journal WHERE id=$1 AND status IS DISTINCT FROM 'VERIFIED'", command_id)

    async def record_observation(self, event_id, command_id, kind, value):
        await self.conn.execute('INSERT INTO easun_output_reconciliation_events (id,command_id,kind,observed_value) VALUES ($1,$2,$3,$4)', event_id, command_id, kind, value)

    async def _return_connection(self, conn):
        """Return a checkout, including asyncpg 0.30's remotely closed case.

        In that version Pool.release() can return without cleaning a closed
        connection. Connection.terminate() performs its public cleanup path.
        """
        async def cleanup():
            try:
                if conn.is_closed():
                    conn.terminate()
                else:
                    await self.pool.release(conn)
            except BaseException:
                try:
                    conn.terminate()
                except Exception:
                    pass
                raise
            finally:
                # The server may close it between is_closed() and release().
                # A successfully returned proxy is detached and raises here.
                try:
                    if conn.is_closed():
                        conn.terminate()
                except Exception:
                    pass
        task = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def release(self):
        if self.conn is not None:
            try:
                if self.locked and not self.conn.is_closed():
                    await self.conn.fetchval('SELECT pg_advisory_unlock($1)', self.LOCK_ID)
            finally:
                conn, self.conn = self.conn, None
                self.locked = False
                await self._return_connection(conn)


class FakeDessTransport:
    """In-memory test double; no network client or callable is accepted."""
    def __init__(self, catalogue, current):
        self.catalogue = catalogue
        self.current = current
        self.calls = []
        self.write_error = None
        self.apply_write = True
        self.read_error = None

    async def read_catalogue(self, field):
        self.calls.append(('catalogue', field))
        return self.catalogue

    async def read_setting(self, field):
        self.calls.append(('read', field))
        if self.read_error:
            raise self.read_error
        return self.current

    async def write_setting(self, field, key):
        self.calls.append(('write', field, key))
        if self.apply_write:
            item = next(item for item in self.catalogue['item'] if item['key'] == key)
            self.current = item['val']
        if self.write_error:
            raise self.write_error


@dataclass(frozen=True)
class Authorization:
    enabled: bool = False
    voltage_class: int | None = None
    min_voltage: Decimal | None = None
    max_voltage: Decimal | None = None


@dataclass(frozen=True)
class Result:
    status: str
    reason: str
    command_id: str | None = None


class EASUNOutputActuator:
    """Only fake transport is accepted; this class is not a live write API."""
    def __init__(self, journal, transport, authorization=Authorization(), *, disabled=lambda: True):
        if type(transport) is not FakeDessTransport:
            raise TypeError('only the offline fake transport is supported')
        self.journal, self.transport = journal, transport
        self.authorization, self.disabled = authorization, disabled

    async def reconcile(self, command_id):
        """Record an observation, never infer command execution or resend."""
        acquired = False
        try:
            if not await self.journal.acquire():
                return Result('REFUSED', 'LOCK_UNAVAILABLE')
            acquired = True
            await self.journal.available()
            row = await self.journal.get_unresolved(command_id)
            if row is None:
                return Result('REFUSED', 'NO_UNRESOLVED_INTENT')
            field = OutputField(row['field'])
            try:
                raw = await self.transport.read_setting(field.value)
                current = parse_reported(field, raw)
                old = parse_reported(field, row['old_value'])
                target = row['new_value'] if field is OutputField.PRIORITY else Decimal(row['new_value'])
                kind = 'OBSERVED_TARGET' if current == target else ('OBSERVED_PREVIOUS' if current == old else 'OBSERVED_OTHER')
            except Exception:
                raw, kind = None, 'READBACK_UNAVAILABLE'
            await self.journal.available()
            event_id = str(uuid4())
            await self.journal.record_observation(event_id, command_id, kind, raw)
            return Result(kind, 'OBSERVATION_NOT_PROOF_OF_EXECUTION', event_id)
        except Exception:
            return Result('UNKNOWN', 'RECONCILIATION_FAILED')
        finally:
            if acquired:
                try: await self.journal.release()
                except Exception: pass

    async def resolve(self, command_id, observation_id, *, operator_authorized=False, reason=''):
        if operator_authorized is not True or type(reason) is not str or not reason.strip() or len(reason) > 500:
            return Result('REFUSED', 'OPERATOR_AUTHORIZATION_REQUIRED')
        acquired = False
        try:
            if not await self.journal.acquire(): return Result('REFUSED', 'LOCK_UNAVAILABLE')
            acquired = True
            await self.journal.available()
            if await self.journal.get_unresolved(command_id) is None:
                return Result('REFUSED', 'NO_UNRESOLVED_INTENT')
            # No DESS proof of request quiescence or authenticated recovery
            # protocol exists. Neither an observation nor a boolean may clear it.
            return Result('MANUAL_INTERVENTION_REQUIRED', 'REQUEST_QUIESCENCE_UNVERIFIED', command_id)
        except Exception:
            return Result('REFUSED', 'RESOLUTION_FAILED')
        finally:
            if acquired:
                try: await self.journal.release()
                except Exception: pass

    async def execute(self, field, expected, desired):
        auth = self.authorization
        if type(auth) is not Authorization or auth.enabled is not True or self.disabled() is not False:
            return Result('REFUSED', 'DISABLED')
        if type(field) is not OutputField or auth.voltage_class != 24:
            return Result('REFUSED', 'INVALID_FIELD_OR_CLASS')
        if (type(auth.min_voltage) is not Decimal or type(auth.max_voltage) is not Decimal
                or not auth.min_voltage.is_finite() or not auth.max_voltage.is_finite()
                or auth.min_voltage <= 0 or auth.min_voltage >= auth.max_voltage):
            return Result('REFUSED', 'LIMITS_UNAVAILABLE')
        command_id = None
        acquired = False
        try:
            if not await self.journal.acquire():
                return Result('REFUSED', 'LOCK_UNAVAILABLE')
            acquired = True
            await self.journal.available()
            if await self.journal.unresolved():
                return Result('REFUSED', 'UNRESOLVED_INTENT')
            catalogue = await self.transport.read_catalogue(field.value)
            key = validate_catalogue_value(field, desired, catalogue, installation_voltage_class=auth.voltage_class)
            current_text = await self.transport.read_setting(field.value)
            current = parse_reported(field, current_text)
            expected_value = parse_reported(field, expected)
            desired_value = desired if field is OutputField.PRIORITY else Decimal(desired)
            if current != expected_value:
                return Result('REFUSED', 'CURRENT_VALUE_CHANGED')
            if current == desired_value:
                return Result('REFUSED', 'DUPLICATE')
            if field is not OutputField.PRIORITY and not auth.min_voltage <= desired_value <= auth.max_voltage:
                return Result('REFUSED', 'OUTSIDE_APPROVED_LIMITS')
            if self.disabled() is not False:
                return Result('REFUSED', 'DISABLED')
            command_id = str(uuid4())
            await self.journal.intent(command_id, field.value, current_text, desired, key)
            # Re-check inhibit and pinned connection after durable intent.
            if self.disabled() is not False:
                await self.journal.finish(command_id, 'MISMATCH')
                return Result('REFUSED', 'DISABLED', command_id)
            await self.journal.available()
            # External changes can race even this last read: the DB lock cannot
            # prevent the manufacturer application from changing the device.
            if parse_reported(field, await self.transport.read_setting(field.value)) != current:
                await self.journal.finish(command_id, 'MISMATCH')
                return Result('REFUSED', 'CURRENT_VALUE_CHANGED', command_id)
            if self.disabled() is not False:
                await self.journal.finish(command_id, 'MISMATCH')
                return Result('REFUSED', 'DISABLED', command_id)
            await self.journal.available()
            try:
                await self.transport.write_setting(field.value, key)
                reported = parse_reported(field, await self.transport.read_setting(field.value))
                status = 'VERIFIED' if reported == desired_value else 'MISMATCH'
            except Exception:
                status = 'UNKNOWN'
            await self.journal.available()
            await self.journal.finish(command_id, status)
            return Result(status, 'REPORTED_SETTING_ONLY', command_id)
        except Exception:
            # A persisted INTENT stays unresolved after any unexpected failure.
            return Result('UNKNOWN' if command_id else 'REFUSED', 'STORE_OR_INPUT_FAILURE', command_id)
        finally:
            if acquired:
                try:
                    await self.journal.release()
                except Exception:
                    pass
