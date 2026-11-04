"""Offline examples: illustrative values are not approved hardware limits."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from app.control.adaptive_output_policy import (
    BatteryEligibility, OutputDecision, OutputPolicyBaseline, OutputPolicyReason, TelemetryQuality,
)
from app.control.shadow_output_monitor import ShadowOutputMonitor
from app.control.output_policy_config import parse_output_policy
from test_output_policy_config import document


def sample(observed_at, mode="Line Mode", voltage=25.9, discharge=0.0):
    return SimpleNamespace(
        timestamp=None, device_observed_at=observed_at,
        working_state=mode, output_power=1300.0, pv_total_power=300.0,
        battery_voltage=voltage, battery_charging_current=0.0,
        battery_discharging_current=discharge,
        ctrl_device=Mock(), set_charger_source=Mock(), switch_relay=Mock(),
    )


class AntiFlappingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
        self.logger = Mock()
        # These are synthetic test values, not equipment ratings.
        self.baseline = OutputPolicyBaseline(exit_battery_v=24.0, enter_battery_v=25.5,
                                             min_hysteresis_v=0.5, max_battery_voltage_v=31.0,
                                             max_battery_discharge_a=45.0)
        self.monitor = ShadowOutputMonitor(self.baseline, self.logger)

    def observe(self, instant, mode="Line Mode", voltage=25.9, discharge=0.0):
        point = sample(instant.isoformat(), mode, voltage, discharge)
        result = self.monitor.observe(point, received_at=instant, evaluated_at=instant)
        point.ctrl_device.assert_not_called()
        point.set_charger_source.assert_not_called()
        point.switch_relay.assert_not_called()
        self.assertFalse(result.command_executed)
        self.assertTrue(result.shadow_only)
        return result

    def test_repeated_short_trials_sag_rebound_and_escalating_cooldown(self):
        for minute in range(482, 0, -1):
            self.observe(self.now - timedelta(minutes=minute))
        self.observe(self.now)
        last_expiry = None
        last_return = 0
        for start, duration in ((1, 4), (10, 6), (23, 5)):
            for minute in range(last_return + 1, start):
                self.observe(self.now + timedelta(minutes=minute))
            for minute in range(duration):
                voltage = 25.9 - 0.5 * minute / (duration - 1)
                self.observe(self.now + timedelta(minutes=start + minute), "Invert Mode", voltage, 19.0)
            returned = self.now + timedelta(minutes=start + duration)
            result = self.observe(returned, "Line Mode", 25.9, 0.0)
            self.assertEqual(result.decision, OutputDecision.BLOCK)
            self.assertEqual(result.battery_eligibility.classification, BatteryEligibility.INELIGIBLE)
            self.assertIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes)
            self.assertIn(OutputPolicyReason.COOLDOWN, result.reason_codes)
            self.assertIn(OutputPolicyReason.BATTERY_REBOUND_NOT_RECHARGE, result.reason_codes)
            self.assertIn(OutputPolicyReason.DEMAND_EXCEEDS_PV, result.reason_codes)
            if last_expiry is not None:
                self.assertGreater(result.lockout_expires_at, last_expiry)
            last_expiry = result.lockout_expires_at
            last_return = start + duration

    def test_restart_missing_limits_and_missing_timestamp_stay_unknown(self):
        point = sample(self.now.isoformat())
        first = ShadowOutputMonitor(logger=Mock()).observe(point, received_at=self.now, evaluated_at=self.now)
        self.assertEqual(first.decision, OutputDecision.BLOCK)
        self.assertEqual(first.battery_eligibility.classification, BatteryEligibility.UNKNOWN)
        self.assertIn(OutputPolicyReason.UNKNOWN_BATTERY_CURRENT_SAFETY, first.reason_codes)
        self.assertIn(OutputPolicyReason.INSUFFICIENT_HISTORY, first.reason_codes)
        restarted = ShadowOutputMonitor(self.baseline, Mock())
        point.device_observed_at = None
        point.timestamp = self.now.isoformat()
        result = restarted.observe(point, received_at=self.now, evaluated_at=self.now)
        self.assertEqual(result.data_quality, TelemetryQuality.UNKNOWN)
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertEqual(len(restarted.history), 0)
        point.ctrl_device.assert_not_called()

    def test_observe_configuration_never_writes_when_capability_unknown(self):
        config = parse_output_policy(document())
        self.assertEqual(config.mode, "observe")
        self.assertFalse(config.allow_output_writes)
        monitor = ShadowOutputMonitor(config.to_baseline(), Mock())
        point = sample(None, "Invert Mode", 25.4, 19.0)
        result = monitor.observe(point, received_at=self.now, evaluated_at=self.now)
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.UNKNOWN_BATTERY_CURRENT_SAFETY, result.reason_codes)
        self.assertFalse(result.command_executed)
        point.ctrl_device.assert_not_called()
        point.set_charger_source.assert_not_called()
        point.switch_relay.assert_not_called()

    def test_receipt_only_mode_changes_are_diagnostics_not_telemetry(self):
        for minutes, mode in ((0, "Line Mode"), (1, "Invert Mode"), (5, "Line Mode"),
                              (11, "Invert Mode"), (16, "Line Mode")):
            at = self.now + timedelta(minutes=minutes)
            point = sample(None, mode, 25.4 if mode == "Invert Mode" else 25.9,
                           19.0 if mode == "Invert Mode" else 0.0)
            result = self.monitor.observe(point, received_at=at, evaluated_at=at)
            self.assertEqual(result.data_quality, TelemetryQuality.UNKNOWN)
            self.assertEqual(result.decision, OutputDecision.BLOCK)
            point.ctrl_device.assert_not_called()
        reports = [call.args for call in self.logger.info.call_args_list if call.args[0].startswith("[OUTPUT_SHADOW_REPORT]")]
        self.assertEqual([entry[4] for entry in reports], [None, 240.0, 360.0, 300.0])
        self.assertTrue(all("device_time=not_used" in entry[0] for entry in reports))
        self.assertEqual(len(self.monitor.history), 0)

    def test_unknown_mode_breaks_receipt_only_sequence(self):
        for minute, mode in ((0, "Line Mode"), (1, "Fault"), (2, "Invert Mode")):
            at = self.now + timedelta(minutes=minute)
            self.monitor.observe(sample(None, mode), received_at=at, evaluated_at=at)
        reports = [call for call in self.logger.info.call_args_list if call.args[0].startswith("[OUTPUT_SHADOW_REPORT]")]
        self.assertEqual(reports, [])


if __name__ == "__main__":
    unittest.main()
