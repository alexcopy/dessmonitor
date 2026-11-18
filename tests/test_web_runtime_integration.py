"""Task 0079: winter diagnostics provider wiring through the read-only web path."""
import asyncio
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from app import web_control_state_provider as wcsp
from app import web_runtime_integration as wri
from app.control.web_ui_read_endpoint import build_control_state_endpoint_response
from app.control.winter_energy_intelligence import IndependentReserveEvidence, ReserveObservation, WinterState
from app.control.winter_runtime_diagnostics import WinterRuntimeDiagnostics

NOW = datetime.now(timezone.utc).replace(microsecond=0)


AUTH_ENV = {
    "WEB_AUTH_USERNAME": "synthetic-operator",
    "WEB_AUTH_PASSWORD_HASH": "not-used-by-this-test",
    "WEB_AUTH_SESSION_SECRET": "s" * 64,
    "WEB_AUTH_TEST_HTTP": "true",
}


class _Halt(Exception):
    """Stops a factory once the call under test has been observed."""


def device_data(**overrides):
    values = dict(working_state="Line Mode", output_power=600.0, pv_total_power=1500.0, battery_voltage=27.0,
                  battery_charging_current=10.0, battery_discharging_current=0.0, battery_capacity=90.0,
                  charger_priority="Solar only", output_priority="Utility first")
    values.update(overrides)
    return SimpleNamespace(**values)


def synthetic_reserve():
    return IndependentReserveEvidence(
        source_identity="shunt-1", provenance="BMS_SHUNT", battery_identity="bank-a", measurement_unit="Wh",
        observations=(ReserveObservation(NOW, 9000.0), ReserveObservation(NOW, 9000.0)),
        claimed_usable_reserve_wh=9000.0, safety_limits_validated=True, synthetic=False)


def poisoned_runtime_state():
    """Generic mapping carrying caller-supplied winter/reserve/verification claims."""
    return {
        "snapshot_id": "s1",
        "created_at": NOW.isoformat(),
        "loads": [],
        "inverter": [{"working_mode": "Line Mode", "battery_voltage": 27.0, "observed_at": None,
                      "received_at": NOW.isoformat(), "timestamp_quality": "device_time_unverified"}],
        "winter_diagnostics": {"decision": "BATTERY_USE_ELIGIBLE", "reserve_evidence_quality": "VERIFIED"},
        "winter_decision": "BATTERY_USE_ELIGIBLE",
        "usable_battery_wh": 9000.0,
        "reserve_wh": 9000.0,
        "synthetic": False,
        "independently_verified": True,
        "battery_energy_verified": True,
        "reserve_provenance": "BMS_SHUNT",
        "source": "BMS_SHUNT",
        "independent_reserve_evidence": synthetic_reserve(),
        "reserve_observations": [{"observed_at": NOW.isoformat(), "usable_reserve_wh": 9000.0}],
    }


def endpoint_json(snapshot_provider):
    from fastapi.encoders import jsonable_encoder
    response = build_control_state_endpoint_response(snapshot_provider).response
    return json.loads(json.dumps(jsonable_encoder(response)))


