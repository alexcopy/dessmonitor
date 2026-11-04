import asyncio
import importlib
import sys
import time
import unittest
from collections import deque
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.control.adaptive_output_policy import OutputDecision, OutputPolicyReason, TelemetryQuality
from app.control.shadow_output_monitor import ShadowOutputMonitor


def data(timestamp, **overrides):
    # The fixture explicitly represents a source with verified measurement-time provenance.
    values = dict(timestamp=timestamp, device_observed_at=timestamp, working_state="Line Mode", output_power=1200.0,
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
            observation.device_observed_at = stamp.isoformat()
            result = self.shadow.observe(observation, evaluated_at=stamp)
        self.assertEqual(len(self.shadow.history), 2)
        self.assertLess(self.shadow.history[0].observed_at, self.shadow.history[1].observed_at)
        self.assertFalse(result.command_executed)
        observation.write_output_priority.assert_not_called()
        observation.set_charger_source.assert_not_called()
        observation.switch_relay.assert_not_called()

    def test_legacy_timestamp_is_not_promoted_to_device_time(self):
        sample = data(self.now.isoformat(), device_observed_at=None)
        result = self.shadow.observe(sample, received_at=self.now, evaluated_at=self.now)
        self.assertEqual(result.data_quality, TelemetryQuality.UNKNOWN)
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertIsNone(result.observed_at)
        self.assertEqual(len(self.shadow.history), 0)

    def test_receipt_and_observation_remain_distinct(self):
        observed = self.now - timedelta(minutes=5)
        result = self.shadow.observe(data(observed.isoformat()), received_at=self.now, evaluated_at=self.now)
        self.assertEqual(result.data_quality, TelemetryQuality.STALE)
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertEqual(self.shadow.history[-1].observed_at, observed)
        self.assertEqual(self.shadow.history[-1].received_at, self.now)

    def test_weather_times_require_fresh_source_and_do_not_imply_forecast(self):
        sunrise = self.now.replace(hour=6, minute=17)
        sunset = self.now.replace(hour=19, minute=43)
        sun_times = (self.now - timedelta(minutes=2), sunrise, sunset)
        result = self.shadow.observe(data(self.now.isoformat()), received_at=self.now,
                                     evaluated_at=self.now, sun_times=sun_times)
        self.assertEqual(self.shadow.history[-1].sunrise, sunrise)
        self.assertEqual(self.shadow.history[-1].sunset, sunset)
        self.assertIn(OutputPolicyReason.FORECAST_UNAVAILABLE, result.reason_codes)
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        later = self.now + timedelta(minutes=1)
        stale = (self.now - timedelta(hours=4), sunrise, sunset)
        self.shadow.observe(data(later.isoformat()), received_at=later, evaluated_at=later, sun_times=stale)
        self.assertIsNone(self.shadow.history[-1].sunrise)

    def test_changed_reported_mode_is_logged_even_with_unknown_observation_time(self):
        self.shadow.observe(data(None, working_state="Line Mode"), evaluated_at=self.now)
        self.shadow.observe(data(None, working_state="Invert Mode"), evaluated_at=self.now)
        self.assertEqual(self.logger.info.call_count, 2)
        self.assertIn("reported_mode=%s", self.logger.info.call_args.args[0])
        self.assertEqual(len(self.shadow.history), 0)

    def test_weather_source_preserves_exact_sun_times_and_clears_missing_data(self):
        aiohttp_stub = ModuleType("aiohttp")
        with patch.dict(sys.modules, {"aiohttp": aiohttp_stub}):
            sys.modules.pop("app.weather.openweather_service", None)
            try:
                module = importlib.import_module("app.weather.openweather_service")
                state = {}
                service = module.OpenWeatherService.__new__(module.OpenWeatherService)
                sunrise = self.now.replace(hour=6, minute=17)
                sunset = self.now.replace(hour=19, minute=43)
                with patch.object(module, "shared_state", state):
                    service._update_shared_state({"current": {"dt": int(self.now.timestamp()),
                        "sunrise": int(sunrise.timestamp()), "sunset": int(sunset.timestamp())}})
                    self.assertEqual(state["output_shadow_sun_times"], (self.now, sunrise, sunset))
                    self.assertEqual(state["sunrise_hour"], 6)
                    service._update_shared_state({"current": {"dt": int(self.now.timestamp())}})
                    self.assertIsNone(state["output_shadow_sun_times"])
            finally:
                sys.modules.pop("app.weather.openweather_service", None)

    def test_out_of_domain_timestamp_is_unknown_without_history_mutation(self):
        self.shadow.observe(data(self.now.isoformat()), evaluated_at=self.now)
        original = tuple(self.shadow.history)
        for stamp in ("0001-01-01T00:00:00+00:00", "9999-12-31T23:59:59+00:00",
                      "0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-12:00"):
            result = self.shadow.observe(data(stamp), evaluated_at=self.now)
            self.assertEqual(result.data_quality, TelemetryQuality.UNKNOWN)
            self.assertEqual(result.decision, OutputDecision.BLOCK)
            self.assertEqual(tuple(self.shadow.history), original)
        with self.assertRaises(ValueError):
            self.shadow.observe(data(None), evaluated_at=datetime(1, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(tuple(self.shadow.history), original)

    def test_future_and_out_of_order_samples_cannot_prune_history(self):
        self.shadow.observe(data(self.now.isoformat()), received_at=self.now, evaluated_at=self.now)
        original = tuple(self.shadow.history)
        future = self.shadow.observe(data((self.now + timedelta(days=30)).isoformat()),
                                     received_at=self.now, evaluated_at=self.now)
        self.assertEqual(future.data_quality, TelemetryQuality.UNKNOWN)
        self.assertEqual(tuple(self.shadow.history), original)
        self.shadow.observe(data((self.now - timedelta(days=30)).isoformat()), evaluated_at=self.now)
        self.assertEqual(tuple(self.shadow.history), original)

    def test_inconsistent_receipt_does_not_mutate_history(self):
        self.shadow.observe(data(self.now.isoformat()), evaluated_at=self.now)
        original = tuple(self.shadow.history)
        result = self.shadow.observe(data((self.now + timedelta(minutes=1)).isoformat()),
                                     received_at=self.now, evaluated_at=self.now + timedelta(minutes=1))
        self.assertEqual(result.data_quality, TelemetryQuality.UNKNOWN)
        self.assertEqual(tuple(self.shadow.history), original)

    def test_supported_date_boundaries_do_not_overflow(self):
        for stamp in (datetime(2, 1, 1, tzinfo=timezone.utc),
                      datetime(9998, 12, 31, 23, 59, tzinfo=timezone.utc)):
            monitor = ShadowOutputMonitor(logger=Mock())
            result = monitor.observe(data(stamp.isoformat()), received_at=stamp, evaluated_at=stamp)
            self.assertEqual(result.decision, OutputDecision.BLOCK)
            self.assertEqual(monitor.history[-1].observed_at, stamp)

    def test_failed_trial_remains_locked_out_with_verified_sample_times(self):
        from app.control.adaptive_output_policy import OutputPolicyBaseline

        monitor = ShadowOutputMonitor(
            baseline=OutputPolicyBaseline(exit_battery_v=24.5, enter_battery_v=25.5,
                                          min_hysteresis_v=0.5, max_battery_voltage_v=30.0,
                                          max_battery_discharge_a=90.0), logger=Mock(),
        )
        for minute in range(482, 5, -2):
            stamp = self.now - timedelta(minutes=minute)
            monitor.observe(data(stamp.isoformat(), battery_voltage=26.0, output_power=500.0),
                            received_at=stamp, evaluated_at=stamp)
        for minute, mode, voltage in ((4, "Line Mode", 26.0), (3, "Invert Mode", 25.5),
                                      (2, "Invert Mode", 25.0), (1, "Invert Mode", 24.4)):
            stamp = self.now - timedelta(minutes=minute)
            monitor.observe(data(stamp.isoformat(), working_state=mode, battery_voltage=voltage, output_power=500.0),
                            received_at=stamp, evaluated_at=stamp)
        result = monitor.observe(data(self.now.isoformat(), battery_voltage=25.8, output_power=500.0),
                                 received_at=self.now, evaluated_at=self.now)
        self.assertEqual(result.decision, OutputDecision.BLOCK)
        self.assertIn(OutputPolicyReason.FAST_VOLTAGE_SAG, result.reason_codes)
        self.assertIn(OutputPolicyReason.COOLDOWN, result.reason_codes)
        self.assertEqual(result.lockout_expires_at, self.now + timedelta(minutes=59))

    def test_dess_legacy_timestamps_are_preserved_without_asserting_provenance(self):
        logger_stub = ModuleType("app.logger")
        logger_stub.loki_handler = Mock()
        with patch.dict(sys.modules, {"app.logger": logger_stub}):
            sys.modules.pop("app.api", None)
            try:
                module = importlib.import_module("app.api")
                api = module.DessAPI.__new__(module.DessAPI)
                api.logger = Mock()
                api.pn = api.dev_code = api.dev_addr = api.sn = "fixture"
                primary = api._parse_device_data({"dat": [{"title": "Timestamp", "val": "2026-11-02T10:00:00Z"}]})
                self.assertEqual(primary.timestamp, "2026-11-02T10:00:00Z")
                self.assertIsNone(primary.device_observed_at)
                api._do_api_request = Mock(return_value={"dat": {"gts": "1700000000000", "pars": {}}})
                fallback = api.fetch_device_data_fallback()
                self.assertEqual(fallback.timestamp, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(1700000000)))
                self.assertIsNone(fallback.device_observed_at)
            finally:
                sys.modules.pop("app.api", None)

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
                timing = shadow.observe.call_args.kwargs
                self.assertIsNotNone(timing["received_at"].utcoffset())
                self.assertLessEqual(timing["received_at"], timing["evaluated_at"])
                monitor.imp.exception.assert_called_once()
                sys.modules.pop("service.inverter_monitor", None)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
