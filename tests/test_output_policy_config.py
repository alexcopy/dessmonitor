import copy
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from app.control.adaptive_output_policy import (
    ForecastObservation, OutputDecision, OutputPolicyBaseline, OutputPolicyReason,
    OutputTelemetry, evaluate_output_policy,
)
from app.control.output_policy_config import (
    OutputPolicyConfigError, UnconfiguredOutputPolicy, load_output_policy, parse_output_policy,
)


def document():
    return {
        "devices": [{"min_volt": 21, "max_volt": 29}],
        "charge_policy": {"enabled": True},
        "output_source_policy": {
            "version": 1, "mode": "observe", "allow_output_writes": False,
            "baseline": {"exit_battery_v": None, "enter_battery_v": None, "min_hysteresis_v": None},
            "solar_opportunity": {"timezone": None, "sunrise_offset_min": 30, "sunset_reserve_lead_min": 120, "forecast_max_age_min": 180, "pv_stability_window_min": 10},
            "demand_and_sag": {"use_measured_output_w": True, "use_measured_battery_current": True, "voltage_trend_window_min": 10, "post_transition_settle_min": 3, "min_eligible_runtime_min": 45, "unknown_risk_blocks_battery_return": True},
            "hysteresis_adaptation": {"enabled_in_shadow": True, "max_delta_exit_v": None, "max_delta_enter_v": None, "expiry_min": 120, "midday_reassessment": True, "restore_before_sunset": True},
            "anti_flap": {"stable_condition_min": 10, "min_mains_dwell_min": 45, "min_battery_dwell_min": 30, "failed_trial_window_min": 10, "failed_trial_lockout_min": 60, "max_optional_output_commands_per_hour": 2},
            "safety": {"honor_bms_and_inverter_protection": True, "no_optimistic_action_on_stale_data": True, "respect_manual_override": True, "leave_charge_policy_independent": True},
            "audit": {"decision_log_enabled": True, "source_transition_log_enabled": True, "command_observation_correlation": True},
        },
    }


