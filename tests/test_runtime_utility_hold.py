import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.control.easun_live_output_transport import CatalogueRead, ChargerSettingRead, SettingRead
from app.control.runtime_utility_hold import RuntimeUtilityHold


NOW = datetime.now(timezone.utc).replace(microsecond=0)


class FakeTransport:
    def __init__(self, api, *, enabled):
        self.calls = []

    async def read_catalogue(self):
        self.calls.append("catalogue")
        return CatalogueRead({"id": "los_output_source_priority", "item": [
            {"key": "0", "val": "Utility"}, {"key": "1", "val": "Solar"},
            {"key": "2", "val": "SBU"},
        ]}, NOW)

    async def read_charger_setting(self):
        self.calls.append("charger")
        return ChargerSettingRead("Solar and mains", NOW)

    async def read_setting(self):
        self.calls.append("priority")
        return SettingRead("SBU", NOW)


class FakeDiagnostics:
    def __init__(self):
        self.observed = None
        self.executed = 0

    def observe_coordination(self, *args, **kwargs):
        self.observed = kwargs

    async def execute_utility_hold(self, executor):
        self.executed += 1
        return None


class RuntimeUtilityHoldTests(unittest.TestCase):
    def test_runtime_uses_fresh_readbacks_and_keeps_charger_provenance(self):
        diagnostics = FakeDiagnostics()
        api = SimpleNamespace(pn="pn", dev_code="code", dev_addr=1, sn="sn")
        authorization = SimpleNamespace(ready=True)
        with patch("app.control.runtime_utility_hold.LiveDessTransport", FakeTransport):
            runtime = RuntimeUtilityHold(api, object(), authorization)
            asyncio.run(runtime.tick(
                diagnostics, SimpleNamespace(working_state="Line Mode"),
                received_at=NOW, evaluated_at=NOW, fingerprint="fingerprint"))
        self.assertEqual(runtime.transport.calls, ["catalogue", "charger", "priority"])
        charger = diagnostics.observed["charger_observation"]
        priority = diagnostics.observed["priority_observation"]
        self.assertEqual(charger.mode, "solar_and_mains")
        self.assertEqual(charger.provenance.value, "DEVICE_READBACK")
        self.assertEqual(priority.value, "SBU")
        self.assertTrue(priority.catalogue_verified)
        self.assertTrue(priority.readback_verified)
        self.assertEqual(diagnostics.executed, 1)

    def test_catalogue_verification_rejects_extra_or_aliased_values(self):
        valid = {"id": "los_output_source_priority", "item": [
            {"key": "0", "val": "Utility"}, {"key": "1", "val": "Solar"},
            {"key": "2", "val": "SBU"}]}
        self.assertTrue(RuntimeUtilityHold._catalogue_verified(valid))
        self.assertFalse(RuntimeUtilityHold._catalogue_verified({**valid, "item": valid["item"] + [{"key": "3", "val": "Other"}]}))


if __name__ == "__main__":
    unittest.main()
