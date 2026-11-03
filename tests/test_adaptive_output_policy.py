import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone, tzinfo
from unittest.mock import patch

from app.control.adaptive_output_policy import (
    BatteryEligibility,
    BatteryEligibilityAssessment,
    Evidence,
    ForecastObservation,
    ForecastQuality,
    OutputDecision,
    OutputPolicyBaseline,
    OutputPolicyDecision,
    OutputPolicyReason,
    OutputTelemetry,
    SolarOpportunity,
    SolarOpportunityAssessment,
    TelemetryQuality,
    evaluate_output_policy,
)


UTC = timezone.utc
BASE = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
SUNRISE = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
SUNSET = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)


def forecast(at: datetime = BASE, *, empty: bool = False, observed_at: datetime | None = None, valid_at: datetime | None = None) -> ForecastObservation:
    return ForecastObservation(
        observed_at=observed_at if observed_at is not None else at - timedelta(minutes=5),
        valid_at=valid_at if valid_at is not None else at + timedelta(hours=1),
        clouds_pct=None if empty else 20.0,
        precipitation_probability_pct=None if empty else 5.0,
    )


def sample(
    at: datetime = BASE,
    *,
    mode: str | None = "Line Mode",
    output: float | None = 500.0,
    pv: float | None = 800.0,
    voltage: float | None = 26.0,
    discharge: float | None = None,
    charge: float | None = 0.0,
    soc: float | None = 75.0,
    with_forecast: bool = True,
    forecast_observation: ForecastObservation | None = None,
) -> OutputTelemetry:
    return OutputTelemetry(
        observed_at=at,
        working_mode=mode,
        output_power_w=output,
        pv_power_w=pv,
        battery_voltage=voltage,
        battery_current_chg=charge,
        battery_current_dis=discharge,
        sunrise=SUNRISE,
        sunset=SUNSET,
        forecast=forecast_observation if forecast_observation is not None else (forecast(at) if with_forecast else None),
        battery_soc=soc,
    )


def baseline(**overrides) -> OutputPolicyBaseline:
    values = {
        "exit_battery_v": 24.5,
        "enter_battery_v": 25.5,
        "min_hysteresis_v": 0.5,
        # Explicit fixture boundaries only; the production model defaults to unknown.
        "max_battery_voltage_v": 30.0,
        "max_battery_discharge_a": 90.0,
        "telemetry_max_age_seconds": 180.0,
        "forecast_max_age_seconds": 600.0,
    }
    values.update(overrides)
    return OutputPolicyBaseline(**values)


def established_line_history(*, start_minutes: int = 602, end_minutes: int = 2, pv: float = 800.0) -> tuple[OutputTelemetry, ...]:
    return tuple(
        sample(BASE - timedelta(minutes=minutes), pv=pv, discharge=0.0)
        for minutes in range(start_minutes, end_minutes - 1, -2)
    )


def evaluate(current: OutputTelemetry, config: OutputPolicyBaseline | None = None, history: tuple[OutputTelemetry, ...] = ()):
    return evaluate_output_policy(current, config or baseline(), history, evaluated_at=current.observed_at or BASE)


def evaluate_at(current: OutputTelemetry, evaluated_at: datetime, config: OutputPolicyBaseline | None = None, history: tuple[OutputTelemetry, ...] = ()):
    return evaluate_output_policy(current, config or baseline(), history, evaluated_at=evaluated_at)