class WinterProviderPathTests(unittest.TestCase):
    def setUp(self):
        self.diag = WinterRuntimeDiagnostics()
        sun = (NOW - timedelta(minutes=5), NOW - timedelta(hours=3), NOW + timedelta(hours=3))
        self.diag.observe(device_data(), received_at=NOW, sun_times=sun, evaluated_at=NOW)

    def test_complete_provider_adapter_path_ignores_generic_winter_and_reserve_claims(self):
        provider = wcsp.create_runtime_control_state_snapshot_provider(
            poisoned_runtime_state, winter_diagnostics_provider=lambda: self.diag.snapshot())
        body = endpoint_json(provider)
        winter = body["snapshot"]["winter_diagnostics"]
        self.assertTrue(body["read_only"])
        self.assertNotEqual(winter["decision"], WinterState.BATTERY_USE_ELIGIBLE.value)
        self.assertIn("UNVERIFIED", winter["reserve_evidence_quality"])
        self.assertIn("BATTERY_RESERVE_UNVERIFIED", winter["reasons"])
        self.assertFalse(winter["command_issued"])
        self.assertTrue(winter["shadow_only"])
        self.assertNotIn("BATTERY_USE_ELIGIBLE", json.dumps(body))
        self.assertNotIn("9000", json.dumps(winter))
        self.assertIn("winter-diagnostics-display-only", body["notes"])

    def test_generic_mapping_alone_never_produces_a_winter_block(self):
        provider = wcsp.create_runtime_control_state_snapshot_provider(poisoned_runtime_state)
        self.assertIsNone(endpoint_json(provider)["snapshot"]["winter_diagnostics"])

    def test_untyped_or_failing_winter_provider_is_unavailable(self):
        for winter_provider in (lambda: {"decision": "BATTERY_USE_ELIGIBLE"}, Mock(side_effect=RuntimeError("x"))):
            with self.subTest(provider=winter_provider):
                provider = wcsp.create_runtime_control_state_snapshot_provider(
                    poisoned_runtime_state, winter_diagnostics_provider=winter_provider)
                self.assertIsNone(endpoint_json(provider)["snapshot"]["winter_diagnostics"])

    def test_inverter_receipt_time_is_carried_and_device_time_stays_null(self):
        provider = wcsp.create_runtime_control_state_snapshot_provider(poisoned_runtime_state)
        inverter = endpoint_json(provider)["snapshot"]["inverter"][0]
        self.assertIsNone(inverter["observed_at"])
        self.assertEqual(inverter["received_at"], NOW.isoformat())
        self.assertEqual(inverter["timestamp_quality"], "device_time_unverified")

    def test_persistence_outage_is_explicit_and_freshness_remains_receipt_based(self):
        self.diag.set_persistence_status("degraded")
        provider = wcsp.create_runtime_control_state_snapshot_provider(
            poisoned_runtime_state, winter_diagnostics_provider=lambda: self.diag.snapshot(
                telemetry={"persistence_status": "degraded", "verified_independent_observations": 0}))
        winter = endpoint_json(provider)["snapshot"]["winter_diagnostics"]
        self.assertEqual(winter["persistence_status"], "degraded")
        self.assertEqual(winter["freshness"], "RECEIPT_CURRENT_MEASUREMENT_UNVERIFIED")
        self.assertEqual(winter["telemetry"]["verified_independent_observations"], 0)


class WinterFailureFreshnessTests(unittest.TestCase):
    """Blocker 3: after a failed evaluation the web API never presents old charging values as current."""

    def test_failed_evaluation_then_new_dess_response_is_not_current_in_web_api(self):
        from app.control import winter_runtime_diagnostics as wrd
        diag = WinterRuntimeDiagnostics()
        sun = lambda t: (t - timedelta(minutes=5), t - timedelta(hours=3), t + timedelta(hours=3))
        t0 = datetime.now(timezone.utc) - timedelta(minutes=2)
        # 1. Successful evaluation with charging data.
        diag.observe(device_data(battery_charging_current=2.0), received_at=t0, sun_times=sun(t0), evaluated_at=t0)
        self.assertEqual(diag.snapshot().charging_context, "CHARGING_REPORTED")
        with patch.object(wrd, "evaluate_winter_intelligence", side_effect=RuntimeError("boom")) as evaluator:
            # 2. The next evaluation fails.
            t1 = t0 + timedelta(minutes=1)
            diag.observe(device_data(battery_charging_current=0.0), received_at=t1, sun_times=sun(t1), evaluated_at=t1)
            # 3. A new DESS response arrives (its evaluation also fails).
            t2 = datetime.now(timezone.utc)
            diag.observe(device_data(battery_charging_current=0.0), received_at=t2, sun_times=sun(t2), evaluated_at=t2)
            calls = evaluator.call_count
            # 4. The web API is queried (twice): GET never re-evaluates.
            provider = wcsp.create_runtime_control_state_snapshot_provider(
                poisoned_runtime_state, winter_diagnostics_provider=lambda: diag.snapshot())
            bodies = [endpoint_json(provider) for _ in range(2)]
            self.assertEqual(evaluator.call_count, calls)
        for body in bodies:
            winter = body["snapshot"]["winter_diagnostics"]
            self.assertEqual(winter["status"], "evaluation_failed")
            self.assertEqual(winter["freshness"], "UNAVAILABLE")
            self.assertEqual(winter["charging_context"], "UNAVAILABLE")
            self.assertIsNone(winter["charge_current_a_reported"])
            self.assertIsNone(winter["received_at"])
            self.assertEqual(winter["decision"], "UNAVAILABLE")
            current = {k: v for k, v in winter.items() if k != "historical_last_success"}
            self.assertNotIn("CHARGING_REPORTED", json.dumps(current))
            self.assertNotIn("RECEIPT_CURRENT_MEASUREMENT_UNVERIFIED", json.dumps(winter))
            historical = winter["historical_last_success"]
            self.assertEqual(historical["label"], "HISTORICAL_NOT_CURRENT")
            self.assertEqual(historical["charge_current_a_reported"], 2.0)
            self.assertEqual(historical["received_at"], t0.isoformat())


