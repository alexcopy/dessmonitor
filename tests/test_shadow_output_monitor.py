import asyncio
import importlib
import sys
import unittest
from collections import deque
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.control.adaptive_output_policy import OutputDecision, OutputPolicyReason, TelemetryQuality
from app.control.shadow_output_monitor import ShadowOutputMonitor


def data(timestamp, **overrides):
    values = dict(timestamp=timestamp, working_state="Line Mode", output_power=1200.0,
                  pv_total_power=700.0, battery_voltage=26.0,
                  battery_charging_current=0.0, battery_discharging_current=0.0)
    values.update(overrides)
    return SimpleNamespace(**values)


class ShadowOutputMonitorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)
        self.logger = Mock()
        self.shadow = ShadowOutputMonitor(logger=self.logger)

    def test_missing_or_naive_timestamp_is_unknown_and_not_history(self):
        for stamp in (None, "2026-11-02 10:00:00"):
            result = self.shadow.observe(data(stamp, output_power=None), evaluated_at=self.now)
            self.assertEqual(result.decision, OutputDecision.BLOCK)
            self.assertEqual(result.data_quality, TelemetryQuality.UNKNOWN)
            self.assertEqual(len(self.shadow.history), 0)

    def test_warmup_history_and_actual_measured_values(self):
        first = self.shadow.observe(data((self.now - timedelta(minutes=2)).isoformat()), evaluated_at=self.now)
        second = self.shadow.observe(data(self.now.isoformat()), evaluated_at=self.now)
        self.assertEqual(first.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.INSUFFICIENT_HISTORY, second.reason_codes)
        self.assertEqual(len(self.shadow.history), 2)
        self.assertEqual(self.shadow.history[-1].output_power_w, 1200.0)
        self.assertEqual(self.shadow.history[-1].working_mode, "Line Mode")
        self.assertIsNone(self.shadow.history[-1].sunrise)
        self.assertEqual(self.logger.info.call_count, 1)
        self.assertFalse(second.command_executed)
        self.assertTrue(second.shadow_only)
        self.assertEqual(len(ShadowOutputMonitor(logger=Mock()).history), 0)

    def test_stale_and_out_of_order_are_not_promoted(self):
        stale = self.shadow.observe(data((self.now - timedelta(minutes=10)).isoformat()), evaluated_at=self.now)
        self.assertEqual(stale.data_quality, TelemetryQuality.STALE)
        self.shadow.observe(data(self.now.isoformat()), evaluated_at=self.now)
        before = tuple(self.shadow.history)
        earlier = self.shadow.observe(data((self.now - timedelta(minutes=1)).isoformat()), evaluated_at=self.now)
        self.assertNotEqual(earlier.decision, OutputDecision.CONSIDER)
        self.assertEqual(tuple(self.shadow.history), before)

    def test_missing_measurements_never_become_zero(self):
        self.shadow.observe(data(self.now.isoformat(), output_power=None, pv_total_power=None,
                                 battery_voltage=None, battery_charging_current=None,
                                 battery_discharging_current=None), evaluated_at=self.now)
        point = self.shadow.history[-1]
        self.assertIsNone(point.output_power_w)
        self.assertIsNone(point.pv_power_w)
        self.assertIsNone(point.battery_current_dis)

    def test_history_is_bounded_and_adapter_has_no_hardware_writes(self):
        self.assertEqual(self.shadow.history.maxlen, 12000)
        self.shadow.history = deque(maxlen=2)
        observation = data(self.now.isoformat())
        observation.write_output_priority = Mock()
        observation.set_charger_source = Mock()
        observation.switch_relay = Mock()
        for offset in (0, 1, 2):
            stamp = self.now + timedelta(minutes=offset)
            observation.timestamp = stamp.isoformat()
            result = self.shadow.observe(observation, evaluated_at=stamp)
        self.assertEqual(len(self.shadow.history), 2)
        self.assertLess(self.shadow.history[0].observed_at, self.shadow.history[1].observed_at)
        self.assertFalse(result.command_executed)
        observation.write_output_priority.assert_not_called()
        observation.set_charger_source.assert_not_called()
        observation.switch_relay.assert_not_called()

    def test_shadow_failure_does_not_stop_control_loop(self):
        async def run():
            api = Mock()
            api.fetch_device_data.return_value = data(self.now.isoformat())
            charge = Mock()
            charge.tick = AsyncMock()
            shadow = Mock()
            shadow.observe.side_effect = RuntimeError("shadow failure")
            stubs = {}
            for name, attrs in {
                "app.api": {"DessAPI": object, "DeviceData": object},
                "app.monitoring.inverter_logger": {"InverterLogger": Mock},
                "app.service.battery_charge_controller": {"charger_mode": lambda value: value},
                "app.utils.time_utils": {"smart_sleep": AsyncMock()},
                "shared_state.shared_state": {"shared_state": {}},
            }.items():
                module = ModuleType(name)
                module.__dict__.update(attrs)
                stubs[name] = module
            with patch.dict(sys.modules, stubs):
                sys.modules.pop("service.inverter_monitor", None)
                module = importlib.import_module("service.inverter_monitor")
                monitor = module.InverterMonitor(api, charge_controller=charge, shadow_monitor=shadow)
                monitor._process_business_metrics = Mock()
                monitor.imp = Mock()
                async def stop_after_poll(*args):
                    monitor.stop()
                with patch.object(module, "smart_sleep", side_effect=stop_after_poll):
                    await monitor.run()
                charge.tick.assert_awaited_once()
                shadow.observe.assert_called_once()
                monitor.imp.exception.assert_called_once()
                sys.modules.pop("service.inverter_monitor", None)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
