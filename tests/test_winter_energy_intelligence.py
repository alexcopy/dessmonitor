import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.control.adaptive_output_policy import OutputDecision
from app.control.grid_output_interlock import (
    ChargerProvenance, DurableInterlockState, InterlockAction, InterlockBaseline, InterlockDecision,
    InterlockReason, InterlockSnapshot, InterlockState, KnownStatus, StateSource, TransitionOrigin, TrialPhase,
)
from app.control.seasonal_output_strategy import ApprovedBands, EnergyInputs
from app.control.winter_energy_intelligence import (
    EvidenceConfidence, EvidenceFreshness, MeasurementValidity, RESEARCH_VOLTAGE_CANDIDATES_V,
    IndependentReserveEvidence, InterlockEvaluationInputs, ReserveObservation, TimestampValidity,
    WinterContext, WinterPolicyConfig, WinterProfile, WinterState,
    evaluate_winter_intelligence,
)
from test_adaptive_output_policy import BASE, SUNSET, baseline, established_line_history, sample
from app.control.grid_output_interlock import PvContext


class WinterEnergyIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.baseline = baseline(max_battery_charge_a=60.0)
        self.snapshot = sample(discharge=0.0, pv=800.0, output=500.0)
        self.history = established_line_history()
        self.energy = EnergyInputs(
            expected_pv_wh=6000.0,
            forecast_issued_at=BASE - timedelta(minutes=5),
            forecast_valid_through=SUNSET,
            forecast_confidence=0.9,
            usable_battery_wh=9000.0,
            battery_energy_observed_at=BASE,
            battery_energy_verified=True,
            reserve_wh=1000.0,
            minimum_confidence=0.75,
            max_energy_age_seconds=180.0,
            proposal_ttl_seconds=600.0,
        )
        self.bands = ApprovedBands(24.0, 25.0, 25.5, 27.0, 0.4, 0.5)
        self.context = WinterContext(
            month=11,
            daylight_remaining_hours=8.0,
            measured_pv_observed_at=BASE - timedelta(seconds=20),
            measured_pv_provenance="EASUN_REPORTED_PV",
            forecast_provenance="WEATHER_FORECAST",
            forecast_independently_verified=True,
            reserve_provenance="BMS_SHUNT",
            reserve_measurement_validity=MeasurementValidity.VALID,
            reserve_observations_validated=True,
            external_charging=KnownStatus.ABSENT,
            pv_context=PvContext(
                curtailment=KnownStatus.ABSENT,
                external_controller=KnownStatus.ABSENT,
                timezone_verified=True,
            ),
        )

    def interlock(self, state, reasons=(), charger_mode="solar_only"):
        action = InterlockAction.ADVISE if state is InterlockState.BATTERY_RETURN_ELIGIBLE else (
            InterlockAction.BLOCK if state in (InterlockState.GRID_RECOVERY_ACTIVE, InterlockState.FAULT_HOLD,
                                                InterlockState.CHURN_LOCKOUT) else InterlockAction.HOLD)
        return InterlockDecision(
            decision=action,
            state=state,
            reason_codes=tuple(reasons),
            evidence=(),
            evaluated_at=BASE,
            observed_working_mode="Line Mode",
            observed_charger_mode=charger_mode,
            charger_provenance=ChargerProvenance.DEVICE_READBACK,
            output_priority_raw=None,
            lockout_expires_at=None,
            trial_phase=TrialPhase.NONE,
            transition_origin=TransitionOrigin.UNKNOWN_EXTERNAL_OR_DEVICE,
            base_decision=OutputDecision.CONSIDER,
            base_reason_codes=(),
            updated_state=DurableInterlockState(source=StateSource.PERSISTED, recovery_in_progress=False),
        )

    _DEFAULT = object()

    def evaluate(self, *, snapshot=None, context=None, interlock=_DEFAULT, energy="default", interlock_inputs=None):
        return evaluate_winter_intelligence(
            snapshot or self.snapshot,
            self.baseline,
            self.history,
            evaluated_at=BASE,
            energy=self.energy if energy == "default" else energy,
            bands=self.bands,
            interlock=self.interlock(InterlockState.GRID_OUTPUT_HOLD) if interlock is self._DEFAULT else interlock,
            context=context or self.context,
            interlock_inputs=interlock_inputs,
        )

    @staticmethod
    def evidence(result, name):
        return next(item for item in result.evidence if item.name == name)

    def reserve_contract(self, *, observations=None, synthetic=True, safety_limits_validated=True,
                         claimed=9000.0, provenance="BMS_SHUNT"):
        observations = observations or (
            ReserveObservation(BASE - timedelta(seconds=20), claimed),
            ReserveObservation(BASE, claimed),
        )
        return IndependentReserveEvidence(
            source_identity="test-bms-shunt",
            provenance=provenance,
            battery_identity="test-bank",
            measurement_unit="Wh",
            observations=observations,
            claimed_usable_reserve_wh=claimed,
            safety_limits_validated=safety_limits_validated,
            synthetic=synthetic,
        )

    def test_weak_morning_after_recovery_holds_utility(self):
        result = self.evaluate(
            snapshot=replace(self.snapshot, pv_power_w=20.0),
            interlock=self.interlock(InterlockState.GRID_RECOVERY_COMPLETE),
        )
        self.assertIs(result.state, WinterState.RECOVERY_OR_FAULT_HOLD)
        self.assertIn("POLICY_PV_DEFICIT", result.seasonal_decision.reasons)

    def test_grid_charging_to_27v_is_not_solar_recovery(self):
        result = self.evaluate(
            snapshot=replace(self.snapshot, battery_voltage=27.0, battery_current_chg=2.0),
            context=replace(self.context, external_charging=KnownStatus.PRESENT),
            interlock=self.interlock(InterlockState.GRID_RECOVERY_ACTIVE,
                                     (InterlockReason.GRID_CHARGE_NOT_SOLAR,), charger_mode="solar_and_mains"),
        )
        self.assertIs(result.state, WinterState.RECOVERY_OR_FAULT_HOLD)
        self.assertIn("GRID_CHARGING_NOT_SOLAR", result.reasons)

    def test_strong_pv_without_independently_verified_reserve_stays_on_hold(self):
        result = self.evaluate(context=replace(self.context, reserve_provenance="EASUN_REPORTED_CHANNEL"))
        self.assertIs(result.state, WinterState.RECOVERY_OR_FAULT_HOLD)
        self.assertIn("BATTERY_RESERVE_UNVERIFIED", result.reasons)
        self.assertNotEqual(result.state, WinterState.BATTERY_USE_ELIGIBLE)

    def test_every_interlock_hold_or_block_remains_ineligible(self):
        cases = (
            (InterlockState.SOLAR_RETURN_CANDIDATE, (InterlockReason.POST_RECOVERY_RESERVE_UNPROVEN,)),
            (InterlockState.GRID_OUTPUT_HOLD, ()),
            (InterlockState.GRID_RECOVERY_COMPLETE, ()),
            (InterlockState.FAULT_HOLD, ()),
            (InterlockState.CHURN_LOCKOUT, ()),
        )
        for state, reasons in cases:
            with self.subTest(state=state):
                result = self.evaluate(interlock=self.interlock(state, reasons))
                self.assertNotEqual(result.state, WinterState.BATTERY_USE_ELIGIBLE)

    def test_missing_interlock_remains_ineligible(self):
        result = self.evaluate(interlock=None)
        self.assertIs(result.state, WinterState.RECOVERY_OR_FAULT_HOLD)
        self.assertIn("INTERLOCK_DECISION_MISSING", result.reasons)

    def test_three_day_old_forecast_is_stale_and_not_verified_current(self):
        result = self.evaluate(energy=replace(self.energy, forecast_issued_at=BASE - timedelta(days=3)))
        item = self.evidence(result, "forecast_pv_wh")
        self.assertIs(item.freshness, EvidenceFreshness.STALE)
        self.assertIsNot(item.confidence, EvidenceConfidence.VERIFIED)
        self.assertNotEqual(result.state, WinterState.BATTERY_USE_ELIGIBLE)

    def test_fresh_forecast_without_independent_verification_is_not_verified(self):
        result = self.evaluate(context=replace(self.context, forecast_independently_verified=False))
        item = self.evidence(result, "forecast_pv_wh")
        self.assertIs(item.freshness, EvidenceFreshness.CURRENT)
        self.assertIs(item.confidence, EvidenceConfidence.UNKNOWN)
        self.assertFalse(item.independently_verified)
        self.assertNotEqual(result.state, WinterState.BATTERY_USE_ELIGIBLE)

    def test_stale_and_future_reserve_are_not_current(self):
        for label, observed_at, freshness in (
            ("stale", BASE - timedelta(days=3), EvidenceFreshness.STALE),
            ("future", BASE + timedelta(hours=1), EvidenceFreshness.FUTURE),
        ):
            with self.subTest(label=label):
                result = self.evaluate(energy=replace(self.energy, battery_energy_observed_at=observed_at))
                item = self.evidence(result, "usable_battery_wh")
                self.assertIs(item.freshness, freshness)
                self.assertIsNot(item.confidence, EvidenceConfidence.VERIFIED)
                self.assertNotEqual(result.state, WinterState.BATTERY_USE_ELIGIBLE)

    def test_stale_interlock_advice_is_not_current_authority(self):
        for label, evaluated_at in (("stale", BASE - timedelta(days=3)),
                                    ("future", BASE + timedelta(hours=1))):
            with self.subTest(label=label):
                decision = replace(self.interlock(InterlockState.BATTERY_RETURN_ELIGIBLE),
                                   evaluated_at=evaluated_at)
                result = self.evaluate(interlock=decision)
                self.assertNotEqual(result.state, WinterState.BATTERY_USE_ELIGIBLE)
                self.assertIn("INTERLOCK_BINDING_UNVERIFIED", result.reasons)
                self.assertIn("interlock_reported_decision", {item.name for item in result.evidence})

    def test_fresh_advice_for_different_snapshot_is_not_authority(self):
        other = replace(self.snapshot, pv_power_w=801.0)
        inputs = InterlockEvaluationInputs(
            snapshot=InterlockSnapshot(telemetry=other),
            baseline=InterlockBaseline(output=self.baseline),
            recent_history=self.history,
            durable_state=DurableInterlockState(source=StateSource.PERSISTED, recovery_in_progress=False),
        )
        result = self.evaluate(interlock=self.interlock(InterlockState.BATTERY_RETURN_ELIGIBLE),
                               interlock_inputs=inputs)
        self.assertNotEqual(result.state, WinterState.BATTERY_USE_ELIGIBLE)
        self.assertIn("INTERLOCK_SNAPSHOT_MISMATCH", result.reasons)

    def test_same_timestamp_different_telemetry_cannot_authorize(self):
        other = replace(self.snapshot, battery_voltage=26.1)
        inputs = InterlockEvaluationInputs(
            snapshot=InterlockSnapshot(telemetry=other),
            baseline=InterlockBaseline(output=self.baseline),
            recent_history=self.history,
            durable_state=DurableInterlockState(source=StateSource.PERSISTED, recovery_in_progress=False),
        )
        result = self.evaluate(interlock=self.interlock(InterlockState.BATTERY_RETURN_ELIGIBLE),
                               interlock_inputs=inputs)
        self.assertIn("INTERLOCK_SNAPSHOT_MISMATCH", result.reasons)
        self.assertNotEqual(result.state, WinterState.BATTERY_USE_ELIGIBLE)

    def test_exact_snapshot_recomputes_task_0076_in_same_cycle(self):
        inputs = InterlockEvaluationInputs(
            snapshot=InterlockSnapshot(telemetry=self.snapshot),
            baseline=InterlockBaseline(output=self.baseline),
            recent_history=self.history,
            durable_state=DurableInterlockState(source=StateSource.PERSISTED, recovery_in_progress=False),
        )
        result = self.evaluate(interlock=None, interlock_inputs=inputs)
        self.assertNotIn("INTERLOCK_BINDING_UNVERIFIED", result.reasons)
        self.assertNotIn("INTERLOCK_DECISION_MISSING", result.reasons)
        self.assertIsNotNone(result.interlock_state)
        self.assertNotEqual(result.state, WinterState.BATTERY_USE_ELIGIBLE)

    def test_missing_interlock_evaluation_timestamp_fails_closed(self):
        missing_time = replace(self.interlock(InterlockState.BATTERY_RETURN_ELIGIBLE), evaluated_at=None)
        result = self.evaluate(interlock=missing_time)
        item = self.evidence(result, "interlock_reported_decision")
        self.assertIs(item.timestamp_validity, TimestampValidity.MISSING)
        self.assertIn("INTERLOCK_BINDING_UNVERIFIED", result.reasons)
        self.assertNotEqual(result.state, WinterState.BATTERY_USE_ELIGIBLE)

    def test_legacy_reserve_flags_label_and_scalar_are_not_a_contract(self):
        result = self.evaluate()
        item = self.evidence(result, "usable_battery_wh")
        self.assertIs(item.measurement_validity, MeasurementValidity.UNVERIFIED)
        self.assertFalse(item.independently_verified)
        self.assertIn("RESERVE_VALIDATION_CONTEXT_MISSING", result.reasons)

    def test_invalid_current_telemetry_remains_ineligible(self):
        result = self.evaluate(snapshot=replace(self.snapshot, battery_voltage=0.0),
                               interlock=self.interlock(InterlockState.BATTERY_RETURN_ELIGIBLE))
        self.assertNotEqual(result.state, WinterState.BATTERY_USE_ELIGIBLE)
        self.assertIn("BATTERY_RESERVE_UNVERIFIED", result.reasons)

    def test_underlying_synthetic_reserve_is_explicitly_unverified(self):
        context = replace(self.context, independent_reserve_evidence=self.reserve_contract())
        result = self.evaluate(context=context)
        item = self.evidence(result, "usable_battery_wh")
        self.assertIs(item.freshness, EvidenceFreshness.CURRENT)
        self.assertIn("RESERVE_SYNTHETIC_EVIDENCE", result.reasons)
        self.assertFalse(item.independently_verified)
        self.assertNotEqual(result.state, WinterState.BATTERY_USE_ELIGIBLE)

    def test_valid_looking_non_synthetic_fixture_is_still_unverified(self):
        context = replace(self.context, independent_reserve_evidence=self.reserve_contract(synthetic=False))
        result = self.evaluate(
            context=context,
            interlock=self.interlock(InterlockState.BATTERY_RETURN_ELIGIBLE),
        )
        item = self.evidence(result, "usable_battery_wh")
        self.assertIs(item.measurement_validity, MeasurementValidity.VALID)
        self.assertIs(item.freshness, EvidenceFreshness.CURRENT)
        self.assertIs(item.confidence, EvidenceConfidence.OBSERVED)
        self.assertFalse(item.independently_verified)
        self.assertIn("RESERVE_TRUSTED_INTEGRATION_UNAVAILABLE", result.reasons)
        self.assertIn("BATTERY_RESERVE_UNVERIFIED", result.reasons)
        self.assertNotEqual(result.state, WinterState.BATTERY_USE_ELIGIBLE)

    def test_stale_future_and_contradictory_underlying_reserve_fail_closed(self):
        cases = (
            ("stale", (ReserveObservation(BASE - timedelta(days=3), 9000.0),), "RESERVE_EVIDENCE_STALE"),
            ("future", (ReserveObservation(BASE + timedelta(hours=1), 9000.0),), "RESERVE_TIMESTAMP_FUTURE"),
            ("contradictory", (ReserveObservation(BASE, 9000.0), ReserveObservation(BASE, 8000.0)),
             "RESERVE_OBSERVATIONS_CONTRADICTORY"),
        )
        for label, observations, reason in cases:
            with self.subTest(label=label):
                context = replace(self.context, independent_reserve_evidence=self.reserve_contract(
                    observations=observations))
                result = self.evaluate(context=context)
                self.assertIn(reason, result.reasons)
                self.assertNotEqual(result.state, WinterState.BATTERY_USE_ELIGIBLE)

    def test_caller_complete_without_observations_is_unverified(self):
        result = self.evaluate(context=replace(self.context, reported_interval_status="COMPLETE"))
        item = self.evidence(result, "reported_interval_status")
        self.assertIs(item.measurement_validity, MeasurementValidity.UNVERIFIED)
        self.assertIsNot(item.confidence, EvidenceConfidence.VERIFIED)

    def test_validated_interval_is_observed_proxy_not_independent_reserve(self):
        rows = (
            {"time": BASE - timedelta(minutes=2), "battery_voltage": 26.0,
             "battery_current_chg": 0.0, "battery_current_dis": 0.0},
            {"time": BASE, "battery_voltage": 26.1,
             "battery_current_chg": 0.0, "battery_current_dis": 0.0},
        )
        result = self.evaluate(context=replace(self.context, reported_interval_status="COMPLETE",
                                                reported_interval_observations=rows))
        item = self.evidence(result, "reported_interval_status")
        self.assertIs(item.measurement_validity, MeasurementValidity.VALID)
        self.assertIs(item.freshness, EvidenceFreshness.CURRENT)
        self.assertIs(item.confidence, EvidenceConfidence.OBSERVED)

    def test_missing_and_naive_timestamps_fail_closed_but_keep_diagnostics(self):
        missing = self.evaluate(context=replace(self.context, measured_pv_observed_at=None))
        missing_item = self.evidence(missing, "measured_pv_w")
        self.assertIs(missing_item.freshness, EvidenceFreshness.UNKNOWN)
        self.assertIs(missing_item.timestamp_validity, TimestampValidity.MISSING)
        self.assertIn("measured_pv_w", {item.name for item in missing.evidence})
        naive = self.evaluate(energy=replace(self.energy, battery_energy_observed_at=datetime(2026, 11, 2, 10)))
        reserve_item = self.evidence(naive, "usable_battery_wh")
        self.assertIs(reserve_item.freshness, EvidenceFreshness.UNKNOWN)
        self.assertIs(reserve_item.timestamp_validity, TimestampValidity.INVALID)
        self.assertNotEqual(naive.state, WinterState.BATTERY_USE_ELIGIBLE)

    def test_stable_solar_charging_is_observation_only(self):
        result = self.evaluate(snapshot=replace(self.snapshot, battery_current_chg=2.0))
        self.assertIs(result.state, WinterState.RECOVERY_OR_FAULT_HOLD)
        self.assertIn("SOLAR_CHARGING_OBSERVED_FROM_REPORTED_CHANNELS", result.reasons)

    def test_early_morning_voltage_candidate_does_not_authorise_return(self):
        result = self.evaluate(
            snapshot=replace(self.snapshot, battery_voltage=26.5),
            context=replace(self.context, daylight_remaining_hours=7.5),
            interlock=self.interlock(InterlockState.SOLAR_RETURN_CANDIDATE),
        )
        self.assertIs(result.state, WinterState.RECOVERY_OR_FAULT_HOLD)
        self.assertEqual(result.research_voltage_candidates_v, RESEARCH_VOLTAGE_CANDIDATES_V)
        self.assertNotEqual(result.state, WinterState.BATTERY_USE_ELIGIBLE)

    def test_evening_peak_demand_conserves_reserve(self):
        result = self.evaluate(context=replace(self.context, daylight_remaining_hours=2.0,
                                                evening_demand_wh=2000.0, evening_reserve_wh=500.0))
        self.assertIs(result.state, WinterState.EVENING_RESERVE_CONSERVATION)
        self.assertIn("EVENING_RESERVE_DEFICIT", result.reasons)

    def test_unknown_external_charging_and_missing_context_fail_closed(self):
        result = self.evaluate(context=replace(self.context, external_charging=KnownStatus.UNKNOWN,
                                                pv_context=PvContext()))
        self.assertIs(result.state, WinterState.RECOVERY_OR_FAULT_HOLD)
        self.assertIn("EXTERNAL_CHARGING_UNKNOWN", result.reasons)
        self.assertIn("PV_SOURCE_CONTEXT_UNVERIFIED", result.reasons)

    def test_post_recovery_deadlock_is_reported_without_a_trial(self):
        result = self.evaluate(
            context=replace(self.context, reserve_provenance=None),
            interlock=self.interlock(InterlockState.SOLAR_RETURN_CANDIDATE,
                                     (InterlockReason.POST_RECOVERY_RESERVE_UNPROVEN,)),
        )
        self.assertIs(result.state, WinterState.RECOVERY_OR_FAULT_HOLD)
        self.assertIn("POST_RECOVERY_EVIDENCE_DEADLOCK", result.reasons)
        self.assertIn("SUPERVISED_TRIAL_REQUIRES_SEPARATE_APPROVAL", result.reasons)

    def test_independent_bms_shunt_reserve_cannot_override_interlock_hold(self):
        result = self.evaluate(
            interlock=self.interlock(InterlockState.SOLAR_RETURN_CANDIDATE,
                                     (InterlockReason.POST_RECOVERY_RESERVE_UNPROVEN,)),
        )
        self.assertIs(result.state, WinterState.RECOVERY_OR_FAULT_HOLD)
        self.assertIn("RESERVE_VALIDATION_CONTEXT_MISSING", result.reasons)
        self.assertIn("POST_RECOVERY_EVIDENCE_DEADLOCK", result.reasons)
        self.assertNotEqual(result.state, WinterState.BATTERY_USE_ELIGIBLE)
        self.assertTrue(result.shadow_only)
        self.assertFalse(result.command_issued)

    def test_sunny_winter_day_with_existing_eligibility_is_advisory_only(self):
        result = self.evaluate(interlock=self.interlock(InterlockState.BATTERY_RETURN_ELIGIBLE))
        self.assertIs(result.state, WinterState.RECOVERY_OR_FAULT_HOLD)
        self.assertIn("INTERLOCK_BINDING_UNVERIFIED", result.reasons)
        self.assertEqual({item.name for item in result.evidence} & {
            "output_demand_w", "battery_voltage_v", "reported_interval_status"
        }, {"output_demand_w", "battery_voltage_v", "reported_interval_status"})
        self.assertTrue(result.shadow_only)
        self.assertFalse(result.command_issued)

    def test_shoulder_profile_requires_daylight_activation(self):
        result = self.evaluate(context=replace(self.context, month=10, daylight_remaining_hours=9.0))
        self.assertIs(result.profile, WinterProfile.OUT_OF_PROFILE)
        self.assertIs(result.state, WinterState.RECOVERY_OR_FAULT_HOLD)
        self.assertIn("WINTER_PROFILE_INACTIVE", result.reasons)


if __name__ == "__main__":
    unittest.main()
