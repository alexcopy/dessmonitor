"""Task 0079: read-only runtime adapter for the committed Task 0078 evaluator."""
import ast
import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.control import winter_runtime_diagnostics as wrd
from app.control.adaptive_output_policy import OutputDecision, OutputPolicyBaseline
from app.control.grid_output_interlock import (
    ChargerProvenance, DurableInterlockState, InterlockAction, InterlockDecision, InterlockReason,
    InterlockState, StateSource, TransitionOrigin, TrialPhase,
)
from app.control.winter_energy_intelligence import (
    IndependentReserveEvidence, ReserveObservation, WinterState, evaluate_winter_intelligence,
)
from app.control.winter_runtime_diagnostics import WinterRuntimeDiagnostics

NOW = datetime(2026, 12, 10, 12, 0, tzinfo=timezone.utc)


def data(**overrides):
    values = dict(working_state="Line Mode", output_power=900.0, pv_total_power=1200.0, battery_voltage=26.8,
                  battery_charging_current=4.0, battery_discharging_current=0.0, battery_capacity=80.0,
                  charger_priority="Solar only", output_priority="Utility first", timestamp="2026-12-10 20:00:00",
                  device_observed_at=NOW.isoformat())
    values.update(overrides)
    return SimpleNamespace(**values)


def sun(now=NOW):
    return (now - timedelta(minutes=10), now - timedelta(hours=4), now + timedelta(hours=4))


def eligible_interlock():
    return InterlockDecision(
        decision=InterlockAction.ADVISE, state=InterlockState.BATTERY_RETURN_ELIGIBLE, reason_codes=(),
        evidence=(), evaluated_at=NOW, observed_working_mode="Line Mode", observed_charger_mode="solar_only",
        charger_provenance=ChargerProvenance.DEVICE_READBACK, output_priority_raw=None, lockout_expires_at=None,
        trial_phase=TrialPhase.NONE, transition_origin=TransitionOrigin.UNKNOWN_EXTERNAL_OR_DEVICE,
        base_decision=OutputDecision.CONSIDER, base_reason_codes=(),
        updated_state=DurableInterlockState(source=StateSource.PERSISTED, recovery_in_progress=False),
    )


class WinterRuntimeDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.diag = WinterRuntimeDiagnostics()

    def test_initial_state_is_unavailable_and_fail_closed(self):
        snap = self.diag.snapshot(NOW)
        self.assertEqual(snap.status, "no_observation")
        self.assertEqual(snap.decision, "UNAVAILABLE")
        self.assertEqual(snap.freshness, "UNAVAILABLE")
        self.assertIn(wrd.RUNTIME_RESERVE_REASON, snap.reasons)

    def test_observation_is_read_only_hold_with_specific_reasons(self):
        snap = self.diag.observe(data(), received_at=NOW, sun_times=sun(), evaluated_at=NOW,
                                 fingerprint="abc123")
        self.assertEqual(snap.status, "ok")
        self.assertNotEqual(snap.decision, WinterState.BATTERY_USE_ELIGIBLE.value)
        self.assertEqual(snap.decision, WinterState.RECOVERY_OR_FAULT_HOLD.value)
        self.assertIn("BATTERY_RESERVE_UNVERIFIED", snap.reasons)
        self.assertIn(wrd.RUNTIME_RESERVE_REASON, snap.reasons)
        self.assertIn("independently verified BMS/shunt usable reserve", snap.missing_requirements)
        self.assertTrue(snap.reserve_evidence_quality.endswith("_UNVERIFIED"))
        self.assertIn(snap.interlock_decision, ("HOLD", "BLOCK"))
        self.assertEqual(snap.charging_context, "CHARGING_REPORTED")
        self.assertEqual(snap.charge_current_a_reported, 4.0)
        self.assertTrue(snap.shadow_only)
        self.assertFalse(snap.command_issued)
        self.assertFalse(snap.hardware_write_path)

    def test_post_charge_coordination_uses_exact_receipt_and_unverified_runtime_policy(self):
        snap = self.diag.observe(data(), received_at=NOW, sun_times=sun(), evaluated_at=NOW,
                                 fingerprint="abc123")
        coordinated = self.diag.observe_coordination(
            data(), received_at=NOW, evaluated_at=NOW, fingerprint="abc123",
            policy_status={"state": "idle", "desired_charger_mode": "solar_only",
                           "emergency_active": False},
        )
        self.assertEqual(coordinated.received_at, snap.received_at)
        self.assertEqual(coordinated.coordination["state"], "FAULT_HOLD")
        self.assertEqual(coordinated.coordination["charge_policy_provenance"], "RUNTIME_STATUS")
        self.assertFalse(coordinated.coordination["charge_policy_verified"])
        self.assertEqual(coordinated.coordination["charger_provenance"], "UNKNOWN")
        self.assertEqual(coordinated.coordination["control_state"], "FAULT_HOLD")
        self.assertFalse(coordinated.coordination["utility_hold_requested"])
        self.assertTrue(coordinated.coordination["return_blocked"])
        self.assertFalse(coordinated.coordination["command_issued"])
        self.assertFalse(coordinated.coordination["hardware_write_attempted"])

    def test_coordination_identity_mismatch_fails_closed(self):
        self.diag.observe(data(), received_at=NOW, evaluated_at=NOW, fingerprint="original")
        snap = self.diag.observe_coordination(data(), received_at=NOW + timedelta(seconds=1),
                                              evaluated_at=NOW + timedelta(seconds=1), fingerprint="x")
        self.assertEqual(snap.coordination["state"], "FAULT_HOLD")
        self.assertIn("COORDINATION_OBSERVATION_IDENTITY_UNAVAILABLE", snap.coordination["reasons"])

    def test_same_receipt_different_post_charge_payload_fails_closed(self):
        self.diag.observe(data(), received_at=NOW, evaluated_at=NOW, fingerprint="original")
        changed = data(working_state="Invert Mode", pv_total_power=10.0)
        snap = self.diag.observe_coordination(changed, received_at=NOW, evaluated_at=NOW,
                                              fingerprint="different")
        self.assertEqual(snap.coordination["state"], "FAULT_HOLD")
        self.assertIn("COORDINATION_OBSERVATION_IDENTITY_UNAVAILABLE", snap.coordination["reasons"])

    def test_reused_fingerprint_cannot_mix_payload_or_append_history(self):
        self.diag.observe(data(), received_at=NOW, evaluated_at=NOW, fingerprint="original")
        before = len(self.diag._coordination_history)
        changed = data(output_power=123.0, charger_priority="Solar and mains")
        snap = self.diag.observe_coordination(changed, received_at=NOW, evaluated_at=NOW,
                                              fingerprint="original")
        self.assertEqual(snap.coordination["state"], "FAULT_HOLD")
        self.assertIn("COORDINATION_OBSERVATION_IDENTITY_UNAVAILABLE", snap.coordination["reasons"])
        self.assertEqual(len(self.diag._coordination_history), before)

    def test_device_time_is_never_trusted_and_freshness_is_receipt_based(self):
        inputs = self.diag.build_inputs(data(), received_at=NOW, sun_times=sun(), evaluated_at=NOW)
        self.assertIsNone(inputs.snapshot.observed_at)
        self.assertEqual(inputs.snapshot.received_at, NOW)
        self.diag.observe(data(), received_at=NOW, sun_times=sun(), evaluated_at=NOW)
        current = self.diag.snapshot(NOW + timedelta(seconds=60))
        self.assertEqual(current.freshness, "RECEIPT_CURRENT_MEASUREMENT_UNVERIFIED")
        self.assertIsNone(current.measurement_time)
        self.assertEqual(current.timestamp_quality, "device_time_unverified")
        stale = self.diag.snapshot(NOW + timedelta(minutes=10))  # DESS outage: no new observe()
        self.assertEqual(stale.freshness, "RECEIPT_STALE")
        self.assertEqual(stale.receipt_age_seconds, 600.0)

    def test_unknown_or_stale_sun_context_is_timezone_unverified(self):
        for value in (None, sun(NOW - timedelta(hours=5)), tuple(x.replace(tzinfo=None) for x in sun())):
            with self.subTest(value=value):
                snap = self.diag.observe(data(), received_at=NOW, sun_times=value, evaluated_at=NOW)
                self.assertIn("TIMEZONE_UNVERIFIED", snap.reasons)
                self.assertIn("DAYLIGHT_UNVERIFIED", snap.reasons)

    def test_snapshot_identity_binding_and_mismatch(self):
        inputs = self.diag.build_inputs(data(), received_at=NOW, sun_times=sun(), evaluated_at=NOW)
        self.assertIs(inputs.interlock_inputs.snapshot.telemetry, inputs.snapshot)
        bound = evaluate_winter_intelligence(
            inputs.snapshot, self.diag.baseline, inputs.history, evaluated_at=NOW, energy=None, bands=None,
            interlock=None, context=inputs.context, interlock_inputs=inputs.interlock_inputs)
        self.assertNotIn("INTERLOCK_SNAPSHOT_MISMATCH", bound.reasons)
        self.assertNotIn("INTERLOCK_BINDING_UNVERIFIED", bound.reasons)
        different = replace(inputs.snapshot, pv_power_w=1201.0)
        mismatch = evaluate_winter_intelligence(
            different, self.diag.baseline, inputs.history, evaluated_at=NOW, energy=None, bands=None,
            interlock=eligible_interlock(), context=inputs.context, interlock_inputs=inputs.interlock_inputs)
        self.assertIn("INTERLOCK_SNAPSHOT_MISMATCH", mismatch.reasons)
        self.assertIsNot(mismatch.state, WinterState.BATTERY_USE_ELIGIBLE)

    def test_caller_reserve_claims_on_the_observation_are_ignored(self):
        evidence = IndependentReserveEvidence(
            source_identity="shunt-1", provenance="BMS_SHUNT", battery_identity="bank-a", measurement_unit="Wh",
            observations=(ReserveObservation(NOW, 9000.0), ReserveObservation(NOW, 9000.0)),
            claimed_usable_reserve_wh=9000.0, safety_limits_validated=True, synthetic=False)
        snap = self.diag.observe(
            data(usable_battery_wh=9000.0, independent_reserve_evidence=evidence, synthetic=False,
                 independently_verified=True, reserve_provenance="BMS_SHUNT"),
            received_at=NOW, sun_times=sun(), evaluated_at=NOW)
        inputs = self.diag.build_inputs(data(), received_at=NOW, sun_times=sun(), evaluated_at=NOW)
        self.assertIsNone(inputs.context.independent_reserve_evidence)
        self.assertNotEqual(snap.decision, WinterState.BATTERY_USE_ELIGIBLE.value)
        self.assertIn("BATTERY_RESERVE_UNVERIFIED", snap.reasons)
        self.assertNotIn("VERIFIED", snap.reserve_evidence_quality.replace("UNVERIFIED", ""))

    def test_eligible_evaluator_output_is_still_held(self):
        real = evaluate_winter_intelligence

        def forced(*args, **kwargs):
            return replace(real(*args, **kwargs), state=WinterState.BATTERY_USE_ELIGIBLE)

        with patch.object(wrd, "evaluate_winter_intelligence", side_effect=forced):
            snap = self.diag.observe(data(), received_at=NOW, sun_times=sun(), evaluated_at=NOW)
        self.assertEqual(snap.decision, WinterState.RECOVERY_OR_FAULT_HOLD.value)

    def test_post_recovery_deadlock_is_reported(self):
        real = evaluate_winter_intelligence

        def deadlocked(snapshot, baseline, history, **kwargs):
            reported = replace(eligible_interlock(), decision=InterlockAction.HOLD,
                               state=InterlockState.SOLAR_RETURN_CANDIDATE,
                               reason_codes=(InterlockReason.POST_RECOVERY_RESERVE_UNPROVEN,))
            kwargs.update(interlock=reported, interlock_inputs=None)
            return real(snapshot, baseline, history, **kwargs)

        with patch.object(wrd, "evaluate_winter_intelligence", side_effect=deadlocked):
            snap = self.diag.observe(data(), received_at=NOW, sun_times=sun(), evaluated_at=NOW)
        self.assertTrue(snap.post_recovery_deadlock)
        self.assertIn("POST_RECOVERY_EVIDENCE_DEADLOCK", snap.reasons)
        self.assertIn("SUPERVISED_TRIAL_REQUIRES_SEPARATE_APPROVAL", snap.reasons)
        self.assertEqual(snap.decision, WinterState.RECOVERY_OR_FAULT_HOLD.value)

    def test_evaluation_failure_is_unavailable_not_stale_current(self):
        self.diag.observe(data(), received_at=NOW, sun_times=sun(), evaluated_at=NOW)
        with patch.object(wrd, "evaluate_winter_intelligence", side_effect=RuntimeError("boom")):
            snap = self.diag.observe(data(), received_at=NOW + timedelta(minutes=1), evaluated_at=NOW + timedelta(minutes=1))
        self.assertEqual(snap.status, "evaluation_failed")
        self.assertEqual(snap.decision, "UNAVAILABLE")
        self.assertEqual(snap.diagnostic_failure, "RuntimeError")
        self.assertEqual(snap.failure_count, 1)
        self.assertEqual(snap.last_successful_evaluation, NOW.isoformat())
        self.assertNotIn("boom", json.dumps(snap.as_dict()))

    def test_failure_after_charging_success_clears_every_current_field(self):
        # Reproduction from the precommit review: 2 A CHARGING_REPORTED, then a
        # failed evaluation of a 0 A response.
        ok = self.diag.observe(data(battery_charging_current=2.0), received_at=NOW, sun_times=sun(),
                               evaluated_at=NOW)
        self.assertEqual((ok.charging_context, ok.charge_current_a_reported), ("CHARGING_REPORTED", 2.0))
        later = NOW + timedelta(minutes=1)
        with patch.object(wrd, "evaluate_winter_intelligence", side_effect=RuntimeError("boom")):
            self.diag.observe(data(battery_charging_current=0.0), received_at=later, sun_times=sun(later),
                              evaluated_at=later)
        snap = self.diag.snapshot(later + timedelta(seconds=5))
        self.assertEqual(snap.status, "evaluation_failed")
        for name in ("decision", "profile", "solar_opportunity", "charging_context",
                     "interlock_decision", "interlock_state", "freshness"):
            self.assertEqual(getattr(snap, name), "UNAVAILABLE", name)
        self.assertIsNone(snap.charge_current_a_reported)
        self.assertIsNone(snap.charger_priority_reported)
        self.assertIsNone(snap.received_at)
        self.assertIsNone(snap.receipt_age_seconds)
        self.assertEqual(snap.interlock_reasons, ())
        self.assertFalse(snap.post_recovery_deadlock)
        self.assertIn(wrd.EVALUATION_FAILED_REASON, snap.reasons)
        current = {k: v for k, v in snap.as_dict().items() if k != "historical_last_success"}
        self.assertNotIn("CHARGING_REPORTED", json.dumps(current))
        self.assertNotIn("RECEIPT_CURRENT", json.dumps(snap.as_dict()))
        historical = snap.historical_last_success
        self.assertEqual(historical["label"], "HISTORICAL_NOT_CURRENT")
        self.assertEqual((historical["charging_context"], historical["charge_current_a_reported"]),
                         ("CHARGING_REPORTED", 2.0))
        self.assertEqual(historical["received_at"], NOW.isoformat())  # original timestamps
        self.assertEqual(historical["last_successful_evaluation"], NOW.isoformat())

    def test_next_successful_evaluation_replaces_failure_state(self):
        self.diag.observe(data(battery_charging_current=2.0), received_at=NOW, sun_times=sun(), evaluated_at=NOW)
        later = NOW + timedelta(minutes=1)
        with patch.object(wrd, "evaluate_winter_intelligence", side_effect=RuntimeError("boom")):
            self.diag.observe(data(), received_at=later, evaluated_at=later)
        latest = NOW + timedelta(minutes=2)
        snap = self.diag.observe(data(battery_charging_current=0.0), received_at=latest, sun_times=sun(latest),
                                 evaluated_at=latest)
        snap = self.diag.snapshot(latest)
        self.assertEqual((snap.status, snap.charging_context), ("ok", "NOT_CHARGING_REPORTED"))
        self.assertEqual(snap.freshness, "RECEIPT_CURRENT_MEASUREMENT_UNVERIFIED")
        self.assertEqual(snap.received_at, latest.isoformat())
        self.assertIsNone(snap.historical_last_success)
        self.assertEqual(snap.failure_count, 1)

    def test_failure_before_any_success_has_no_historical_block(self):
        with patch.object(wrd, "evaluate_winter_intelligence", side_effect=RuntimeError("boom")):
            snap = self.diag.observe(data(), received_at=NOW, evaluated_at=NOW)
        self.assertEqual((snap.status, snap.freshness), ("evaluation_failed", "UNAVAILABLE"))
        self.assertIsNone(snap.historical_last_success)
        self.assertIsNone(snap.last_successful_evaluation)

    def test_persistence_status_is_separate_from_receipt_freshness(self):
        self.diag.observe(data(), received_at=NOW, sun_times=sun(), evaluated_at=NOW)
        self.diag.set_persistence_status("degraded")
        snap = self.diag.snapshot(NOW + timedelta(seconds=30))
        self.assertEqual(snap.persistence_status, "degraded")
        self.assertEqual(snap.freshness, "RECEIPT_CURRENT_MEASUREMENT_UNVERIFIED")
        self.diag.set_persistence_status("fresh-and-verified")
        self.assertEqual(self.diag.snapshot(NOW).persistence_status, "degraded")

    def test_serialization_is_json_safe(self):
        self.diag.observe(data(), received_at=NOW, sun_times=sun(), evaluated_at=NOW)
        payload = self.diag.snapshot(NOW, telemetry={"responses": 3}).as_dict()
        decoded = json.loads(json.dumps(payload, allow_nan=False))
        self.assertEqual(decoded["telemetry"], {"responses": 3})
        self.assertIsInstance(decoded["reasons"], list)

    def test_non_policy_baseline_falls_back_to_default(self):
        self.assertEqual(WinterRuntimeDiagnostics(baseline=object()).baseline, OutputPolicyBaseline())

    def test_module_has_no_hardware_or_network_imports(self):
        tree = ast.parse(Path(wrd.__file__).read_text())
        modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        modules |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        forbidden = ("tuya", "requests", "asyncpg", "app.api", "app.service", "app.devices", "service.")
        self.assertFalse([m for m in modules if m and any(m.startswith(f) or f in m for f in forbidden)])


if __name__ == "__main__":
    unittest.main()