class OutputPolicyConfigTests(unittest.TestCase):
    def test_valid_schema_retains_unmapped_values_and_projects_baseline(self):
        raw = document()
        original = copy.deepcopy(raw)
        config = parse_output_policy(raw)
        baseline = config.to_baseline()
        self.assertEqual(raw, original)
        self.assertEqual(config.solar_opportunity.sunrise_offset_min, 30)
        self.assertTrue(config.hysteresis_adaptation.midday_reassessment)
        self.assertEqual(config.anti_flap.max_optional_output_commands_per_hour, 2)
        self.assertEqual(baseline.forecast_max_age_seconds, 10800)
        self.assertEqual(baseline.min_mains_dwell_seconds, 2700)
        self.assertIsNone(baseline.exit_battery_v)
        self.assertIsNone(baseline.max_battery_voltage_v)
        self.assertIsNone(baseline.max_battery_discharge_a)
        self.assertIsNone(baseline.max_battery_charge_a)
        self.assertIsInstance(baseline, OutputPolicyBaseline)

    def test_missing_section_and_missing_hardware_values(self):
        self.assertEqual(parse_output_policy({}), UnconfiguredOutputPolicy())
        raw = document()
        raw["output_source_policy"]["baseline"] = {}
        self.assertIsNone(parse_output_policy(raw).to_baseline().enter_battery_v)

    def test_invalid_fields_report_paths(self):
        cases = (
            (("version",), 2, "version"), (("version",), True, "version"),
            (("mode",), "active", "mode"), (("allow_output_writes",), True, "allow_output_writes"),
            (("solar_opportunity", "timezone"), "Not//AZone", "timezone"),
            (("solar_opportunity", "forecast_max_age_min"), float("nan"), "forecast_max_age_min"),
            (("solar_opportunity", "forecast_max_age_min"), float("inf"), "forecast_max_age_min"),
            (("solar_opportunity", "forecast_max_age_min"), 10**400, "forecast_max_age_min"),
            (("solar_opportunity", "pv_stability_window_min"), -1, "pv_stability_window_min"),
            (("anti_flap", "min_mains_dwell_min"), "45", "min_mains_dwell_min"),
            (("safety", "respect_manual_override"), False, "respect_manual_override"),
            (("baseline", "unknown_limit"), 5, "unknown_limit"),
            (("baseline", "max_battery_discharge_a"), -1, "max_battery_discharge_a"),
        )
        for keys, value, expected in cases:
            raw = document()
            target = raw["output_source_policy"]
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = value
            with self.subTest(keys=keys), self.assertRaisesRegex(OutputPolicyConfigError, expected):
                parse_output_policy(raw)

    def test_voltage_order_and_hysteresis(self):
        for values in ((25, 24, 0.5), (24, 25, 2)):
            raw = document()
            raw["output_source_policy"]["baseline"] = dict(zip(("exit_battery_v", "enter_battery_v", "min_hysteresis_v"), values))
            with self.assertRaises(OutputPolicyConfigError):
                parse_output_policy(raw)

    def test_review_duplicate_policy_keys_are_rejected(self):
        raw = yaml.safe_dump(document(), sort_keys=False)
        for duplicate in ("max_battery_discharge_a: 5\n    max_battery_discharge_a: 75", "max_battery_discharge_a: null\n    max_battery_discharge_a: 75"):
            text = raw.replace("    exit_battery_v: null", "    " + duplicate + "\n    exit_battery_v: null")
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.yaml"
                path.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(OutputPolicyConfigError, "baseline.*max_battery_discharge_a"):
                    load_output_policy(path)

    def test_review_direct_constructors_reject_unsafe_and_mutable(self):
        config = parse_output_policy(document())
        for changes in ({"mode": "active"}, {"allow_output_writes": True}, {"baseline": []}):
            with self.subTest(changes=changes), self.assertRaises((TypeError, ValueError)):
                replace(config, **changes)
        with self.assertRaises((TypeError, ValueError)):
            replace(config.baseline, max_battery_charge_a=[])

    def test_review_partial_voltage_conflicts_rejected(self):
        for upper in (24.0, 25.0):
            raw = document()
            raw["output_source_policy"]["baseline"].update(exit_battery_v=25.0, max_battery_voltage_v=upper)
            with self.subTest(upper=upper), self.assertRaisesRegex(OutputPolicyConfigError, "max_battery_voltage_v"):
                parse_output_policy(raw)
        for values in ({"exit_battery_v": 25, "max_battery_voltage_v": 26, "min_hysteresis_v": 2},
                       {"enter_battery_v": 25, "min_hysteresis_v": 25}):
            raw = document()
            raw["output_source_policy"]["baseline"].update(values)
            with self.assertRaisesRegex(OutputPolicyConfigError, "min_hysteresis_v"):
                parse_output_policy(raw)

    def test_review_extreme_duration_rejected(self):
        raw = document()
        raw["output_source_policy"]["solar_opportunity"]["pv_stability_window_min"] = timedelta.max.total_seconds() / 60
        with self.assertRaisesRegex(OutputPolicyConfigError, "pv_stability_window_min"):
            parse_output_policy(raw)

    def test_review_timezone_parser_is_pure_and_unverified(self):
        raw = document()
        raw["output_source_policy"]["solar_opportunity"]["timezone"] = "Europe/London"
        with patch("app.control.output_policy_config.ZoneInfo", side_effect=AssertionError("host timezone access")):
            config = parse_output_policy(raw)
        self.assertEqual(config.solar_opportunity.timezone, "Europe/London")
        self.assertFalse(config.solar_opportunity.timezone_verified)

    def test_review_root_duplicates_aliases_and_merges(self):
        base = yaml.safe_dump(document(), sort_keys=False)
        samples = (
            base + "\noutput_source_policy: null\n",
            base.replace("    exit_battery_v: null", "    <<: {max_battery_discharge_a: 5}\n    max_battery_discharge_a: 75\n    exit_battery_v: null"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            for text in samples:
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(OutputPolicyConfigError):
                    load_output_policy(path)
            # A non-merge alias with no repeated keys is safe to decode.
            path.write_text(base.replace("    exit_battery_v: null", "    exit_battery_v: &unknown null").replace("    enter_battery_v: null", "    enter_battery_v: *unknown"), encoding="utf-8")
            self.assertIsNone(load_output_policy(path).baseline.enter_battery_v)

    def test_review_timezone_resolution_only_at_file_boundary(self):
        raw = document()
        raw["output_source_policy"]["solar_opportunity"]["timezone"] = "Not/AZone"
        self.assertFalse(parse_output_policy(raw).solar_opportunity.timezone_verified)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump(raw), encoding="utf-8")
            with self.assertRaisesRegex(OutputPolicyConfigError, "timezone"):
                load_output_policy(path)

    def test_review_duration_boundaries_and_projection(self):
        raw = document()
        raw["output_source_policy"]["solar_opportunity"]["pv_stability_window_min"] = 10080
        baseline = parse_output_policy(raw).to_baseline()
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        result = evaluate_output_policy(OutputTelemetry(observed_at=now), baseline, (), evaluated_at=now)
        self.assertTrue(result.shadow_only)
        raw["output_source_policy"]["solar_opportunity"]["pv_stability_window_min"] = 10081
        with self.assertRaisesRegex(OutputPolicyConfigError, "pv_stability_window_min"):
            parse_output_policy(raw)

    def test_review_all_nested_constructors_validate(self):
        config = parse_output_policy(document())
        changes = (("solar_opportunity", {"timezone": []}),
                   ("demand_and_sag", {"use_measured_output_w": False}),
                   ("hysteresis_adaptation", {"expiry_min": []}),
                   ("anti_flap", {"min_mains_dwell_min": []}),
                   ("safety", {"respect_manual_override": False}),
                   ("audit", {"decision_log_enabled": []}))
        for name, kwargs in changes:
            with self.subTest(name=name), self.assertRaises((TypeError, ValueError)):
                replace(getattr(config, name), **kwargs)
        with self.assertRaises((TypeError, ValueError)):
            UnconfiguredOutputPolicy(configured=True)
        original = document()
        result = parse_output_policy(original)
        original["output_source_policy"]["baseline"]["exit_battery_v"] = 24
        self.assertIsNone(result.baseline.exit_battery_v)

    def test_read_only_reload_and_deep_immutability(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.yaml"
            path.write_text(yaml.safe_dump(document()), encoding="utf-8")
            before = path.read_bytes()
            with patch("socket.socket", side_effect=AssertionError("network")), patch("subprocess.Popen", side_effect=AssertionError("process")):
                first = load_output_policy(path)
                second = load_output_policy(path)
            self.assertEqual(first, second)
            self.assertEqual(first.to_baseline(), second.to_baseline())
            self.assertEqual(before, path.read_bytes())
            with self.assertRaises(FrozenInstanceError):
                first.solar_opportunity.timezone = "UTC"

    def test_shadow_evaluator_accepts_projection(self):
        baseline = parse_output_policy(document()).to_baseline()
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = evaluate_output_policy(OutputTelemetry(observed_at=now), baseline, (), evaluated_at=now)
        self.assertTrue(result.shadow_only)
        self.assertFalse(result.command_executed)
        self.assertIsNone(baseline.enter_battery_v)

    def test_optional_limits_missing_null_and_partial(self):
        keys = ("max_battery_voltage_v", "max_battery_discharge_a", "max_battery_charge_a")
        for supplied in ({}, dict.fromkeys(keys), {keys[1]: 80.0}):
            raw = document()
            raw["output_source_policy"]["baseline"].update(supplied)
            before = copy.deepcopy(raw)
            config = parse_output_policy(raw)
            projected = config.to_baseline()
            for key in keys:
                self.assertEqual(getattr(config.baseline, key), supplied.get(key))
                self.assertEqual(getattr(projected, key), supplied.get(key))
            self.assertEqual(raw, before)
            self.assertEqual(projected, parse_output_policy(raw).to_baseline())
            with self.assertRaises(FrozenInstanceError):
                config.baseline.max_battery_charge_a = 1.0

    def test_limits_reject_invalid_values_and_unknown_keys(self):
        keys = ("max_battery_voltage_v", "max_battery_discharge_a", "max_battery_charge_a")
        for key in keys:
            for invalid in (0, -1, True, False, "10", float("nan"), float("inf"), float("-inf")):
                raw = document()
                raw["output_source_policy"]["baseline"][key] = invalid
                with self.subTest(key=key, invalid=invalid), self.assertRaisesRegex(OutputPolicyConfigError, key):
                    parse_output_policy(raw)
        raw = document()
        raw["output_source_policy"]["baseline"]["unapproved_limit"] = 1
        with self.assertRaisesRegex(OutputPolicyConfigError, "unapproved_limit"):
            parse_output_policy(raw)
        raw = document()
        raw["output_source_policy"]["baseline"].update(enter_battery_v=26, max_battery_voltage_v=25)
        with self.assertRaisesRegex(OutputPolicyConfigError, "max_battery_voltage_v"):
            parse_output_policy(raw)

    def test_illustrative_approved_limits_reload_and_shadow_eligibility(self):
        # Illustrative values only; these are not equipment ratings or defaults.
        raw = document()
        raw["output_source_policy"]["baseline"].update(
            exit_battery_v=24.5, enter_battery_v=25.5, min_hysteresis_v=0.5,
            max_battery_voltage_v=30.0, max_battery_discharge_a=90.0, max_battery_charge_a=60.0,
        )
        before = copy.deepcopy(raw)
        config = parse_output_policy(raw)
        baseline = config.to_baseline()
        self.assertEqual((baseline.max_battery_voltage_v, baseline.max_battery_discharge_a, baseline.max_battery_charge_a), (30.0, 90.0, 60.0))
        self.assertEqual(raw, before)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.yaml"
            path.write_text(yaml.safe_dump(raw), encoding="utf-8")
            contents = path.read_bytes()
            with patch("socket.socket", side_effect=AssertionError("network")), patch("subprocess.Popen", side_effect=AssertionError("process")):
                self.assertEqual(load_output_policy(path).to_baseline(), baseline)
                self.assertEqual(load_output_policy(path).to_baseline(), baseline)
            self.assertEqual(contents, path.read_bytes())

        now = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)
        sunrise = now.replace(hour=6)
        sunset = now.replace(hour=20)
        forecast = ForecastObservation(observed_at=now, valid_at=now + timedelta(hours=1), clouds_pct=20)
        def point(at):
            return OutputTelemetry(observed_at=at, working_mode="Line Mode", output_power_w=500, pv_power_w=800,
                                   battery_voltage=26, battery_current_chg=0, battery_current_dis=0,
                                   sunrise=sunrise, sunset=sunset, forecast=forecast)
        history = tuple(point(now - timedelta(minutes=m)) for m in range(602, 0, -2))
        result = evaluate_output_policy(point(now), baseline, history, evaluated_at=now)
        self.assertEqual(result.decision, OutputDecision.CONSIDER)
        self.assertTrue(result.shadow_only)
        self.assertFalse(result.command_executed)
        raw["output_source_policy"]["baseline"]["max_battery_discharge_a"] = None
        unknown = evaluate_output_policy(point(now), parse_output_policy(raw).to_baseline(), history, evaluated_at=now)
        self.assertEqual(unknown.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.UNKNOWN_BATTERY_CURRENT_SAFETY, unknown.reason_codes)


if __name__ == "__main__":
    unittest.main()
