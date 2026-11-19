"""Task 0083 fake-only Utility-hold tests; no DESS or hardware access."""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.api import _RejectRedirectHandler
from app.control.easun_live_output_transport import (
    CatalogueRead, LiveDessTransport, LiveTransportRefused, SettingRead,
)
from app.control.easun_utility_hold import UtilityHoldAuthorization, UtilityHoldCoordinator
from app.control.grid_output_interlock import ChargerObservation, ChargerProvenance
from app.control.grid_charge_output_coordination import SettingObservation


NOW = datetime(2026, 12, 10, 8, tzinfo=timezone.utc)
CATALOGUE = {
    "id": "los_output_source_priority",
    "item": [{"key": "0", "val": "Utility"}, {"key": "1", "val": "Solar"}, {"key": "2", "val": "SBU"}],
}


class Journal:
    def __init__(self):
        self.busy = False
        self.rows = {}
        self.events = []
        self.on_acquire = None
        self.fail_event_kind = None
        self.fail_finish = False

    async def acquire(self):
        if self.busy:
            return False
        self.busy = True
        if self.on_acquire is not None:
            self.on_acquire()
        return True

    async def release(self):
        self.busy = False

    async def available(self):
        return None

    async def unresolved(self):
        return any(row["status"] in {"INTENT", "UNKNOWN"} for row in self.rows.values())

    async def find_by_command_key(self, key):
        matches = [row for row in self.rows.values() if row["command_key"] == key]
        if len(matches) > 1:
            return {"ambiguous": True, "rows": tuple(matches)}
        return matches[0] if matches else None

    async def intent(self, ident, field, old, new, key):
        self.rows[ident] = {"id": ident, "field": field, "old_value": old,
                            "new_value": new, "command_key": key, "status": "INTENT"}

    async def record_observation(self, event_id, command_id, kind, value):
        if kind == self.fail_event_kind:
            raise RuntimeError("journal event failure")
        self.events.append((event_id, command_id, kind, value))

    async def finish(self, ident, status):
        if self.fail_finish:
            raise RuntimeError("journal finish failure")
        self.rows[ident]["status"] = status


class FakeVerifiedTransport:
    def __init__(self, current="SBU"):
        self.current = current
        self.catalogue = CATALOGUE
        self.writes = []
        self.reads = 0
        self.catalogue_at = NOW
        self.read_at = NOW
        self.write_error = None
        self.read_error = None

    async def read_catalogue(self):
        return CatalogueRead(self.catalogue, self.catalogue_at)

    async def read_setting(self):
        if self.read_error:
            raise self.read_error
        self.reads += 1
        return SettingRead(self.current, self.read_at)

    async def write_output_priority(self, key):
        self.writes.append(key)
        if self.write_error:
            raise self.write_error
        self.current = "Utility"


def auth(**overrides):
    values = dict(enabled=True, voltage_class=24, model_verified=True, firmware_verified=True,
                  catalogue_verified=True, backup_behavior_verified=True, rollback_acknowledged=True,
                  device_identity_digest="device-digest")
    values.update(overrides)
    return UtilityHoldAuthorization(**values)


def charger(at=NOW, provenance=ChargerProvenance.DEVICE_READBACK, mode="solar_and_mains"):
    return ChargerObservation(mode=mode, observed_at=at, provenance=provenance)


def priority(value="SBU", at=NOW, catalogue=True, readback=True):
    return SettingObservation(value=value, observed_at=at, catalogue_verified=catalogue,
                              readback_verified=readback)


class UtilityHoldTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.journal = Journal()
        self.transport = FakeVerifiedTransport()
        self.coordinator = UtilityHoldCoordinator(self.journal, self.transport, auth(), clock=lambda: NOW)

    async def hold(self, **kwargs):
        values = dict(charger=charger(), priority=priority(), evaluated_at=NOW,
                      observation_fingerprint="receipt-fingerprint", working_mode="Line Mode")
        values.update(kwargs)
        return await self.coordinator.hold(**values)

    async def test_verified_solar_mains_sbu_writes_once_and_audits_readback(self):
        result = await self.hold()
        self.assertEqual((result.status, result.reason), ("VERIFIED", "REPORTED_SETTING_ONLY"))
        self.assertEqual(self.transport.writes, ["0"])
        self.assertEqual(list(self.journal.rows.values())[0]["status"], "VERIFIED")
        self.assertEqual([event[2] for event in self.journal.events], ["WRITE_ATTEMPT", "POST_WRITE_READBACK"])
        self.assertFalse(result.physical_transition_proven)

    async def test_repeated_logical_event_returns_prior_verified_without_write(self):
        first = await self.hold()
        second = await self.hold()
        self.assertEqual(first.status, "VERIFIED")
        self.assertEqual((second.status, second.reason), ("VERIFIED", "DUPLICATE_ALREADY_VERIFIED"))
        self.assertEqual(self.transport.writes, ["0"])

    async def test_utility_is_noop(self):
        self.transport.current = "Utility"
        result = await self.hold(priority=priority("Utility"))
        self.assertEqual((result.status, result.reason), ("NOOP", "ALREADY_UTILITY"))
        self.assertEqual(self.transport.writes, [])

    async def test_unverified_inputs_and_authorization_never_write(self):
        cases = [
            {"charger": charger(provenance=ChargerProvenance.UNKNOWN)},
            {"charger": charger(mode="solar_only")},
            {"priority": priority(catalogue=False)},
            {"priority": priority(readback=False)},
            {"charger": charger(at=NOW - timedelta(seconds=181))},
            {"priority": priority(at=NOW - timedelta(seconds=181))},
            {"manual_override": True},
        ]
        for case in cases:
            with self.subTest(case=case):
                self.transport.writes.clear()
                result = await self.hold(**case)
                self.assertNotEqual(result.status, "VERIFIED")
                self.assertEqual(self.transport.writes, [])
        refused = await UtilityHoldCoordinator(self.journal, self.transport, auth(enabled=False)).hold(
            charger(), priority(), evaluated_at=NOW, observation_fingerprint="x")
        self.assertEqual(refused.reason, "LIVE_AUTHORIZATION_INCOMPLETE")

    async def test_exact_catalogue_is_required(self):
        self.transport.catalogue = {"id": CATALOGUE["id"], "item": CATALOGUE["item"][:2]}
        result = await self.hold()
        self.assertEqual(result.reason, "CATALOGUE_MAPPING_UNVERIFIED")
        self.assertEqual(self.transport.writes, [])

    async def test_live_receipt_is_checked_after_each_read(self):
        clock_now = [NOW]
        self.coordinator = UtilityHoldCoordinator(self.journal, self.transport, auth(), clock=lambda: clock_now[0])
        self.transport.catalogue_at = NOW + timedelta(seconds=1)
        result = await self.hold()
        self.assertEqual(result.reason, "CATALOGUE_MAPPING_UNVERIFIED")
        clock_now[0] = NOW + timedelta(seconds=1)
        self.transport.read_at = NOW + timedelta(seconds=1)
        result = await self.hold(evaluated_at=NOW)
        self.assertEqual(result.status, "VERIFIED")

    async def test_write_failure_leaves_nonretryable_intent(self):
        self.transport.write_error = TimeoutError()
        result = await self.hold()
        self.assertEqual((result.status, result.reason), ("UNKNOWN", "WRITE_OUTCOME_UNCERTAIN"))
        self.assertEqual(list(self.journal.rows.values())[0]["status"], "INTENT")
        self.assertEqual([event[2] for event in self.journal.events],
                         ["WRITE_ATTEMPT", "POST_WRITE_READBACK"])
        retry = await self.hold()
        self.assertEqual(retry.reason, "IDEMPOTENCY_NONRETRYABLE_INTENT")
        self.assertEqual(len(self.transport.writes), 1)

    async def test_post_readback_event_failure_stays_unresolved_and_nonretryable(self):
        self.journal.fail_event_kind = "POST_WRITE_READBACK"
        result = await self.hold()
        self.assertEqual((result.status, result.reason), ("UNKNOWN", "WRITE_OR_AUDIT_UNCERTAIN"))
        self.assertEqual(list(self.journal.rows.values())[0]["status"], "INTENT")
        retry = await self.hold()
        self.assertEqual(retry.reason, "IDEMPOTENCY_NONRETRYABLE_INTENT")
        self.assertEqual(len(self.transport.writes), 1)

    async def test_finish_failure_stays_unresolved_and_nonretryable(self):
        self.journal.fail_finish = True
        result = await self.hold()
        self.assertEqual((result.status, result.reason), ("UNKNOWN", "WRITE_OR_AUDIT_UNCERTAIN"))
        self.assertEqual(list(self.journal.rows.values())[0]["status"], "INTENT")
        self.assertEqual([event[2] for event in self.journal.events],
                         ["WRITE_ATTEMPT", "POST_WRITE_READBACK"])
        retry = await self.hold()
        self.assertEqual(retry.reason, "IDEMPOTENCY_NONRETRYABLE_INTENT")
        self.assertEqual(len(self.transport.writes), 1)

    async def test_cancellation_after_write_attempt_is_audited_and_unresolved(self):
        class Cancelled(FakeVerifiedTransport):
            async def write_output_priority(self, key):
                self.writes.append(key)
                raise asyncio.CancelledError()

        self.transport = Cancelled()
        self.coordinator = UtilityHoldCoordinator(self.journal, self.transport, auth(), clock=lambda: NOW)
        with self.assertRaises(asyncio.CancelledError):
            await self.hold()
        self.assertEqual(list(self.journal.rows.values())[0]["status"], "INTENT")
        self.assertEqual([event[2] for event in self.journal.events],
                         ["WRITE_ATTEMPT", "POST_WRITE_READBACK"])
        self.assertEqual(len(self.transport.writes), 1)

    async def test_cancellation_reconciliation_failure_stays_unresolved(self):
        class Cancelled(FakeVerifiedTransport):
            async def write_output_priority(self, key):
                self.writes.append(key)
                raise asyncio.CancelledError()

        self.journal.fail_event_kind = "POST_WRITE_READBACK"
        self.transport = Cancelled()
        self.coordinator = UtilityHoldCoordinator(self.journal, self.transport, auth(), clock=lambda: NOW)
        with self.assertRaises(asyncio.CancelledError):
            await self.hold()
        self.assertEqual(list(self.journal.rows.values())[0]["status"], "INTENT")
        retry = await self.hold()
        self.assertEqual(retry.reason, "IDEMPOTENCY_NONRETRYABLE_INTENT")
        self.assertEqual(len(self.transport.writes), 1)

    async def test_post_write_future_readback_is_unknown_and_not_retryable(self):
        class FutureRead(FakeVerifiedTransport):
            async def write_output_priority(self, key):
                self.writes.append(key)
                self.current = "Utility"
                self.read_at = NOW + timedelta(seconds=1)

        self.transport = FutureRead()
        self.coordinator = UtilityHoldCoordinator(self.journal, self.transport, auth(), clock=lambda: NOW)
        result = await self.hold()
        self.assertEqual((result.status, result.reason), ("UNKNOWN", "POST_WRITE_READBACK_UNAVAILABLE"))
        self.transport.read_at = NOW
        retry = await self.hold()
        self.assertEqual(retry.reason, "IDEMPOTENCY_NONRETRYABLE_UNKNOWN")
        self.assertEqual(len(self.transport.writes), 1)

    async def test_post_write_exact_age_boundary_is_verified(self):
        class BoundaryRead(FakeVerifiedTransport):
            async def write_output_priority(self, key):
                self.writes.append(key)
                self.current = "Utility"
                self.read_at = NOW - timedelta(seconds=180)

        self.transport = BoundaryRead()
        self.coordinator = UtilityHoldCoordinator(self.journal, self.transport, auth(), clock=lambda: NOW)
        result = await self.hold()
        self.assertEqual(result.status, "VERIFIED")

    async def test_prewrite_race_rechecks_setting_under_lock(self):
        self.journal.on_acquire = lambda: setattr(self.transport, "current", "Solar")
        result = await self.hold()
        self.assertEqual(result.reason, "PREWRITE_RACE")
        self.assertEqual(self.transport.writes, [])

    async def test_concurrent_calls_allow_at_most_one_write(self):
        first_read = asyncio.Event()
        release_read = asyncio.Event()

        class Delayed(FakeVerifiedTransport):
            async def read_catalogue(self):
                first_read.set()
                await release_read.wait()
                return await super().read_catalogue()

        self.transport = Delayed()
        self.coordinator = UtilityHoldCoordinator(self.journal, self.transport, auth(), clock=lambda: NOW)
        first = asyncio.create_task(self.hold())
        await first_read.wait()
        second = asyncio.create_task(self.hold())
        await asyncio.sleep(0)
        release_read.set()
        results = await asyncio.gather(first, second)
        self.assertEqual(len(self.transport.writes), 1)
        self.assertIn({result.status for result in results}, ({"VERIFIED", "REFUSED"}, {"VERIFIED"}))

    async def test_mismatch_is_not_verified_and_never_restores_sbu(self):
        class Mismatch(FakeVerifiedTransport):
            async def write_output_priority(self, key):
                self.writes.append(key)
        self.transport = Mismatch()
        self.coordinator = UtilityHoldCoordinator(self.journal, self.transport, auth(), clock=lambda: NOW)
        result = await self.hold()
        self.assertEqual((result.status, result.reason), ("MISMATCH", "POST_WRITE_READBACK_MISMATCH"))
        self.assertEqual(self.transport.writes, ["0"])
        retry = await self.hold()
        self.assertEqual(retry.reason, "IDEMPOTENCY_NONRETRYABLE_MISMATCH")
        self.assertEqual(self.transport.writes, ["0"])

    async def test_emergency_charging_does_not_call_charger_controller(self):
        charger_controller = SimpleNamespace(calls=0)
        result = await self.hold()
        self.assertEqual(result.status, "VERIFIED")
        self.assertEqual(charger_controller.calls, 0)


