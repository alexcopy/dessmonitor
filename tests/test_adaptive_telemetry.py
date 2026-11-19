"""Task 0079: adaptive telemetry recorder classification, provenance and bounds."""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.api import DeviceData
from app.control import adaptive_telemetry as at
from app.control.adaptive_telemetry import AdaptiveTelemetryRecorder

T0 = datetime(2026, 12, 10, 1, 0, tzinfo=timezone.utc)


def dd(**overrides):
    values = dict(timestamp="2026-12-10 09:00:00", working_state="Line Mode", battery_voltage=26.4,
                  battery_capacity=60.0, battery_charging_current=0.0, battery_discharging_current=0.0,
                  pv_total_power=0.0, output_power=400.0, ac_output_load=8.0, ac_input_voltage=230.0)
    values.update(overrides)
    return DeviceData(**values)


def night_sun(now):
    """Fresh, aware tuple: weather 10 min old, sunrise 07:00, sunset 16:00 (now = 01:00)."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (now - timedelta(minutes=10), day + timedelta(hours=7), day + timedelta(hours=16))


def recorder(**kwargs):
    rec = AdaptiveTelemetryRecorder(**kwargs)
    rec.attach_writer()
    return rec


class ClassificationTests(unittest.TestCase):
    def test_first_response_is_baseline_without_event(self):
        rec = recorder()
        rec.observe(dd(), received_at=T0)
        self.assertEqual((rec.responses, rec.repeats, rec.changed_payloads), (1, 0, 0))
        self.assertEqual(rec.pending(), ())

    def test_line_invert_line_produces_two_transition_events_before_checkpoint(self):
        rec = recorder()
        sequence = ["Line Mode", "Line Mode", "Invert Mode", "Invert Mode", "Line Mode"]
        for minute, mode in enumerate(sequence):
            rec.observe(dd(working_state=mode, timestamp="t%d" % minute),
                        received_at=T0 + timedelta(minutes=minute))
        events = [e for e in rec.pending() if e.event_kind == "mode_transition"]
        self.assertEqual([(e.from_mode, e.to_mode) for e in events],
                         [("Line Mode", "Invert Mode"), ("Invert Mode", "Line Mode")])
        self.assertEqual(rec.checkpoints, 0)
        self.assertEqual(events[0].receipt_at, T0 + timedelta(minutes=2))
        self.assertEqual(events[0].previous_receipt_at, T0 + timedelta(minutes=1))
        self.assertEqual(events[0].changed_fields[0], "working_mode")

    def test_identical_cached_responses_are_repeats_without_events_or_verification(self):
        rec = recorder()
        cached = dd()
        for i in range(50):
            rec.observe(cached, received_at=T0 + timedelta(minutes=i))
        self.assertEqual(rec.responses, 50)
        self.assertEqual(rec.repeats, 49)
        self.assertEqual(rec.changed_payloads, 0)
        self.assertEqual(rec.pending(), ())
        self.assertEqual(rec.verified_independent_observations, 0)
        self.assertEqual(rec.statistics(T0)["repeat_ratio"], 0.98)

    def test_two_significant_changes_between_checkpoints_both_queue(self):
        rec = recorder()
        rec.observe(dd(), received_at=T0)
        rec.observe(dd(timestamp="b", pv_total_power=420.0), received_at=T0 + timedelta(minutes=5))
        rec.observe(dd(timestamp="c", pv_total_power=420.0, battery_voltage=27.1),
                    received_at=T0 + timedelta(minutes=10))
        events = rec.pending()
        self.assertEqual([e.event_kind for e in events], ["significant_payload_change"] * 2)
        self.assertEqual(events[0].changed_fields, ("pv_power_w",))
        self.assertEqual(events[0].quantized_values, {"pv_power_w": 400.0})
        self.assertEqual(events[1].changed_fields, ("battery_voltage",))
        self.assertEqual(events[1].metrics["pv_power_w"], 420.0)
        self.assertEqual(events[1].metrics["battery_voltage"], 27.1)

    def test_values_inside_a_bin_do_not_persist_and_crossing_does(self):
        rec = recorder()
        rec.observe(dd(battery_voltage=26.41), received_at=T0)
        rec.observe(dd(battery_voltage=26.59, timestamp="b"), received_at=T0 + timedelta(minutes=1))
        self.assertEqual(rec.changed_payloads, 1)
        self.assertEqual(rec.pending(), ())  # same 0.2 V persistence bin
        rec.observe(dd(battery_voltage=26.61, timestamp="c"), received_at=T0 + timedelta(minutes=2))
        self.assertEqual([e.changed_fields for e in rec.pending()], [("battery_voltage",)])

    def test_mode_change_is_exact_and_never_filtered(self):
        rec = recorder()
        rec.observe(dd(), received_at=T0)
        rec.observe(dd(working_state="Invert Mode"), received_at=T0 + timedelta(minutes=1))
        self.assertEqual([e.event_kind for e in rec.pending()], ["mode_transition"])

    def test_other_mode_changes_are_not_called_line_invert_transitions(self):
        rec = recorder()
        rec.observe(dd(), received_at=T0)
        rec.observe(dd(working_state="Fault Mode"), received_at=T0 + timedelta(minutes=1))
        (event,) = rec.pending()
        self.assertEqual(event.event_kind, "significant_payload_change")
        self.assertEqual(event.changed_fields, ("working_mode",))

    def test_noise_filters_are_documented_persistence_constants(self):
        self.assertEqual(at.quantize("battery_voltage", 26.59), 26.4)
        self.assertIsNone(at.quantize("battery_voltage", float("nan")))
        self.assertIsNone(at.quantize("battery_voltage", True))
        self.assertIn("PERSISTENCE NOISE FILTERS ONLY", Path(at.__file__).read_text())


class ProvenanceTests(unittest.TestCase):
    def test_latest_fingerprint_is_read_only_and_matches_response_identity(self):
        rec = AdaptiveTelemetryRecorder()
        self.assertIsNone(rec.latest_fingerprint)
        first = dd()
        rec.observe(first, received_at=T0)
        fingerprint = rec.latest_fingerprint
        self.assertIsInstance(fingerprint, str)
        self.assertEqual(len(fingerprint), 16)
        rec.observe(dd(timestamp="changed"), received_at=T0 + timedelta(minutes=1))
        self.assertNotEqual(rec.latest_fingerprint, fingerprint)

    def test_changed_payload_with_unknown_device_time_is_observational_only(self):
        rec = recorder()
        rec.observe(dd(timestamp=None), received_at=T0)
        rec.observe(dd(timestamp=None, pv_total_power=900.0), received_at=T0 + timedelta(minutes=1))
        (event,) = rec.pending()
        meta = event.metadata(T0 + timedelta(minutes=1, seconds=2))
        self.assertEqual(meta["receipt_at"], (T0 + timedelta(minutes=1)).isoformat())
        self.assertEqual(meta["collected_at"], (T0 + timedelta(minutes=1, seconds=2)).isoformat())
        self.assertIsNone(meta["measurement_time"])
        self.assertEqual(meta["timestamp_quality"], "device_time_unverified")
        self.assertFalse(meta["verified_observation"])
        self.assertEqual(rec.verified_independent_observations, 0)

    def test_caller_supplied_device_observed_at_is_ignored_for_verification(self):
        rec = recorder()
        for i in range(5):
            stamp = (T0 + timedelta(minutes=i)).isoformat()
            rec.observe(dd(device_observed_at=stamp, timestamp=stamp, pv_total_power=100.0 * i),
                        received_at=T0 + timedelta(minutes=i))
        stats = rec.statistics(T0 + timedelta(minutes=5))
        self.assertEqual(stats["verified_independent_observations"], 0)
        self.assertEqual(stats["device_time_verified_observations"], 0)
        self.assertIsNone(stats["last_verified_measurement_at"])
        for event in rec.pending():
            self.assertIsNone(event.metadata(T0)["measurement_time"])

    def test_metadata_is_bounded_and_has_no_raw_payload(self):
        rec = recorder()
        rec.observe(dd(), received_at=T0)
        rec.observe(dd(working_state="Invert Mode", charger_priority="secret-raw"), received_at=T0 + timedelta(minutes=1))
        meta = rec.pending()[0].metadata(T0)
        self.assertEqual(set(meta), {"event_id", "event_kind", "changed_fields", "quantized_values", "receipt_at",
                                     "previous_receipt_at", "collected_at", "measurement_time",
                                     "timestamp_quality", "sample_continuity", "verified_observation",
                                     "payload_fingerprint"})
        self.assertNotIn("secret-raw", str(meta))
        self.assertLessEqual(len(meta["quantized_values"]), len(at.PERSISTENCE_NOISE_BINS))

    def test_change_across_missed_dess_updates_is_labelled_gap(self):
        rec = recorder()
        rec.observe(dd(), received_at=T0)
        rec.observe(dd(working_state="Invert Mode"), received_at=T0 + timedelta(minutes=30))
        self.assertEqual(rec.pending()[0].sample_continuity, "gap")

    def test_naive_receipt_is_rejected(self):
        with self.assertRaises(ValueError):
            recorder().observe(dd(), received_at=datetime(2026, 12, 10))

    def test_works_with_plain_objects(self):
        rec = recorder()
        rec.observe(SimpleNamespace(working_state="Line Mode", battery_voltage=26.0), received_at=T0)
        rec.observe(SimpleNamespace(working_state="Invert Mode", battery_voltage=26.0),
                    received_at=T0 + timedelta(minutes=1))
        self.assertEqual(rec.mode_transitions, 1)


class ScheduleTests(unittest.TestCase):
    def stable_night(self):
        rec = recorder()
        for i in range(20):
            now = T0 + timedelta(minutes=i)
            rec.observe(dd(), received_at=now, sun_times=night_sun(now))
        return rec, T0 + timedelta(minutes=19)

    def test_stable_grid_night_with_validated_tuple_is_900(self):
        rec, now = self.stable_night()
        self.assertEqual(rec.checkpoint_context(now), (900, "stable_grid_night"))

    def test_daylight_and_transition_windows_are_300(self):
        rec, now = self.stable_night()
        day = now.replace(hour=0, minute=0)
        for hour, minute in ((12, 0), (6, 30), (16, 45)):
            current = day + timedelta(hours=hour, minutes=minute)
            rec.latest_sun_times_raw = (current - timedelta(minutes=5), day + timedelta(hours=7),
                                        day + timedelta(hours=16))
            rec.latest_received_at = current
            with self.subTest(hour=hour, minute=minute):
                self.assertEqual(rec.checkpoint_context(current), (300, "daylight_or_transition"))

    def test_missing_stale_naive_malformed_or_future_context_is_300(self):
        rec, now = self.stable_night()
        aware = night_sun(now)
        naive = tuple(item.replace(tzinfo=None) for item in aware)
        stale = (now - timedelta(hours=4),) + aware[1:]
        future = (now + timedelta(minutes=1),) + aware[1:]
        for label, value in (("missing", None), ("naive", naive), ("stale", stale), ("future", future),
                             ("list", list(aware)), ("short", aware[:2]), ("strings", ("a", "b", "c")),
                             ("inverted", (aware[0], aware[2], aware[1]))):
            rec.latest_sun_times_raw = value
            with self.subTest(label):
                self.assertEqual(rec.checkpoint_context(now), (300, "unknown_solar_context"))

    def test_night_without_stable_line_mode_is_300(self):
        rec = recorder()
        rec.observe(dd(working_state="Invert Mode"), received_at=T0, sun_times=night_sun(T0))
        self.assertEqual(rec.checkpoint_interval(T0), 300)
        rec2 = recorder()
        rec2.observe(dd(), received_at=T0, sun_times=night_sun(T0))  # Line, but only just observed
        self.assertEqual(rec2.checkpoint_context(T0)[1], "night_not_stable_grid")

    def test_stale_receipt_cannot_select_900(self):
        rec, now = self.stable_night()
        later = now + timedelta(minutes=10)
        rec.latest_sun_times_raw = night_sun(later)
        self.assertEqual(rec.checkpoint_interval(later), 300)


class QueueTests(unittest.TestCase):
    def test_queue_is_bounded_drops_are_counted_and_checkpoints_shed_first(self):
        rec = recorder(queue_size=3)
        rec.observe(dd(), received_at=T0)
        self.assertTrue(rec.add_checkpoint(collected_at=T0 + timedelta(seconds=1)))
        for i in range(1, 5):
            rec.observe(dd(working_state="Invert Mode" if i % 2 else "Line Mode"),
                        received_at=T0 + timedelta(minutes=i))
        kinds = [e.event_kind for e in rec.pending()]
        self.assertEqual(len(kinds), 3)
        self.assertNotIn("checkpoint", kinds)
        self.assertEqual(rec.queue_drops, 2)
        self.assertEqual(rec.statistics(T0)["queue_drops"], 2)

    def test_acknowledge_by_sequence_survives_concurrent_enqueue_and_overflow(self):
        rec = recorder(queue_size=2)
        rec.observe(dd(), received_at=T0)
        rec.observe(dd(working_state="Invert Mode"), received_at=T0 + timedelta(minutes=1))
        in_flight = rec.begin_write()
        # More events arrive while the writer is awaiting the database; the
        # full queue sheds the in-flight event, whose fate the write decides.
        rec.observe(dd(), received_at=T0 + timedelta(minutes=2))
        rec.observe(dd(working_state="Invert Mode"), received_at=T0 + timedelta(minutes=3))
        self.assertEqual(rec.acknowledge([e.seq for e in in_flight]), 1)
        remaining = rec.pending()
        self.assertTrue(all(e.seq > in_flight[-1].seq for e in remaining))
        self.assertEqual(len(remaining), 2)
        self.assertEqual((rec.events_persisted, rec.queue_drops), (1, 0))

    def test_acknowledge_removes_only_confirmed_in_flight_events(self):
        rec = recorder()
        rec.observe(dd(), received_at=T0)
        rec.observe(dd(working_state="Invert Mode"), received_at=T0 + timedelta(minutes=1))
        rec.observe(dd(), received_at=T0 + timedelta(minutes=2))
        first, second = rec.begin_write()
        late_seq = 10_000  # never handed to this write
        self.assertEqual(rec.acknowledge([first.seq, late_seq]), 1)
        self.assertEqual([e.seq for e in rec.pending()], [second.seq])
        self.assertEqual(rec.events_persisted, 1)

    def test_failed_write_counts_evicted_in_flight_event_as_dropped(self):
        rec = recorder(queue_size=1)
        rec.observe(dd(), received_at=T0)
        rec.observe(dd(working_state="Invert Mode"), received_at=T0 + timedelta(minutes=1))
        rec.begin_write()
        rec.observe(dd(), received_at=T0 + timedelta(minutes=2))  # evicts the in-flight event
        self.assertEqual(rec.queue_drops, 0)
        rec.release_write()
        self.assertEqual(rec.queue_drops, 1)
        self.assertEqual(len(rec.pending()), 1)

    def test_event_identity_is_unique_and_stable(self):
        rec = recorder()
        rec.observe(dd(), received_at=T0)
        rec.observe(dd(working_state="Invert Mode"), received_at=T0 + timedelta(minutes=1))
        rec.observe(dd(), received_at=T0 + timedelta(minutes=2))
        a, b = rec.pending()
        self.assertNotEqual(a.event_id, b.event_id)
        self.assertEqual(rec.pending()[0].event_id, a.event_id)
        self.assertEqual(a.metadata(T0)["event_id"], a.event_id)

    def test_detach_writer_stops_queueing_and_counts_queued_events_as_dropped(self):
        rec = recorder()
        rec.observe(dd(), received_at=T0)
        rec.observe(dd(working_state="Invert Mode"), received_at=T0 + timedelta(minutes=1))
        self.assertEqual(rec.detach_writer(), 1)
        self.assertEqual((rec.queue_drops, rec.persistence_status, rec.writer_attached), (1, "disabled", False))
        rec.observe(dd(), received_at=T0 + timedelta(minutes=2))
        self.assertEqual(rec.pending(), ())

    def test_no_writer_attached_means_nothing_is_queued(self):
        rec = AdaptiveTelemetryRecorder()
        rec.observe(dd(), received_at=T0)
        rec.observe(dd(working_state="Invert Mode"), received_at=T0 + timedelta(minutes=1))
        self.assertEqual(rec.pending(), ())
        self.assertEqual(rec.mode_transitions, 1)
        self.assertEqual(rec.persistence_status, "disabled")

    def test_checkpoint_markers_are_spaced_and_flag_repeats(self):
        rec = recorder()
        rec.observe(dd(), received_at=T0)
        self.assertTrue(rec.add_checkpoint(collected_at=T0 + timedelta(seconds=5)))
        self.assertTrue(rec.add_checkpoint(collected_at=T0 + timedelta(seconds=65)))  # charging detail
        self.assertEqual(rec.checkpoints, 1)
        rec.observe(dd(), received_at=T0 + timedelta(minutes=5))
        self.assertTrue(rec.add_checkpoint(collected_at=T0 + timedelta(minutes=5, seconds=1)))
        first, second = [e for e in rec.pending() if e.event_kind == "checkpoint"]
        self.assertIsNone(first.payload_changed_since_last_checkpoint)
        self.assertFalse(second.payload_changed_since_last_checkpoint)

    def test_stale_or_missing_receipt_creates_no_checkpoint(self):
        rec = recorder()
        self.assertFalse(rec.add_checkpoint(collected_at=T0))
        rec.observe(dd(), received_at=T0)
        self.assertFalse(rec.add_checkpoint(collected_at=T0 + timedelta(minutes=6)))
        self.assertEqual(rec.stale_checkpoints_skipped, 2)
        self.assertEqual(rec.pending(), ())

    def test_restart_starts_from_a_fresh_baseline(self):
        rec = recorder()
        rec.observe(dd(), received_at=T0)
        restarted = recorder()
        restarted.observe(dd(working_state="Invert Mode"), received_at=T0 + timedelta(minutes=1))
        self.assertEqual(restarted.pending(), ())  # no switching claim across the restart gap
        self.assertEqual(restarted.mode_transitions, 0)


if __name__ == "__main__":
    unittest.main()