class AdaptiveOutputPolicyTests(unittest.TestCase):
    def test_counterexample_unknown_current_limit_never_authorizes_positive_discharge(self):
        history = established_line_history()
        for output, amps in ((0.0, 300.0), (500.0, 5000.0), (500.0, 500.0), (500.0, 85.0), (0.0, 40.0)):
            result = evaluate(sample(output=output, discharge=amps), baseline(max_battery_discharge_a=None), history)
            self.assertNotEqual(result.decision, OutputDecision.CONSIDER)
            self.assertIn(OutputPolicyReason.UNKNOWN_BATTERY_CURRENT_SAFETY, result.reason_codes)

    # --- C-01: current safety must be positively verified, never inferred from absence ---

    def assertCurrentUnverified(self, result, decision=OutputDecision.BLOCK, classification=BatteryEligibility.UNKNOWN):
        self.assertEqual(result.decision, decision)
        self.assertEqual(result.battery_eligibility.classification, classification)
        self.assertIn(OutputPolicyReason.UNKNOWN_BATTERY_CURRENT_SAFETY, result.reason_codes)
        self.assertNotIn(OutputPolicyReason.INVALID_MEASUREMENT, result.reason_codes)
        self.assertNotIn(OutputPolicyReason.INVERT_MODE_OBSERVED, result.reason_codes)

    def evidence(self, result, name):
        return {item.name: item.value for item in result.supporting_evidence}.get(name, "")

    def test_c01_zero_discharge_without_approved_limit_is_not_verified_safe(self):
        # Principle: 0 A observed on mains says nothing about what the battery can
        # safely deliver in a future Invert trial; only an approved limit can.
        unapproved = baseline(max_battery_discharge_a=None)
        line = evaluate(sample(discharge=0.0), unapproved, established_line_history())
        self.assertCurrentUnverified(line)
        self.assertIn("discharge_limit_unapproved", self.evidence(line, "current_safety_unverified"))
        # The dwell is complete, so the evaluator must not blame dwell for the block.
        self.assertNotIn(OutputPolicyReason.MAINS_DWELL_UNPROVEN, line.reason_codes)

        invert_history = tuple(sample(BASE - timedelta(minutes=m), mode="Invert Mode", output=500.0, discharge=0.0)
                               for m in range(602, 0, -2))
        invert = evaluate(sample(mode="Invert Mode", output=500.0, discharge=0.0), unapproved, invert_history)
        self.assertCurrentUnverified(invert)

    def test_c01_approved_discharge_limit_with_zero_current_allows_candidate(self):
        # Principle: with an operator-approved limit and complete evidence a shadow
        # candidate stays reachable; the fix must not block everything.
        result = evaluate(sample(discharge=0.0, charge=0.0), baseline(max_battery_discharge_a=90.0), established_line_history())
        self.assertEqual(result.decision, OutputDecision.CONSIDER)
        self.assertEqual(result.battery_eligibility.classification, BatteryEligibility.ELIGIBLE)
        self.assertNotIn(OutputPolicyReason.UNKNOWN_BATTERY_CURRENT_SAFETY, result.reason_codes)
        self.assertTrue(result.shadow_only)
        self.assertFalse(result.command_executed)

    def test_c01_missing_discharge_reading_blocks_line_and_invert(self):
        # Principle: an unavailable reading is unknown, never zero.
        line = evaluate(sample(discharge=None), history=established_line_history())
        self.assertCurrentUnverified(line)
        self.assertIn(OutputPolicyReason.UNKNOWN_TELEMETRY, line.reason_codes)
        self.assertIn("discharge_reading_missing", self.evidence(line, "current_safety_unverified"))

        invert_history = tuple(sample(BASE - timedelta(minutes=m), mode="Invert Mode", output=500.0, discharge=40.0)
                               for m in range(602, 0, -2))
        invert = evaluate(sample(mode="Invert Mode", output=500.0, discharge=None), baseline(max_battery_discharge_a=70.0), invert_history)
        self.assertCurrentUnverified(invert)
        self.assertIn(OutputPolicyReason.UNKNOWN_TELEMETRY, invert.reason_codes)

    def test_c01_invert_discharge_limit_boundary(self):
        # Principle: the approved limit is inclusive; exceeding it is a confirmed
        # violation (INELIGIBLE), not a sensor fault and not a healthy HOLD.
        config = baseline(max_battery_discharge_a=70.0)
        for amps, decision in ((40.0, OutputDecision.HOLD), (70.0, OutputDecision.HOLD), (70.5, OutputDecision.BLOCK), (85.0, OutputDecision.BLOCK)):
            history = tuple(sample(BASE - timedelta(minutes=m), mode="Invert Mode", output=500.0, discharge=40.0)
                            for m in range(602, 0, -2))
            result = evaluate(sample(mode="Invert Mode", output=500.0, discharge=amps), config, history)
            self.assertEqual(result.decision, decision, amps)
            self.assertNotIn(OutputPolicyReason.INVALID_MEASUREMENT, result.reason_codes)
            if decision is OutputDecision.HOLD:
                self.assertEqual(result.battery_eligibility.classification, BatteryEligibility.STABLE)
                self.assertNotIn(OutputPolicyReason.HIGH_BATTERY_DISCHARGE, result.reason_codes)
            else:
                self.assertEqual(result.battery_eligibility.classification, BatteryEligibility.INELIGIBLE)
                self.assertIn(OutputPolicyReason.HIGH_BATTERY_DISCHARGE, result.reason_codes)
                self.assertNotIn(OutputPolicyReason.INVERT_MODE_OBSERVED, result.reason_codes)

    def test_c01_positive_charge_requires_an_approved_charge_limit(self):
        # Principle: observed charging can only be judged safe against an approved
        # charge limit; a measured 0 A charge needs no charge limit.
        history = established_line_history()
        for amps in (0.5, 40.0):
            result = evaluate(sample(discharge=0.0, charge=amps), baseline(), history)
            self.assertCurrentUnverified(result)
            self.assertIn("charge_limit_unapproved", self.evidence(result, "current_safety_unverified"))
        zero = evaluate(sample(discharge=0.0, charge=0.0), baseline(), history)
        self.assertEqual(zero.decision, OutputDecision.CONSIDER)
        self.assertEqual(zero.battery_eligibility.classification, BatteryEligibility.ELIGIBLE)

        charged_earlier = list(history)
        charged_earlier[-20] = sample(charged_earlier[-20].observed_at, discharge=0.0, charge=30.0)
        earlier = evaluate(sample(discharge=0.0, charge=0.0), baseline(), tuple(charged_earlier))
        self.assertCurrentUnverified(earlier)

        missing = evaluate(sample(discharge=0.0, charge=None), baseline(max_battery_charge_a=50.0), history)
        self.assertCurrentUnverified(missing)
        self.assertIn("charge_reading_missing", self.evidence(missing, "current_safety_unverified"))

        limited = baseline(max_battery_charge_a=60.0)
        for amps in (40.0, 60.0):
            ok = evaluate(sample(discharge=0.0, charge=amps), limited, history)
            self.assertEqual(ok.decision, OutputDecision.CONSIDER, amps)
            self.assertEqual(ok.battery_eligibility.classification, BatteryEligibility.ELIGIBLE)
            self.assertNotIn(OutputPolicyReason.HIGH_BATTERY_CHARGE, ok.reason_codes)
        over = evaluate(sample(discharge=0.0, charge=60.5), limited, history)
        self.assertEqual(over.decision, OutputDecision.BLOCK)
        self.assertEqual(over.battery_eligibility.classification, BatteryEligibility.INELIGIBLE)
        self.assertIn(OutputPolicyReason.HIGH_BATTERY_CHARGE, over.reason_codes)
        self.assertNotIn(OutputPolicyReason.INVALID_MEASUREMENT, over.reason_codes)

    def test_c01_simultaneous_positive_currents_are_conflicting_evidence(self):
        # Principle: the sensor contract does not say whether both channels can be
        # positive at once, so the reading is uncertain, not invalid and not safe.
        config = baseline(max_battery_discharge_a=500.0, max_battery_charge_a=500.0)
        for amps in (0.1, 10.0, 100.0):
            line = evaluate(sample(discharge=amps, charge=amps), config, established_line_history())
            self.assertCurrentUnverified(line)
            self.assertIn(OutputPolicyReason.INCONSISTENT_BATTERY_CURRENT, line.reason_codes)
        invert_history = tuple(sample(BASE - timedelta(minutes=m), mode="Invert Mode", output=500.0, discharge=10.0)
                               for m in range(602, 0, -2))
        invert = evaluate(sample(mode="Invert Mode", output=500.0, discharge=10.0, charge=10.0), config, invert_history)
        self.assertCurrentUnverified(invert)
        self.assertIn(OutputPolicyReason.INCONSISTENT_BATTERY_CURRENT, invert.reason_codes)

    def test_c01_current_safety_never_suppresses_voltage_sag(self):
        # Principle: a confirmed collapse is protective evidence whatever the current
        # reading or limit status; restriction outranks uncertainty.
        prefix = [point for point in established_line_history(end_minutes=12) if point.observed_at <= BASE - timedelta(minutes=12)]
        prefix.extend(sample(BASE - timedelta(minutes=m), discharge=0.0) for m in (10, 8, 6, 4, 2, 0))
        at = BASE + timedelta(minutes=6)
        for limit in (None, 70.0):
            for amps in (0.0, 49.0, 50.0, 85.0, 100.0):
                trial = tuple(prefix) + tuple(
                    sample(BASE + timedelta(minutes=m), mode="Invert Mode", voltage=v, output=2000.0, discharge=amps)
                    for m, v in ((1, 25.5), (3, 25.0), (5, 24.4)))
                result = evaluate_at(sample(at, voltage=25.8, discharge=0.0), at, baseline(max_battery_discharge_a=limit), trial)
                label = (limit, amps)
                self.assertEqual(result.decision, OutputDecision.BLOCK, label)
                self.assertIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes, label)
                self.assertEqual(result.lockout_expires_at, BASE + timedelta(minutes=65), label)
                self.assertEqual(result.battery_eligibility.classification, BatteryEligibility.INELIGIBLE, label)
                self.assertNotIn(OutputPolicyReason.INVALID_MEASUREMENT, result.reason_codes, label)
                self.assertEqual(OutputPolicyReason.UNKNOWN_BATTERY_CURRENT_SAFETY in result.reason_codes, limit is None, label)
                self.assertEqual(OutputPolicyReason.HIGH_BATTERY_DISCHARGE in result.reason_codes, limit is not None and amps > limit, label)

    def test_m01_decision_constructor_normalizes_collections(self):
        # Principle: a frozen result must not share writable state with its caller.
        result = evaluate(sample(discharge=0.0), history=established_line_history())
        reasons = list(result.reason_codes)
        evidence = list(result.supporting_evidence)
        copy = OutputPolicyDecision(**{**result.__dict__, "reason_codes": reasons, "supporting_evidence": evidence})
        reasons.append(OutputPolicyReason.COOLDOWN)
        evidence.clear()
        self.assertIsInstance(copy.reason_codes, tuple)
        self.assertIsInstance(copy.supporting_evidence, tuple)
        self.assertEqual(copy.reason_codes, result.reason_codes)
        self.assertEqual(copy.supporting_evidence, result.supporting_evidence)
        self.assertEqual(copy, result)
        with self.assertRaises((TypeError, ValueError)):
            OutputPolicyDecision(**{**result.__dict__, "reason_codes": ("NOT_A_REASON",)})
        with self.assertRaises(TypeError):
            OutputPolicyDecision(**{**result.__dict__, "supporting_evidence": ({"name": "x"},)})

    def test_m01_assessments_reject_mutable_and_wrongly_typed_fields(self):
        caller_values = [12.0]
        with self.assertRaises((TypeError, ValueError)):
            SolarOpportunityAssessment(SolarOpportunity.STABLE, pv_trend_w=caller_values)
        with self.assertRaises((TypeError, ValueError)):
            BatteryEligibilityAssessment(BatteryEligibility.STABLE, voltage_drop_v=caller_values)

        for kwargs in (
            {"classification": []},
            {"daylight_elapsed_seconds": []},
            {"daylight_remaining_seconds": []},
            {"pv_samples": True},
            {"pv_samples": []},
            {"pv_trend_w": []},
            {"forecast_quality": []},
        ):
            values = {"classification": SolarOpportunity.STABLE, **kwargs}
            with self.subTest(solar=kwargs), self.assertRaises((TypeError, ValueError)):
                SolarOpportunityAssessment(**values)
        for kwargs in (
            {"classification": []},
            {"voltage_slope_v_per_min": []},
            {"voltage_drop_v": []},
            {"recent_mode_changes": True},
            {"failed_trial_duration_seconds": []},
        ):
            values = {"classification": BatteryEligibility.STABLE, **kwargs}
            with self.subTest(battery=kwargs), self.assertRaises((TypeError, ValueError)):
                BatteryEligibilityAssessment(**values)

        with self.assertRaises(TypeError):
            Evidence(name=[], value="immutable value required")

    def test_m01_decision_rejects_mutable_or_incompatible_public_fields(self):
        result = evaluate(sample(discharge=0.0), history=established_line_history())
        mutable = [12.0]
        invalid_fields = (
            ("decision", []),
            ("data_quality", []),
            ("solar_opportunity", []),
            ("battery_eligibility", []),
            ("observed_at", []),
            ("evaluated_at", []),
            ("data_age_seconds", mutable),
            ("baseline_exit_battery_v", mutable),
            ("baseline_enter_battery_v", mutable),
            ("baseline_max_battery_voltage_v", mutable),
            ("effective_exit_battery_v", mutable),
            ("effective_enter_battery_v", mutable),
            ("lockout_expires_at", mutable),
            ("shadow_only", []),
            ("command_executed", []),
        )
        for field_name, value in invalid_fields:
            with self.subTest(field=field_name), self.assertRaises((TypeError, ValueError)):
                OutputPolicyDecision(**{**result.__dict__, field_name: value})

        # No mutable object can be smuggled into an enum-valued public field.
        with self.assertRaises((TypeError, ValueError)):
            OutputPolicyDecision(**{**result.__dict__, "decision": mutable})
        with self.assertRaises((TypeError, ValueError)):
            OutputPolicyDecision(**{**result.__dict__, "reason_codes": (["SHADOW_ONLY"],)})
        with self.assertRaises(TypeError):
            OutputPolicyDecision(**{**result.__dict__, "supporting_evidence": {}})

    def test_m01_valid_public_graph_is_frozen_and_evaluation_is_repeatable(self):
        history = established_line_history()
        invert_history = tuple(
            sample(BASE - timedelta(minutes=m), mode="Invert Mode", output=500.0, discharge=40.0, charge=0.0)
            for m in range(602, 0, -2)
        )
        snapshots = (
            sample(discharge=0.0, charge=0.0),
            sample(discharge=0.0, charge=0.0, pv=100.0),
            sample(mode="Invert Mode", discharge=40.0, charge=0.0),
        )
        histories = (history, history, invert_history)
        results = []
        for current, recent in zip(snapshots, histories):
            first = evaluate(current, history=recent)
            second = evaluate(current, history=recent)
            self.assertEqual(first, second)
            results.append(first)

        self.assertEqual(
            tuple(result.decision for result in results),
            (OutputDecision.CONSIDER, OutputDecision.BLOCK, OutputDecision.HOLD),
        )
        for result in results:
            public_copy = OutputPolicyDecision(**result.__dict__)
            self.assertEqual(public_copy, result)
            self.assertIsInstance(public_copy.reason_codes, tuple)
            self.assertIsInstance(public_copy.supporting_evidence, tuple)
            self.assertIsInstance(public_copy.solar_opportunity, SolarOpportunityAssessment)
            self.assertIsInstance(public_copy.battery_eligibility, BatteryEligibilityAssessment)
            with self.assertRaises(FrozenInstanceError):
                public_copy.solar_opportunity.classification = SolarOpportunity.WEAK
            with self.assertRaises(FrozenInstanceError):
                public_copy.battery_eligibility.classification = BatteryEligibility.UNKNOWN
            with self.assertRaises(ValueError):
                OutputPolicyDecision(**{**result.__dict__, "command_executed": True})

    def test_m01_decision_normalizes_mutable_timezone_state(self):
        class AdjustableTimezone(tzinfo):
            def __init__(self):
                self.offset = timedelta(hours=1)

            def utcoffset(self, dt):
                return self.offset

            def dst(self, dt):
                return timedelta(0)

        result = evaluate(sample(discharge=0.0), history=established_line_history())
        caller_zone = AdjustableTimezone()
        caller_time = datetime(2026, 11, 2, 11, 0, tzinfo=caller_zone)
        payload = {**result.__dict__, "observed_at": caller_time, "evaluated_at": caller_time}
        decision = OutputPolicyDecision(**payload)
        expected = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
        self.assertEqual(decision.evaluated_at, expected)
        self.assertIs(decision.evaluated_at.tzinfo, UTC)

        caller_zone.offset = timedelta(hours=2)
        self.assertEqual(decision.evaluated_at, expected)
        self.assertEqual(decision.observed_at, expected)

    def test_counterexample_configured_current_violation_is_not_invalid_measurement(self):
        result = evaluate(sample(output=500.0, discharge=85.0), baseline(max_battery_discharge_a=70.0), established_line_history())
        self.assertIn(OutputPolicyReason.HIGH_BATTERY_DISCHARGE, result.reason_codes)
        self.assertNotIn(OutputPolicyReason.INVALID_MEASUREMENT, result.reason_codes)
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertEqual(result.battery_eligibility.classification, BatteryEligibility.INELIGIBLE)

    def test_configured_current_limit_violation_remains_sag_evidence(self):
        prefix = [point for point in established_line_history(end_minutes=12)
                  if point.observed_at <= BASE - timedelta(minutes=12)]
        prefix.extend(sample(BASE - timedelta(minutes=m), mode="Line Mode", discharge=0.0)
                      for m in (10, 8, 6, 4, 2, 0))
        trial = tuple(prefix) + tuple(
            sample(BASE + timedelta(minutes=m), mode="Invert Mode", voltage=v, output=1200.0, discharge=85.0)
            for m, v in ((1, 26.0), (3, 25.4), (5, 24.8))
        )
        at = BASE + timedelta(minutes=6)
        result = evaluate_at(sample(at, mode="Line Mode", voltage=25.8, discharge=0.0), at,
                             baseline(max_battery_discharge_a=70.0), trial)
        self.assertIn(OutputPolicyReason.HIGH_BATTERY_DISCHARGE, result.reason_codes)
        self.assertIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes)
        self.assertNotIn(OutputPolicyReason.INVALID_MEASUREMENT, result.reason_codes)
        self.assertEqual(result.lockout_expires_at, BASE + timedelta(minutes=65))

    def test_counterexample_escalated_lockout_needs_its_full_lookback(self):
        evaluated = datetime(2026, 11, 2, 16, 0, tzinfo=UTC)
        # Events ending 09:15, 11:10 and 13:10 escalate the last lockout to 16:10.
        event_points = {}
        for ended in (datetime(2026, 11, 2, 9, 15, tzinfo=UTC), datetime(2026, 11, 2, 11, 10, tzinfo=UTC), datetime(2026, 11, 2, 13, 10, tzinfo=UTC)):
            for offset, voltage in ((3, 26.0), (2, 25.5), (1, 25.0), (0, 24.8)):
                at = ended - timedelta(minutes=offset)
                event_points[at] = sample(at, mode="Invert Mode", voltage=voltage, output=1000.0, pv=800.0, discharge=50.0)
            event_points[ended + timedelta(minutes=1)] = sample(ended + timedelta(minutes=1), mode="Line Mode", voltage=25.8, pv=800.0, discharge=0.0)
        history = tuple(
            event_points.get(at, sample(at, pv=800.0, discharge=0.0))
            for minute in range(540, 0, -1)
            for at in (evaluated - timedelta(minutes=minute),)
        )
        history = tuple(point for point in history if point.observed_at >= evaluated - timedelta(hours=8))
        current = sample(evaluated, mode="Line Mode", voltage=26.0, pv=800.0)
        result = evaluate_at(current, evaluated, baseline(), history)
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertEqual(result.lockout_expires_at, datetime(2026, 11, 2, 16, 10, tzinfo=UTC))
        self.assertNotIn(OutputPolicyReason.INSUFFICIENT_HISTORY, result.reason_codes)

    def test_counterexample_old_recovered_dip_is_not_current_sag(self):
        points = [
            sample(BASE - timedelta(minutes=m), mode=("Invert Mode" if m <= 190 else "Line Mode"),
                   voltage=(25.5 if m == 120 else 26.0), output=1000.0, discharge=0.0)
            for m in range(600, 0, -2)
        ]
        current = sample(mode="Invert Mode", voltage=26.0, output=1000.0, discharge=0.0)
        result = evaluate(current, history=tuple(points))
        self.assertEqual(result.decision, OutputDecision.HOLD)
        self.assertNotIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes)

    def test_counterexample_falling_load_does_not_erase_confirmed_voltage_collapse(self):
        history = list(established_line_history(start_minutes=600, end_minutes=20))
        history.extend(sample(BASE - timedelta(minutes=m), mode="Invert Mode", voltage=v, output=w)
                       for m, v, w in ((18, 26.0, 2000.0), (16, 25.8, 1700.0), (14, 25.6, 1400.0),
                                       (12, 25.4, 1100.0), (10, 25.2, 900.0), (8, 25.0, 700.0),
                                       (6, 24.8, 600.0), (4, 24.7, 550.0), (2, 24.6, 500.0)))
        current = sample(BASE, mode="Invert Mode", voltage=24.6, output=500.0)
        result = evaluate(current, history=tuple(history))
        self.assertIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes)
        self.assertEqual(result.battery_eligibility.classification, BatteryEligibility.INELIGIBLE)
        failed_history = tuple(history) + (current,)
        line_at = BASE + timedelta(minutes=2)
        returned_to_line = sample(line_at, mode="Line Mode", voltage=25.8, output=500.0, discharge=0.0)
        failed = evaluate_at(returned_to_line, line_at, history=failed_history)
        self.assertIn(OutputPolicyReason.FAST_VOLTAGE_SAG, failed.reason_codes)
        self.assertEqual(failed.lockout_expires_at, BASE + timedelta(minutes=60))

    def test_counterexample_latest_pv_below_productive_threshold_is_weak(self):
        history = tuple(sample(BASE - timedelta(minutes=m), pv=(300.0 if m > 14 else 800.0), discharge=0.0)
                        for m in range(602, 0, -2))
        result = evaluate(sample(pv=460.0, discharge=0.0), history=history)
        self.assertEqual(result.solar_opportunity.classification, SolarOpportunity.WEAK)
        self.assertNotIn(OutputPolicyReason.PRODUCTIVE_SOLAR, result.reason_codes)
        self.assertNotEqual(result.decision, OutputDecision.CONSIDER)

    def test_counterexample_recent_unknown_mode_interval_blocks_without_fake_expiry(self):
        span = {
            72: "Line Mode", 70: "Fault", 68: "Fault", 66: "Invert Mode",
            64: "Fault", 62: "Fault", 60: "Line Mode", 58: "Fault",
            56: "Fault", 54: "Invert Mode", 52: "Fault", 50: "Fault", 48: "Line Mode",
        }
        history = tuple(sample(BASE - timedelta(minutes=m), mode=span.get(m, "Line Mode"), discharge=0.0)
                        for m in range(602, 0, -2))
        result = evaluate(sample(mode="Line Mode", discharge=0.0), history=history)
        self.assertNotEqual(result.decision, OutputDecision.CONSIDER)
        self.assertIsNone(result.lockout_expires_at)

    def test_positive_discharge_without_approved_limit_blocks_both_line_and_invert(self):
        unapproved = baseline(max_battery_discharge_a=None)
        line = evaluate(sample(output=0.0, discharge=300.0), unapproved, established_line_history())
        self.assertEqual(line.decision, OutputDecision.BLOCK)
        self.assertEqual(line.battery_eligibility.classification, BatteryEligibility.UNKNOWN)
        self.assertIn(OutputPolicyReason.UNKNOWN_BATTERY_CURRENT_SAFETY, line.reason_codes)
        invert_history = tuple(
            sample(BASE - timedelta(minutes=m), mode="Invert Mode", output=500.0, discharge=0.0)
            for m in range(602, 0, -2)
        )
        invert = evaluate(sample(mode="Invert Mode", output=500.0, discharge=300.0), unapproved, invert_history)
        self.assertEqual(invert.decision, OutputDecision.BLOCK)
        self.assertNotIn(OutputPolicyReason.INVERT_MODE_OBSERVED, invert.reason_codes)
        self.assertIn(OutputPolicyReason.UNKNOWN_BATTERY_CURRENT_SAFETY, invert.reason_codes)
        self.assertEqual(invert.battery_eligibility.classification, BatteryEligibility.UNKNOWN)

        historical_current = list(established_line_history())
        historical_current[-1] = sample(historical_current[-1].observed_at, discharge=85.0)
        from_history = evaluate(sample(discharge=0.0), unapproved, tuple(historical_current))
        self.assertEqual(from_history.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.UNKNOWN_BATTERY_CURRENT_SAFETY, from_history.reason_codes)
        self.assertNotIn(OutputPolicyReason.INVALID_MEASUREMENT, from_history.reason_codes)

    def test_protective_current_limit_blocks_hold_without_invalidating_measurement(self):
        history = tuple(
            sample(BASE - timedelta(minutes=m), mode="Invert Mode", output=500.0, discharge=40.0)
            for m in range(602, 0, -2)
        )
        result = evaluate(sample(mode="Invert Mode", output=500.0, discharge=85.0),
                          baseline(max_battery_discharge_a=70.0), history)
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.HIGH_BATTERY_DISCHARGE, result.reason_codes)
        self.assertNotIn(OutputPolicyReason.INVALID_MEASUREMENT, result.reason_codes)
        self.assertNotIn(OutputPolicyReason.INVERT_MODE_OBSERVED, result.reason_codes)

    def test_positive_hold_requires_current_limit_and_valid_battery_evidence(self):
        history = tuple(
            sample(BASE - timedelta(minutes=m), mode="Invert Mode", output=500.0, discharge=40.0)
            for m in range(602, 0, -2)
        )
        result = evaluate(sample(mode="Invert Mode", output=500.0, discharge=40.0),
                          baseline(max_battery_discharge_a=70.0), history)
        self.assertEqual(result.decision, OutputDecision.HOLD)
        self.assertIn(OutputPolicyReason.INVERT_MODE_OBSERVED, result.reason_codes)

    def test_line_enter_voltage_guard_is_independent_of_solar_and_dwell(self):
        result = evaluate(sample(voltage=25.4, discharge=0.0), history=established_line_history())
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.BATTERY_GUARD, result.reason_codes)

    def test_complete_recent_unknown_interval_blocks_candidate_without_expiry(self):
        history = list(established_line_history())
        history[-15:] = [sample(item.observed_at, mode="Fault", discharge=0.0) for item in history[-15:]]
        result = evaluate(sample(discharge=0.0), history=tuple(history))
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.MODE_HISTORY_UNCERTAIN, result.reason_codes)
        self.assertIsNone(result.lockout_expires_at)

    def test_confirmed_churn_expiry_survives_unknown_modes_without_new_expiry(self):
        history = list(established_line_history(end_minutes=20))
        history.extend((sample(BASE - timedelta(minutes=18), mode="Invert Mode", discharge=0.0),
                        sample(BASE - timedelta(minutes=16), mode="Line Mode", discharge=0.0),
                        sample(BASE - timedelta(minutes=14), mode="Invert Mode", discharge=0.0),
                        sample(BASE - timedelta(minutes=12), mode="Line Mode", discharge=0.0),
                        sample(BASE - timedelta(minutes=10), mode="Fault", discharge=0.0),
                        sample(BASE - timedelta(minutes=8), mode="Fault", discharge=0.0)))
        history.extend(sample(BASE - timedelta(minutes=m), mode="Line Mode", discharge=0.0) for m in (6, 4, 2))
        result = evaluate(sample(discharge=0.0), history=tuple(history))
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertEqual(result.lockout_expires_at, BASE + timedelta(minutes=48))
        self.assertIn(OutputPolicyReason.CHURN_LOCKOUT, result.reason_codes)
        self.assertIn(OutputPolicyReason.MODE_HISTORY_UNCERTAIN, result.reason_codes)

    def test_known_modes_separated_by_unknowns_do_not_fabricate_transitions(self):
        history = list(established_line_history(end_minutes=20))
        history.extend((sample(BASE - timedelta(minutes=18), mode="Fault", discharge=0.0),
                        sample(BASE - timedelta(minutes=16), mode="Invert Mode", discharge=0.0),
                        sample(BASE - timedelta(minutes=14), mode="Fault", discharge=0.0),
                        sample(BASE - timedelta(minutes=12), mode="Line Mode", discharge=0.0)))
        history.extend(sample(BASE - timedelta(minutes=m), mode="Line Mode", discharge=0.0) for m in (10, 8, 6, 4, 2))
        result = evaluate(sample(discharge=0.0), history=tuple(history))
        self.assertEqual(result.battery_eligibility.recent_mode_changes, 0)
        self.assertIn(OutputPolicyReason.MODE_HISTORY_UNCERTAIN, result.reason_codes)
        self.assertIsNone(result.lockout_expires_at)

    def test_fault_history_and_short_known_recovery_do_not_prove_stability(self):
        all_fault = tuple(sample(BASE - timedelta(minutes=m), mode="Fault", discharge=0.0)
                          for m in range(602, 0, -2))
        fault_result = evaluate(sample(mode="Fault", discharge=0.0), history=all_fault)
        self.assertEqual(fault_result.decision, OutputDecision.BLOCK)
        self.assertEqual(fault_result.battery_eligibility.classification, BatteryEligibility.UNKNOWN)
        self.assertIsNone(fault_result.lockout_expires_at)

        history = [sample(BASE - timedelta(minutes=m), mode="Fault", discharge=0.0)
                   for m in range(602, 48, -2)]
        history.extend(sample(BASE - timedelta(minutes=m), mode="Line Mode", discharge=0.0)
                       for m in range(46, 0, -2))
        recovered = evaluate(sample(discharge=0.0), history=tuple(history))
        self.assertEqual(recovered.decision, OutputDecision.BLOCK)
        self.assertEqual(recovered.battery_eligibility.classification, BatteryEligibility.UNKNOWN)
        self.assertIn(OutputPolicyReason.MODE_HISTORY_UNCERTAIN, recovered.reason_codes)
        self.assertIsNone(recovered.lockout_expires_at)

    def test_candidate_requires_fresh_forecast_and_full_mains_dwell(self):
        good_history = established_line_history()
        stale_forecast = forecast(BASE, observed_at=BASE - timedelta(hours=1))
        stale = evaluate(sample(discharge=0.0, forecast_observation=stale_forecast), history=good_history)
        self.assertEqual(stale.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.FORECAST_STALE, stale.reason_codes)

        short_dwell = [sample(BASE - timedelta(minutes=m), mode="Invert Mode", discharge=0.0)
                       for m in range(602, 20, -2)]
        short_dwell.extend(sample(BASE - timedelta(minutes=m), mode="Line Mode", discharge=0.0)
                           for m in range(18, 0, -2))
        dwell = evaluate(sample(discharge=0.0), history=tuple(short_dwell))
        self.assertEqual(dwell.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.MAINS_DWELL_UNPROVEN, dwell.reason_codes)

    def test_history_coverage_exact_truncated_and_truncated_old_data(self):
        exact = established_line_history(start_minutes=480)
        current = sample(discharge=0.0)
        enough = evaluate(current, history=exact)
        self.assertEqual(enough.decision, OutputDecision.CONSIDER)
        short = evaluate(current, history=established_line_history(start_minutes=479))
        self.assertEqual(short.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.INSUFFICIENT_HISTORY, short.reason_codes)

        old_bad = list(established_line_history(start_minutes=602))
        old_bad[0] = sample(old_bad[0].observed_at, voltage=0.0, discharge=0.0)
        trimmed = evaluate(current, history=tuple(old_bad))
        self.assertEqual(trimmed.decision, OutputDecision.CONSIDER)
        self.assertNotIn(OutputPolicyReason.INVALID_MEASUREMENT, trimmed.reason_codes)
        old_duplicate = evaluate(current, history=tuple(old_bad) + (old_bad[0],))
        self.assertEqual(old_duplicate.decision, OutputDecision.CONSIDER)

        in_window_duplicate = evaluate(current, history=exact + (exact[-1],))
        self.assertEqual(in_window_duplicate.data_quality, TelemetryQuality.UNKNOWN)
        self.assertNotEqual(in_window_duplicate.decision, OutputDecision.CONSIDER)

    def test_command_execution_true_is_rejected_by_decision_model(self):
        result = evaluate(sample(discharge=0.0), history=established_line_history())
        with self.assertRaises(ValueError):
            OutputPolicyDecision(**{**result.__dict__, "command_executed": True})

    def test_measured_inverter_load_blocks_independently_of_pv(self):
        history = established_line_history()
        for output, pv in ((1499.0, 200.0), (1500.0, 800.0), (5000.0, 600.0)):
            result = evaluate(sample(output=output, pv=pv), history=history)
            self.assertEqual(result.decision, OutputDecision.BLOCK)
            if output >= 1500:
                self.assertIn(OutputPolicyReason.HIGH_MEASURED_LOAD, result.reason_codes)

    def test_demand_exceeding_pv_is_advisory_when_other_evidence_is_safe(self):
        result = evaluate(sample(output=1000.0, pv=600.0, discharge=0.0),
                          baseline(max_battery_discharge_a=90.0), established_line_history(pv=600.0))
        self.assertEqual(result.decision, OutputDecision.CONSIDER)
        self.assertIn(OutputPolicyReason.DEMAND_EXCEEDS_PV, result.reason_codes)

    def test_productive_day_requires_history_and_then_is_considered(self):
        short = evaluate(sample(pv=800.0), history=(sample(BASE - timedelta(minutes=4), pv=800.0),))
        self.assertEqual(short.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.INSUFFICIENT_HISTORY, short.reason_codes)

        history = established_line_history(pv=800.0)
        result = evaluate(sample(pv=800.0, discharge=0.0),
                          baseline(max_battery_discharge_a=90.0), history=history)
        self.assertEqual(result.decision, OutputDecision.CONSIDER)
        self.assertEqual(result.data_quality, TelemetryQuality.FRESH)
        self.assertEqual(result.solar_opportunity.classification, SolarOpportunity.STABLE)
        self.assertEqual(result.battery_eligibility.classification, BatteryEligibility.ELIGIBLE)

    def test_weak_pv_close_to_sunset_is_blocked(self):
        at = datetime(2026, 11, 2, 18, 30, tzinfo=UTC)
        current = sample(at, pv=100.0, voltage=26.0, discharge=0.0)
        history = tuple(sample(at - timedelta(minutes=m), pv=800.0, discharge=0.0)
                        for m in range(602, 0, -2))
        result = evaluate(current, history=history)
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertEqual(result.solar_opportunity.classification, SolarOpportunity.LATE_DAY)
        self.assertIn(OutputPolicyReason.SUNSET_RESERVE, result.reason_codes)

    def test_failed_trial_lockout_persists_until_expiry(self):
        history = list(established_line_history(end_minutes=12))
        history.extend((
            sample(BASE - timedelta(minutes=8), mode="Invert Mode", voltage=26.0, output=1000.0, discharge=0.0),
            sample(BASE - timedelta(minutes=5), mode="Invert Mode", voltage=25.4, output=1000.0, discharge=0.0),
            sample(BASE - timedelta(minutes=2), mode="Invert Mode", voltage=24.8, output=1000.0, discharge=0.0),
        ))
        history = [point for point in history if point.observed_at <= BASE - timedelta(minutes=12)] + history[-3:]
        for minutes in (0, 1, 30, 57):
            current = sample(BASE + timedelta(minutes=minutes), mode="Line Mode", voltage=25.8, output=500.0, discharge=0.0)
            followup = tuple(sample(BASE + timedelta(minutes=m), mode="Line Mode", voltage=25.8, output=500.0, discharge=0.0)
                             for m in range(0, minutes, 2))
            result = evaluate(current, history=tuple(history) + followup)
            self.assertEqual(result.decision, OutputDecision.BLOCK)
            self.assertIn(OutputPolicyReason.COOLDOWN, result.reason_codes)
            self.assertIsNotNone(result.lockout_expires_at)
        expired = sample(BASE + timedelta(minutes=58), mode="Line Mode", voltage=25.8, output=500.0, discharge=0.0)
        followup = tuple(sample(BASE + timedelta(minutes=m), mode="Line Mode", voltage=25.8, output=500.0, discharge=0.0)
                         for m in range(0, 58, 2))
        result = evaluate(expired, history=tuple(history) + followup)
        self.assertNotIn(OutputPolicyReason.COOLDOWN, result.reason_codes)
        self.assertIsNone(result.lockout_expires_at)

    def test_original_five_minute_trial_is_detected_through_settling_window(self):
        history = list(established_line_history())
        history.append(sample(BASE, mode="Line Mode", voltage=26.0, output=2000.0, pv=100.0, discharge=0.0))
        history.extend((
            sample(BASE + timedelta(minutes=1), mode="Invert Mode", voltage=25.5, output=2000.0, pv=100.0, discharge=0.0),
            sample(BASE + timedelta(minutes=3), mode="Invert Mode", voltage=25.0, output=2000.0, pv=100.0, discharge=0.0),
            sample(BASE + timedelta(minutes=5), mode="Invert Mode", voltage=24.4, output=2000.0, pv=100.0, discharge=0.0),
        ))
        continuous_line = tuple(history) + tuple(
            sample(BASE + timedelta(minutes=minute), mode="Line Mode", voltage=25.8, output=500.0, discharge=0.0)
            for minute in range(6, 65, 2)
        )
        for minute in (6, 7, 54, 64):
            current = sample(BASE + timedelta(minutes=minute), mode="Line Mode", voltage=25.8, output=500.0)
            valid_past = tuple(point for point in continuous_line if point.observed_at < current.observed_at)
            result = evaluate(current, history=valid_past)
            self.assertEqual(result.decision, OutputDecision.BLOCK)
            self.assertIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes)
            self.assertIn(OutputPolicyReason.COOLDOWN, result.reason_codes)
            self.assertEqual(result.lockout_expires_at, BASE + timedelta(minutes=65))
        expired = sample(BASE + timedelta(minutes=65), mode="Line Mode", voltage=25.8, output=500.0)
        valid_past = tuple(point for point in continuous_line if point.observed_at < expired.observed_at)
        result = evaluate(expired, history=valid_past)
        self.assertNotIn(OutputPolicyReason.COOLDOWN, result.reason_codes)

    def test_stable_pv_requires_productive_boundary(self):
        for pv in (260.0, 499.0):
            result = evaluate(sample(pv=pv), history=established_line_history(pv=pv))
            self.assertEqual(result.decision, OutputDecision.BLOCK)
            self.assertEqual(result.solar_opportunity.classification, SolarOpportunity.WEAK)
        result = evaluate(sample(pv=500.0), history=established_line_history(pv=500.0))
        self.assertEqual(result.solar_opportunity.classification, SolarOpportunity.STABLE)

    def test_line_voltage_drift_is_not_inverter_sag(self):
        history = list(established_line_history())
        history[-5:] = [sample(item.observed_at, voltage=26.0 - index * 0.1) for index, item in enumerate(history[-5:])]
        result = evaluate(sample(voltage=25.6, discharge=0.0), history=tuple(history))
        self.assertEqual(result.decision, OutputDecision.CONSIDER)
        self.assertNotIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes)

    def test_load_ramp_does_not_create_comparable_sag(self):
        history = list(established_line_history(end_minutes=14))
        history.extend(
            sample(BASE - timedelta(minutes=minutes), mode="Invert Mode", output=500.0 + index * 200.0, voltage=26.0 - index * 0.08)
            for index, minutes in enumerate((12, 10, 8, 6, 4, 2))
        )
        result = evaluate(sample(mode="Invert Mode", output=1700.0, voltage=25.5), history=tuple(history))
        self.assertNotIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes)

    def test_temporary_rising_load_inside_terminal_window_confounds_sag(self):
        history = tuple(sample(BASE - timedelta(minutes=m), mode="Line Mode", discharge=0.0)
                        for m in range(602, 10, -2))
        invert = (
            sample(BASE - timedelta(minutes=8), mode="Invert Mode", voltage=26.0, output=2000.0, discharge=0.0),
            sample(BASE - timedelta(minutes=6), mode="Invert Mode", voltage=25.5, output=2600.0, discharge=0.0),
            sample(BASE - timedelta(minutes=4), mode="Invert Mode", voltage=25.8, output=2300.0, discharge=0.0),
            sample(BASE - timedelta(minutes=2), mode="Invert Mode", voltage=26.0, output=2000.0, discharge=0.0),
        )
        result = evaluate(sample(mode="Invert Mode", voltage=26.0, output=2000.0, discharge=0.0),
                          history=history + invert)
        self.assertNotIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes)

    def test_small_single_settling_step_is_not_a_failed_trial(self):
        history = list(established_line_history(end_minutes=6))
        history = [point for point in history if point.observed_at <= BASE - timedelta(minutes=6)]
        history.extend((
            sample(BASE - timedelta(minutes=4), mode="Line Mode", voltage=26.0),
            sample(BASE - timedelta(minutes=2), mode="Invert Mode", voltage=25.6),
        ))
        result = evaluate(sample(mode="Line Mode", voltage=26.0), history=tuple(history))
        self.assertNotIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes)
        self.assertNotIn(OutputPolicyReason.COOLDOWN, result.reason_codes)

    def test_invalid_history_measurements_block_without_feeding_sag(self):
        history = list(established_line_history())
        history[-1] = sample(history[-1].observed_at, voltage=-1.0, discharge=0.0)
        result = evaluate(sample(), history=tuple(history))
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.INVALID_MEASUREMENT, result.reason_codes)

    def test_significant_discharge_is_not_ignored(self):
        for current in (sample(discharge=99.0), sample(output=0.0, discharge=90.0)):
            result = evaluate(current, baseline(significant_battery_discharge_a=50.0), established_line_history())
            self.assertEqual(result.decision, OutputDecision.BLOCK)
            self.assertIn(OutputPolicyReason.HIGH_BATTERY_DISCHARGE, result.reason_codes)

    def test_configured_lockout_duration_and_churn_expiry_are_honoured(self):
        history = list(established_line_history(end_minutes=12))
        history = [point for point in history if point.observed_at <= BASE - timedelta(minutes=12)]
        history.extend(sample(BASE - timedelta(minutes=10 - i * 2), mode=mode) for i, mode in enumerate(("Invert Mode", "Line Mode", "Invert Mode", "Line Mode", "Line Mode")))
        result = evaluate(sample(mode="Line Mode"), history=tuple(history))
        self.assertEqual(result.lockout_expires_at, BASE + timedelta(minutes=56))

        trial_history = list(established_line_history(end_minutes=12))
        trial_history = [point for point in trial_history if point.observed_at <= BASE - timedelta(minutes=12)]
        trial_history.extend((
            sample(BASE - timedelta(minutes=8), mode="Invert Mode", voltage=26.0),
            sample(BASE - timedelta(minutes=5), mode="Invert Mode", voltage=25.0),
        ))
        configured = baseline(failed_trial_lockout_seconds=7200.0)
        result = evaluate(sample(mode="Line Mode"), configured, tuple(trial_history))
        self.assertEqual(result.lockout_expires_at, BASE + timedelta(minutes=115))

    def test_default_forecast_boundary_and_determinism_are_explicit(self):
        fresh_forecast = forecast(BASE, observed_at=BASE - timedelta(minutes=180), valid_at=BASE + timedelta(hours=1))
        stale_forecast = forecast(BASE, observed_at=BASE - timedelta(minutes=181), valid_at=BASE + timedelta(hours=1))
        fresh = evaluate(sample(forecast_observation=fresh_forecast), config=OutputPolicyBaseline(exit_battery_v=24.5, enter_battery_v=25.5, min_hysteresis_v=0.5), history=established_line_history())
        stale = evaluate(sample(forecast_observation=stale_forecast), config=OutputPolicyBaseline(exit_battery_v=24.5, enter_battery_v=25.5, min_hysteresis_v=0.5), history=established_line_history())
        self.assertEqual(fresh.solar_opportunity.forecast_quality, ForecastQuality.FRESH)
        self.assertEqual(stale.solar_opportunity.forecast_quality, ForecastQuality.STALE)
        self.assertEqual(fresh, evaluate(sample(forecast_observation=fresh_forecast), config=OutputPolicyBaseline(exit_battery_v=24.5, enter_battery_v=25.5, min_hysteresis_v=0.5), history=established_line_history()))
        with patch("builtins.open", side_effect=AssertionError("filesystem access")), \
             patch("socket.socket", side_effect=AssertionError("network access")), \
             patch("subprocess.Popen", side_effect=AssertionError("process launch")), \
             patch("time.time", side_effect=AssertionError("wall clock access")):
            evaluate(sample(), history=established_line_history())

    def test_n01_real_collapse_detection_is_independent_of_unapproved_current_limits(self):
        config = baseline(max_battery_discharge_a=None, max_battery_charge_a=None, significant_battery_discharge_a=None)
        prefix = list(established_line_history(end_minutes=12))
        prefix = [point for point in prefix if point.observed_at <= BASE - timedelta(minutes=12)]
        prefix.extend(sample(BASE - timedelta(minutes=m), mode="Line Mode", discharge=0.0) for m in (10, 8, 6, 4, 2, 0))
        for amps in (49.0, 50.0, 85.0, 100.0):
            trial = tuple(prefix) + tuple(
                sample(BASE + timedelta(minutes=m), mode="Invert Mode", voltage=v, output=2000.0, discharge=amps, pv=800.0)
                for m, v in ((1, 25.5), (3, 25.0), (5, 24.4))
            )
            line_at = BASE + timedelta(minutes=6)
            line = sample(line_at, mode="Line Mode", voltage=25.8, output=500.0, discharge=0.0)
            result = evaluate_at(line, line_at, config, trial)
            self.assertIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes)
            self.assertEqual(result.lockout_expires_at, BASE + timedelta(minutes=65))
            self.assertNotIn(OutputPolicyReason.INVALID_MEASUREMENT, result.reason_codes)
            self.assertEqual(result.data_quality, TelemetryQuality.FRESH)
            self.assertNotIn(OutputPolicyReason.INSUFFICIENT_HISTORY, result.reason_codes)
        self.assertIsNone(config.max_battery_discharge_a)

    def test_n02_history_coverage_includes_escalated_restriction_duration(self):
        from app.control.adaptive_output_policy import _required_history_seconds

        required_minutes = int(_required_history_seconds(baseline()) / 60)
        self.assertEqual(required_minutes, 480)
        sufficient = evaluate(sample(discharge=0.0), history=established_line_history(start_minutes=required_minutes))
        self.assertEqual(sufficient.decision, OutputDecision.CONSIDER)
        truncated = evaluate(sample(discharge=0.0), history=established_line_history(start_minutes=required_minutes - 1))
        self.assertEqual(truncated.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.INSUFFICIENT_HISTORY, truncated.reason_codes)
        varied = baseline(
            failed_trial_lockout_seconds=2 * 60 * 60,
            max_failed_trial_escalation=4,
            max_churn_lockout_seconds=10 * 60 * 60,
            mode_change_window_seconds=90 * 60,
            voltage_sag_window_seconds=30 * 60,
        )
        self.assertEqual(_required_history_seconds(varied), (8 + 10 + 1.5) * 60 * 60)

    def test_n03_long_session_terminal_collapse_is_restricted_but_healthy_long_session_is_not(self):
        line_history = tuple(sample(BASE - timedelta(minutes=m), pv=800.0, discharge=0.0) for m in range(602, 14, -2))
        invert = tuple(sample(BASE - timedelta(minutes=m), mode="Invert Mode", voltage=v, output=1000.0)
                       for m, v in ((14, 26.0), (11, 25.9), (8, 25.4), (5, 24.9), (2, 24.4)))
        trial = line_history + invert
        collapse = evaluate(sample(mode="Line Mode", voltage=25.8), history=trial)
        self.assertIn(OutputPolicyReason.FAST_VOLTAGE_SAG, collapse.reason_codes)
        self.assertIn(OutputPolicyReason.COOLDOWN, collapse.reason_codes)
        self.assertEqual(collapse.lockout_expires_at, BASE + timedelta(minutes=58))

        healthy_line = tuple(sample(BASE - timedelta(minutes=m), pv=800.0) for m in range(600, 194, -2))
        healthy_invert = tuple(sample(BASE - timedelta(minutes=m), mode="Invert Mode", voltage=26.0, output=1000.0) for m in range(192, 0, -2))
        healthy = healthy_line + healthy_invert
        recovered = evaluate(sample(mode="Line Mode", voltage=26.0), history=healthy)
        self.assertNotIn(OutputPolicyReason.COOLDOWN, recovered.reason_codes)

    def test_short_and_long_terminal_sags_have_evidence_but_recovered_dips_do_not(self):
        for duration_minutes in (4, 12, 30):
            prior = tuple(sample(BASE - timedelta(minutes=m), mode="Line Mode", pv=800.0, discharge=0.0)
                          for m in range(602, duration_minutes + 1, -2))
            invert_points = []
            for elapsed in range(0, duration_minutes + 1, 2):
                voltage = 26.0 - (1.6 * elapsed / duration_minutes)
                at = BASE - timedelta(minutes=duration_minutes - elapsed)
                invert_points.append(sample(at, mode="Invert Mode", voltage=voltage, output=1000.0, discharge=0.0))
            history = prior + tuple(invert_points[:-1])
            current = invert_points[-1]
            result = evaluate(current, history=history)
            self.assertIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes)
            self.assertEqual(result.battery_eligibility.classification, BatteryEligibility.INELIGIBLE)
            ended_line_at = BASE + timedelta(minutes=2)
            completed = evaluate_at(
                sample(ended_line_at, mode="Line Mode", voltage=25.8, output=500.0, discharge=0.0),
                ended_line_at,
                history=history + (current,),
            )
            self.assertIn(OutputPolicyReason.FAST_VOLTAGE_SAG, completed.reason_codes)
            self.assertIn(OutputPolicyReason.COOLDOWN, completed.reason_codes)
            self.assertEqual(completed.lockout_expires_at, BASE + timedelta(minutes=60))

        # A 0.4 V settling step followed by three stable hours is not terminal collapse.
        settle_history = tuple(
            sample(BASE - timedelta(minutes=m), mode=("Invert Mode" if m <= 180 else "Line Mode"),
                   voltage=(25.6 if m <= 178 else 26.0), output=1000.0, discharge=0.0)
            for m in range(602, 0, -2)
        )
        settled = evaluate(sample(mode="Invert Mode", voltage=25.6, output=1000.0, discharge=0.0), history=settle_history)
        self.assertEqual(settled.decision, OutputDecision.HOLD)
        self.assertNotIn(OutputPolicyReason.FAST_VOLTAGE_SAG, settled.reason_codes)
        self.assertNotIn(OutputPolicyReason.COOLDOWN, settled.reason_codes)
        settling_line_at = BASE + timedelta(minutes=2)
        settling_return = evaluate_at(
            sample(settling_line_at, mode="Line Mode", voltage=25.8, discharge=0.0),
            settling_line_at,
            history=settle_history + (sample(BASE, mode="Invert Mode", voltage=25.6, output=1000.0, discharge=0.0),),
        )
        self.assertNotIn(OutputPolicyReason.FAST_VOLTAGE_SAG, settling_return.reason_codes)
        self.assertNotIn(OutputPolicyReason.COOLDOWN, settling_return.reason_codes)

        # A three-hour-old dip recovered in the same continuous Invert segment is past evidence.
        recovered_history = tuple(
            sample(BASE - timedelta(minutes=m), mode=("Invert Mode" if m <= 180 else "Line Mode"),
                   voltage=(25.5 if m == 120 else 26.0), output=1000.0, discharge=0.0)
            for m in range(602, 0, -2)
        )
        recovered = evaluate(sample(mode="Invert Mode", voltage=26.0, output=1000.0, discharge=0.0), history=recovered_history)
        self.assertEqual(recovered.decision, OutputDecision.HOLD)
        self.assertNotIn(OutputPolicyReason.FAST_VOLTAGE_SAG, recovered.reason_codes)
        self.assertNotIn(OutputPolicyReason.COOLDOWN, recovered.reason_codes)
        recovered_line_at = BASE + timedelta(minutes=2)
        recovered_return = evaluate_at(
            sample(recovered_line_at, mode="Line Mode", voltage=26.0, discharge=0.0),
            recovered_line_at,
            history=recovered_history + (sample(BASE, mode="Invert Mode", voltage=26.0, output=1000.0, discharge=0.0),),
        )
        self.assertNotIn(OutputPolicyReason.FAST_VOLTAGE_SAG, recovered_return.reason_codes)
        self.assertNotIn(OutputPolicyReason.COOLDOWN, recovered_return.reason_codes)

    def test_long_session_telemetry_gap_does_not_connect_voltage_samples(self):
        history = [sample(BASE - timedelta(minutes=m), mode="Line Mode", discharge=0.0)
                   for m in range(602, 200, -2)]
        history.extend(sample(BASE - timedelta(minutes=m), mode="Invert Mode", voltage=26.0, output=1000.0, discharge=0.0)
                       for m in range(198, 100, -2))
        history.extend((sample(BASE - timedelta(minutes=92), mode="Invert Mode", voltage=26.0, output=1000.0, discharge=0.0),
                        sample(BASE - timedelta(minutes=82), mode="Invert Mode", voltage=24.4, output=1000.0, discharge=0.0)))
        for m in range(80, 0, -2):
            history.append(sample(BASE - timedelta(minutes=m), mode="Invert Mode", voltage=24.4, output=1000.0, discharge=0.0))
        result = evaluate(sample(mode="Invert Mode", voltage=24.4, output=1000.0, discharge=0.0), history=tuple(history))
        self.assertNotIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes)
        self.assertIn(OutputPolicyReason.INSUFFICIENT_HISTORY, result.reason_codes)

    def test_historical_pv_median_cannot_override_weak_current_reading(self):
        history = tuple(sample(BASE - timedelta(minutes=m), pv=(300.0 if m >= 16 else 800.0), discharge=0.0)
                        for m in range(602, 0, -2))
        result = evaluate(sample(pv=460.0, discharge=0.0), history=history)
        self.assertEqual(result.solar_opportunity.classification, SolarOpportunity.WEAK)
        self.assertIn(OutputPolicyReason.PV_DEFICIT, result.reason_codes)
        self.assertNotIn(OutputPolicyReason.PRODUCTIVE_SOLAR, result.reason_codes)
        self.assertNotEqual(result.decision, OutputDecision.CONSIDER)

    def test_n04_demand_exceeds_pv_advisory_alone_does_not_veto(self):
        result = evaluate(sample(output=1000.0, pv=700.0, discharge=0.0),
                          baseline(max_battery_discharge_a=90.0), established_line_history(pv=700.0))
        self.assertIn(OutputPolicyReason.DEMAND_EXCEEDS_PV, result.reason_codes)
        self.assertEqual(result.decision, OutputDecision.CONSIDER)

    def test_n05_improving_pv_below_productive_threshold_cannot_consider(self):
        history = tuple(
            sample(BASE - timedelta(minutes=m), pv=(300.0 if m > 14 else 300.0 + (14 - m) * 10.0), discharge=0.0)
            for m in range(602, 0, -2)
        )
        result = evaluate(sample(pv=460.0), history=history)
        self.assertNotEqual(result.solar_opportunity.classification, SolarOpportunity.STABLE)
        self.assertNotIn(OutputPolicyReason.PRODUCTIVE_SOLAR, result.reason_codes)
        self.assertNotEqual(result.decision, OutputDecision.CONSIDER)

    def test_n06_consecutive_unknown_modes_preserve_lockout(self):
        history = list(established_line_history(start_minutes=602, end_minutes=26))
        sequence = ((24, "Line Mode"), (22, "Fault"), (20, "Fault"), (18, "Invert Mode"), (16, "Fault"), (14, "Fault"), (12, "Line Mode"), (10, "Fault"), (8, "Fault"), (6, "Invert Mode"), (4, "Fault"), (2, "Fault"))
        history.extend(sample(BASE - timedelta(minutes=m), mode=mode) for m, mode in sequence)
        for minute in (12, 23):
            followup = tuple(sample(BASE + timedelta(minutes=m), mode="Line Mode", discharge=0.0)
                             for m in range(0, minute, 2))
            result = evaluate(sample(BASE + timedelta(minutes=minute), mode="Line Mode", discharge=0.0), history=tuple(history) + followup)
            self.assertEqual(result.decision, OutputDecision.BLOCK)
            self.assertIsNone(result.lockout_expires_at)
            self.assertIn(OutputPolicyReason.MODE_HISTORY_UNCERTAIN, result.reason_codes)
        followup = tuple(sample(BASE + timedelta(minutes=m), mode="Line Mode", discharge=0.0) for m in range(0, 24, 2))
        expired = evaluate(sample(BASE + timedelta(minutes=24), mode="Line Mode", discharge=0.0), history=tuple(history) + followup)
        self.assertIsNone(expired.lockout_expires_at)

    def test_shadow_only_contract_is_enforced(self):
        result = evaluate(sample(), history=established_line_history())
        self.assertTrue(result.shadow_only)
        self.assertFalse(result.command_executed)

    def test_one_sample_trial_is_conservative(self):
        history = list(established_line_history())
        history.append(sample(BASE - timedelta(minutes=1), mode="Invert Mode", voltage=24.0, output=500.0, discharge=0.0))
        result = evaluate(sample(mode="Line Mode", voltage=25.0, discharge=0.0), history=tuple(history))
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes)
        self.assertIn(OutputPolicyReason.COOLDOWN, result.reason_codes)
        self.assertEqual(result.lockout_expires_at, BASE + timedelta(minutes=59))

    def test_invert_exit_voltage_is_evaluated_before_hold(self):
        history = tuple(sample(BASE - timedelta(minutes=minutes), mode="Invert Mode", voltage=26.0, pv=800.0, discharge=0.0) for minutes in range(602, 1, -2))
        result = evaluate(sample(mode="Invert Mode", voltage=24.2, discharge=0.0), history=history)
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.EXIT_BATTERY_GUARD, result.reason_codes)
        self.assertNotIn(OutputPolicyReason.INVERT_MODE_OBSERVED, result.reason_codes)

    def test_repeated_short_mode_changes_trigger_timed_anti_flap_lockout(self):
        history = list(established_line_history(end_minutes=12))
        history = [point for point in history if point.observed_at <= BASE - timedelta(minutes=12)]
        modes = ("Invert Mode", "Line Mode", "Invert Mode", "Line Mode", "Invert Mode")
        history.extend(sample(BASE - timedelta(minutes=10 - i * 2), mode=mode, voltage=26.0) for i, mode in enumerate(modes))
        result = evaluate(sample(mode="Line Mode"), history=tuple(history))
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.EXCESSIVE_MODE_CHANGES, result.reason_codes)
        self.assertIn(OutputPolicyReason.CHURN_LOCKOUT, result.reason_codes)
        self.assertIsNotNone(result.lockout_expires_at)

    def test_unknown_modes_do_not_hide_known_flapping(self):
        history = list(established_line_history(end_minutes=20))
        history.extend((
            sample(BASE - timedelta(minutes=18), mode="Invert Mode"),
            sample(BASE - timedelta(minutes=16), mode="Unknown"),
            sample(BASE - timedelta(minutes=14), mode="Line Mode"),
            sample(BASE - timedelta(minutes=12), mode="Unknown"),
            sample(BASE - timedelta(minutes=10), mode="Invert Mode"),
            sample(BASE - timedelta(minutes=8), mode="Unknown"),
            sample(BASE - timedelta(minutes=6), mode="Line Mode"),
            sample(BASE - timedelta(minutes=4), mode="Unknown"),
            sample(BASE - timedelta(minutes=2), mode="Invert Mode"),
        ))
        result = evaluate(sample(), history=tuple(history))
        self.assertNotIn(OutputPolicyReason.EXCESSIVE_MODE_CHANGES, result.reason_codes)
        self.assertIn(OutputPolicyReason.MODE_HISTORY_UNCERTAIN, result.reason_codes)

    def test_voltage_sag_uses_recent_comparable_window_and_settling(self):
        history = list(established_line_history(end_minutes=22))
        history.extend((
            sample(BASE - timedelta(minutes=20), mode="Invert Mode", voltage=26.0, output=1000.0, discharge=0.0),
            sample(BASE - timedelta(minutes=15), mode="Invert Mode", voltage=25.9, output=1000.0, discharge=0.0),
            sample(BASE - timedelta(minutes=10), mode="Invert Mode", voltage=25.1, output=1000.0, discharge=0.0),
            sample(BASE - timedelta(minutes=5), mode="Invert Mode", voltage=24.6, output=1000.0, discharge=0.0),
        ))
        result = evaluate(sample(mode="Invert Mode", voltage=24.5, output=1000.0, discharge=0.0), history=tuple(history))
        self.assertIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes)

    def test_rebound_after_invert_to_line_is_not_recharge(self):
        history = tuple(sample(BASE - timedelta(minutes=minutes), mode="Invert Mode", voltage=25.0, pv=800.0, discharge=0.0) for minutes in range(602, 1, -2))
        result = evaluate(sample(mode="Line Mode", voltage=25.2, discharge=0.0), history=history)
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.BATTERY_REBOUND_NOT_RECHARGE, result.reason_codes)
        self.assertNotIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes)
        self.assertNotIn(OutputPolicyReason.COOLDOWN, result.reason_codes)

    def test_stale_future_and_exact_age_are_conservative(self):
        old = sample(BASE - timedelta(days=400))
        old_result = evaluate_at(old, BASE, history=(sample(BASE - timedelta(days=400, minutes=2)),))
        self.assertEqual(old_result.data_quality, TelemetryQuality.STALE)
        self.assertNotEqual(old_result.decision, OutputDecision.CONSIDER)

        future = sample(BASE + timedelta(seconds=1))
        future_result = evaluate_at(future, BASE, history=(), config=baseline())
        self.assertEqual(future_result.data_quality, TelemetryQuality.UNKNOWN)
        self.assertIn(OutputPolicyReason.FUTURE_TELEMETRY, future_result.reason_codes)

        exact = sample(BASE)
        exact_result = evaluate_at(exact, BASE + timedelta(seconds=180), history=established_line_history(), config=baseline())
        self.assertEqual(exact_result.data_quality, TelemetryQuality.FRESH)
        self.assertEqual(exact_result.data_age_seconds, 180.0)

        future_history = (sample(BASE + timedelta(minutes=1), discharge=0.0),)
        malformed = evaluate(sample(discharge=0.0), history=established_line_history() + future_history)
        self.assertEqual(malformed.data_quality, TelemetryQuality.UNKNOWN)
        self.assertNotEqual(malformed.decision, OutputDecision.CONSIDER)

    def test_forecast_validity_and_default_age_are_checked(self):
        stale_forecast = forecast(BASE, valid_at=BASE - timedelta(minutes=1))
        result = evaluate(sample(forecast_observation=stale_forecast), history=established_line_history())
        self.assertEqual(result.solar_opportunity.forecast_quality, ForecastQuality.STALE)
        self.assertIn(OutputPolicyReason.FORECAST_STALE, result.reason_codes)

        empty_forecast = evaluate(sample(forecast_observation=forecast(BASE, empty=True)), history=established_line_history())
        self.assertEqual(empty_forecast.solar_opportunity.forecast_quality, ForecastQuality.UNKNOWN)
        self.assertIn(OutputPolicyReason.FORECAST_UNAVAILABLE, empty_forecast.reason_codes)

    def test_invalid_current_and_power_values_never_consider(self):
        for kwargs in ({"output": -5000.0}, {"voltage": 0.0}):
            result = evaluate(sample(**kwargs), history=established_line_history())
            self.assertEqual(result.decision, OutputDecision.BLOCK)
            self.assertIn(OutputPolicyReason.INVALID_MEASUREMENT, result.reason_codes)
        for output, amps in ((0.0, 300.0), (500.0, 500.0), (500.0, 5000.0)):
            result = evaluate(sample(output=output, discharge=amps), baseline(max_battery_discharge_a=None), established_line_history())
            self.assertEqual(result.decision, OutputDecision.BLOCK)
            self.assertIn(OutputPolicyReason.UNKNOWN_BATTERY_CURRENT_SAFETY, result.reason_codes)
            self.assertNotIn(OutputPolicyReason.INVALID_MEASUREMENT, result.reason_codes)
        result = evaluate(sample(discharge=500.0), baseline(max_battery_discharge_a=400.0), established_line_history())
        self.assertIn(OutputPolicyReason.HIGH_BATTERY_DISCHARGE, result.reason_codes)
        self.assertNotIn(OutputPolicyReason.INVALID_MEASUREMENT, result.reason_codes)

    def test_missing_history_and_gaps_are_unknown_not_eligible(self):
        empty = evaluate(sample(), history=())
        self.assertEqual(empty.battery_eligibility.classification, BatteryEligibility.UNKNOWN)
        self.assertIn(OutputPolicyReason.INSUFFICIENT_HISTORY, empty.reason_codes)

        gap_history = (sample(BASE - timedelta(minutes=60)), sample(BASE - timedelta(minutes=10)))
        result = evaluate(sample(), history=gap_history)
        self.assertEqual(result.battery_eligibility.classification, BatteryEligibility.UNKNOWN)
        self.assertIn(OutputPolicyReason.INSUFFICIENT_HISTORY, result.reason_codes)

    def test_soc_is_diagnostic_only_and_result_is_immutable(self):
        history = established_line_history()
        low = evaluate(sample(soc=70.0), history=history)
        high = evaluate(sample(soc=100.0), history=history)
        self.assertEqual(low, high)
        with self.assertRaises(FrozenInstanceError):
            low.reason_codes += (OutputPolicyReason.COOLDOWN,)

    def test_unknown_baseline_never_fabricates_thresholds(self):
        result = evaluate(sample(), OutputPolicyBaseline(), established_line_history())
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.UNKNOWN_OUTPUT_CAPABILITY, result.reason_codes)
        self.assertIn(OutputPolicyReason.UNKNOWN_BATTERY_VOLTAGE_LIMIT, result.reason_codes)
        self.assertEqual(result.battery_eligibility.classification, BatteryEligibility.UNKNOWN)
        self.assertIsNone(result.baseline_max_battery_voltage_v)
        self.assertIsNone(result.effective_exit_battery_v)
        self.assertIsNone(result.effective_enter_battery_v)

    def test_voltage_above_operator_approved_limit_is_not_invalidated_or_authorized(self):
        result = evaluate(sample(voltage=31.0, discharge=0.0), history=established_line_history())
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.BATTERY_VOLTAGE_LIMIT_EXCEEDED, result.reason_codes)
        self.assertNotIn(OutputPolicyReason.INVALID_MEASUREMENT, result.reason_codes)
        self.assertEqual(result.battery_eligibility.classification, BatteryEligibility.INELIGIBLE)
        self.assertEqual(result.baseline_max_battery_voltage_v, 30.0)

        history = list(established_line_history())
        history[-10] = sample(history[-10].observed_at, voltage=31.0, discharge=0.0)
        historical = evaluate(sample(voltage=26.0, discharge=0.0), history=tuple(history))
        self.assertEqual(historical.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.BATTERY_VOLTAGE_LIMIT_EXCEEDED, historical.reason_codes)

    def test_evaluation_time_is_mandatory_and_timestamps_are_aware(self):
        with self.assertRaises(TypeError):
            evaluate_output_policy(sample(), baseline(), ())
        with self.assertRaises(ValueError):
            evaluate_output_policy(sample(), baseline(), (), evaluated_at=datetime(2026, 11, 2, 10, 0))
        with self.assertRaises(ValueError):
            sample(at=datetime(2026, 11, 2, 10, 0))


if __name__ == "__main__":
    unittest.main()