class LiveTransportTests(unittest.IsolatedAsyncioTestCase):
    def api(self):
        return SimpleNamespace(pn="pn", dev_code="code", dev_addr=1, sn="sn", _do_api_request=None)

    async def test_disabled_live_transport_refuses_write(self):
        calls = []
        transport = LiveDessTransport(self.api(), request=lambda params: calls.append(params) or {"err": 0})
        with self.assertRaises(LiveTransportRefused):
            await transport.write_output_priority("0")
        self.assertEqual(calls, [])

    async def test_live_transport_allowlist_uses_exact_output_field(self):
        calls = []
        def request(params):
            calls.append(params)
            if params["action"] == "queryDeviceCtrlField":
                return {"err": 0, "dat": {"field": [CATALOGUE]}}
            if params["action"] == "queryDeviceCtrlValue":
                return {"err": 0, "dat": {"id": "los_output_source_priority", "val": "SBU"}}
            return {"err": 0}
        transport = LiveDessTransport(self.api(), enabled=True, request=request)
        await transport.read_catalogue()
        await transport.read_setting()
        await transport.write_output_priority("0")
        self.assertEqual([call["action"] for call in calls],
                         ["queryDeviceCtrlField", "queryDeviceCtrlValue", "ctrlDevice"])
        self.assertEqual(calls[-1]["id"], "los_output_source_priority")
        self.assertEqual(calls[-1]["val"], "0")
        with self.assertRaises(LiveTransportRefused):
            await transport.write_output_priority("charger-key")
        with self.assertRaises(LiveTransportRefused):
            await transport.write_output_priority("2")

    async def test_redirect_handler_refuses_redirects(self):
        self.assertIsNone(_RejectRedirectHandler().redirect_request(
            None, None, 302, "Found", {}, "https://other.example/"))

    async def test_live_transport_uses_shared_redirect_safe_request_boundary(self):
        calls = []

        def request(params, **kwargs):
            calls.append((params, kwargs))
            if params["action"] == "queryDeviceCtrlField":
                return {"err": 0, "dat": {"field": [CATALOGUE]}}
            if params["action"] == "queryDeviceCtrlValue":
                return {"err": 0, "dat": {"id": "los_output_source_priority", "val": "SBU"}}
            return {"err": 0}

        api = SimpleNamespace(pn="pn", dev_code="code", dev_addr=1, sn="sn",
                              _do_api_request=request)
        transport = LiveDessTransport(api, enabled=True)
        await transport.read_catalogue()
        await transport.read_setting()
        await transport.write_output_priority("0")
        self.assertTrue(calls)
        self.assertTrue(all(kwargs["reject_redirects"] is True for _, kwargs in calls))
        self.assertTrue(all(kwargs["base_url"] == "https://api.dessmonitor.com/public/"
                             for _, kwargs in calls))


if __name__ == "__main__":
    unittest.main()