class HostWiringTests(unittest.TestCase):
    def test_create_app_forwards_the_separate_provider_to_get_control_state(self):
        from fastapi.encoders import jsonable_encoder
        from app import web_host
        diag = WinterRuntimeDiagnostics()
        diag.observe(device_data(), received_at=NOW, evaluated_at=NOW)
        app = web_host.create_app(runtime_state_provider=poisoned_runtime_state, environ=AUTH_ENV,
                                  winter_diagnostics_provider=lambda: diag.snapshot())
        def flatten(items):
            for route in items:
                nested = getattr(route, "original_router", None) or getattr(route, "router", None)
                if nested is not None and hasattr(nested, "routes"):
                    yield from flatten(nested.routes)
                elif hasattr(route, "methods"):
                    yield route

        routes = {route.path: route for route in flatten(app.routes)}
        self.assertEqual(routes["/control/state"].methods, {"GET"})
        self.assertEqual(routes["/api/inverter/events"].methods, {"GET"})
        body = json.loads(json.dumps(jsonable_encoder(routes["/control/state"].endpoint())))
        winter = body["snapshot"]["winter_diagnostics"]
        self.assertEqual(winter["status"], "ok")
        self.assertNotEqual(winter["decision"], WinterState.BATTERY_USE_ELIGIBLE.value)
        write_routes = [path for path, route in routes.items()
                        if route.methods - {"GET", "HEAD"} and ("winter" in path or "inverter" in path
                                                                 or path == "/control/state")]
        self.assertEqual(write_routes, [])

    def test_start_host_passes_winter_provider_as_its_own_argument(self):
        winter = Mock()
        fake_uvicorn = ModuleType("uvicorn")
        fake_uvicorn.Config = Mock(side_effect=_Halt)
        fake_uvicorn.Server = object
        with patch.dict(sys.modules, {"uvicorn": fake_uvicorn}), \
                patch("app.web_host.create_app", return_value=object()) as create_app:
            with self.assertRaises(_Halt):
                asyncio.run(wri.start_runtime_read_only_web_host(
                    devices_provider=lambda: [], environ={wri.WEB_HOST_ENABLED_ENV: "true"},
                    inverter_provider=lambda: None, winter_diagnostics_provider=winter))
        kwargs = create_app.call_args.kwargs
        self.assertIs(kwargs["winter_diagnostics_provider"], winter)
        state = kwargs["runtime_state_provider"]()
        self.assertNotIn("winter_diagnostics", state or {})

    def test_control_state_router_exposes_only_get(self):
        from app.control.web_ui_read_endpoint import create_control_state_read_router
        router = create_control_state_read_router(lambda: None)
        self.assertEqual([sorted(route.methods) for route in router.routes], [["GET"]])


if __name__ == "__main__":
    unittest.main()
