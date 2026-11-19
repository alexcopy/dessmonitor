"""Bounded, read-only telemetry classification for existing DESS responses.

The recorder is fed by the existing InverterMonitor poll loop after every
successful DESS response. It never issues a request, never waits for I/O and
never touches hardware. It only:

* fingerprints each response to separate repeated/cached payloads from changed
  payloads;
* detects exact observed ``Line Mode`` <-> ``Invert Mode`` changes;
* applies a deterministic *persistence* noise filter to numeric channels;
* chooses the adaptive checkpoint interval from validated sun-times context;
* keeps a bounded hand-off queue for the single Timescale writer.

Provenance rules (ADR-0004, Task 0079 PLAN):

* No trusted device-measurement-time contract exists. DESS ``timestamp``/``gts``
  and ``DeviceData.device_observed_at`` are never treated as verified, so the
  verified-observation counters stay at zero under the current integration.
* ``receipt_at`` is when this process received the DESS response; it is the
  first-detection time of any change, not the physical switching time.
* ``collected_at`` is the database persistence time and is assigned by the
  writer. A database row is not proof of a new measurement.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any, Iterable
import uuid


DAY_CHECKPOINT_SECONDS = 300
STABLE_NIGHT_CHECKPOINT_SECONDS = 900
UNKNOWN_CONTEXT_CHECKPOINT_SECONDS = 300
# Sunrise/sunset transition periods keep the 5-minute checkpoint.
SUN_TRANSITION_MARGIN_SECONDS = 60 * 60
# A receipt older than one DESS update period (~5 min) is not current: the
# collector must not save it again as a fresh checkpoint.
RECEIPT_STALE_SECONDS = 300
# Responses further apart than this mean at least one DESS update window was
# not observed; a change across such a gap is labelled as a gap, not a switch.
SAMPLE_GAP_SECONDS = 600
# Detail collections (charging/emergency at 60 s) still write inverter_metrics,
# but checkpoint *event markers* are not written more often than this.
CHECKPOINT_MARKER_MIN_SPACING_SECONDS = 290
DEFAULT_QUEUE_SIZE = 256
MAX_WRITE_BATCH = 100

TIMESTAMP_QUALITY = "device_time_unverified"
EVENT_KINDS = ("mode_transition", "significant_payload_change", "checkpoint")

# DeviceData attribute -> persisted diagnostic name and its bin width.
# PERSISTENCE NOISE FILTERS ONLY. These decide whether a changed payload is
# worth an event row. They are not control, reserve, charger or policy
# thresholds and must never be used as such.
PERSISTENCE_NOISE_BINS: dict[str, tuple[str, float]] = {
    "pv_total_power": ("pv_power_w", 50.0),
    "battery_voltage": ("battery_voltage", 0.2),
    "battery_charging_current": ("battery_current_chg", 2.0),
    "battery_discharging_current": ("battery_current_dis", 2.0),
    "output_power": ("output_power_w", 50.0),
    "ac_output_load": ("ac_output_load_pct", 5.0),
    "ac_input_voltage": ("ac_input_voltage", 10.0),
}
TRANSITION_MODES = frozenset({"Line Mode", "Invert Mode"})

# inverter_metrics columns written for event rows (values from the event's own
# response, never from shared_state).
_METRIC_SOURCES: dict[str, str] = {
    "battery_voltage": "battery_voltage",
    "battery_soc": "battery_capacity",
    "battery_current_dis": "battery_discharging_current",
    "battery_current_chg": "battery_charging_current",
    "pv_power_w": "pv_total_power",
    "output_power_w": "output_power",
    "output_voltage": "output_voltage",
    "ac_input_voltage": "ac_input_voltage",
    "ac_input_frequency": "ac_input_frequency",
    "ac_output_load_pct": "ac_output_load",
    "total_load_w": "output_power",
    "working_mode": "working_state",
    "charger_priority_raw": "charger_priority",
    "output_priority_raw": "output_priority",
    "mains_status": "mains_status",
}


def _utc(value: object) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def quantize(attribute: str, value: object) -> float | None:
    """Return the lower edge of the persistence bin containing ``value``."""
    number = _number(value)
    if number is None:
        return None
    width = PERSISTENCE_NOISE_BINS[attribute][1]
    return round(math.floor(number / width) * width, 3)


def _scalar(value: object) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    number = _number(value)
    return number


def validate_sun_times(value: object, now: datetime,
                       max_age_seconds: float) -> tuple[datetime, datetime, datetime] | None:
    """Validate shared_state["output_shadow_sun_times"] = (weather_time, sunrise, sunset).

    Every element must be a timezone-aware datetime, weather_time must not be
    in the future and must be no older than ``max_age_seconds``. Anything else
    is unknown context.
    """
    current = _utc(now)
    if current is None or type(value) is not tuple or len(value) != 3:
        return None
    parsed = tuple(_utc(item) for item in value)
    if any(item is None for item in parsed):
        return None
    weather_time, sunrise, sunset = parsed
    if weather_time > current or sunrise >= sunset:
        return None
    if current - weather_time > timedelta(seconds=max_age_seconds):
        return None
    return weather_time, sunrise, sunset


@dataclass(frozen=True)
class TelemetryEvent:
    """One bounded hand-off record. Never contains a raw response payload."""

    seq: int
    event_kind: str
    from_mode: str
    to_mode: str
    changed_fields: tuple[str, ...]
    quantized_values: dict[str, float | None]
    receipt_at: datetime
    payload_fingerprint: str
    previous_receipt_at: datetime | None = None
    sample_continuity: str = "first_observation"
    payload_changed_since_last_checkpoint: bool | None = None
    battery_soc: float | None = None
    metrics: dict[str, object] = field(default_factory=dict)
    # Stable identity across write retries and processes. The writer stores it
    # in metadata and uses it to confirm that an existing row is this event.
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def metadata(self, collected_at: datetime) -> dict[str, object]:
        result: dict[str, object] = {
            "event_id": self.event_id,
            "event_kind": self.event_kind,
            "changed_fields": list(self.changed_fields),
            "quantized_values": dict(self.quantized_values),
            "receipt_at": self.receipt_at.isoformat(),
            "previous_receipt_at": self.previous_receipt_at.isoformat() if self.previous_receipt_at else None,
            "collected_at": collected_at.isoformat(),
            "measurement_time": None,
            "timestamp_quality": TIMESTAMP_QUALITY,
            "sample_continuity": self.sample_continuity,
            "verified_observation": False,
            "payload_fingerprint": self.payload_fingerprint,
        }
        if self.event_kind == "checkpoint":
            result["payload_changed_since_last_checkpoint"] = self.payload_changed_since_last_checkpoint
        return result


class AdaptiveTelemetryRecorder:
    """Classify successful responses without making them more authoritative."""

    def __init__(self, *, queue_size: int = DEFAULT_QUEUE_SIZE,
                 forecast_max_age_seconds: float = 180.0 * 60.0):
        if isinstance(queue_size, bool) or not isinstance(queue_size, int) or queue_size < 1:
            raise ValueError("queue_size must be a positive integer")
        max_age = _number(forecast_max_age_seconds)
        if max_age is None or max_age < 0:
            raise ValueError("forecast_max_age_seconds must be finite and non-negative")
        self.queue_size = queue_size
        self.forecast_max_age_seconds = max_age
        self._events: deque[TelemetryEvent] = deque()
        # Sequence numbers handed to the writer in the current write attempt.
        self._in_flight: set[int] = set()
        # In-flight events shed by overflow mid-write: counted as dropped only
        # if that write is not confirmed (they may already be persisted).
        self._evicted_in_flight: set[int] = set()
        self._seq = 0
        self.writer_attached = False
        self.latest_data: Any | None = None
        self.latest_received_at: datetime | None = None
        self.latest_sun_times_raw: object | None = None
        self._last_quantized: dict[str, float | None] | None = None
        self._last_fingerprint: str | None = None
        self._last_mode: str = ""
        self._mode_since: datetime | None = None
        self._last_marker_collected_at: datetime | None = None
        self._last_marker_fingerprint: str | None = None
        # Counters. Verified counters stay zero: no trusted device-time source exists.
        self.responses = 0
        self.repeats = 0
        self.changed_payloads = 0
        self.mode_transitions = 0
        self.significant_changes = 0
        self.checkpoints = 0
        self.stale_checkpoints_skipped = 0
        self.events_persisted = 0
        self.events_reconciled_existing = 0
        self.queue_drops = 0
        self.verified_independent_observations = 0
        self.device_time_verified_observations = 0
        self.persistence_status = "disabled"
        self.last_persisted_at: datetime | None = None

    # -- writer attachment ------------------------------------------------

    def attach_writer(self) -> None:
        """Called by the single Timescale writer. Without it nothing is queued."""
        self.writer_attached = True
        if self.persistence_status == "disabled":
            self.persistence_status = "unavailable"

    def detach_writer(self) -> int:
        """The writer cannot ever run (e.g. no DATABASE_URL/asyncpg).

        Stops queueing, reports persistence as disabled and counts any queued
        events as dropped, so nothing is held as if it will still be written.
        Returns the number of events dropped.
        """
        dropped = len(self._events)
        self._events.clear()
        self._in_flight.clear()
        self._evicted_in_flight.clear()
        self.queue_drops += dropped
        self.writer_attached = False
        self.persistence_status = "disabled"
        return dropped

    # -- observation ------------------------------------------------------

    @staticmethod
    def _fingerprint(data: Any) -> str:
        if is_dataclass(data) and not isinstance(data, type):
            payload = asdict(data)
        elif hasattr(data, "__dict__"):
            payload = dict(vars(data))
        else:
            payload = {"repr": repr(data)}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _quantized(data: Any) -> dict[str, float | None]:
        return {name: quantize(attr, getattr(data, attr, None))
                for attr, (name, _width) in PERSISTENCE_NOISE_BINS.items()}

    @staticmethod
    def _metrics(data: Any) -> dict[str, object]:
        return {column: _scalar(getattr(data, attr, None)) for column, attr in _METRIC_SOURCES.items()}

    def observe(self, data: Any, *, received_at: datetime, sun_times: object | None = None) -> None:
        """Bounded bookkeeping for one successful DESS response. No I/O."""
        received = _utc(received_at)
        if received is None:
            raise ValueError("received_at must be timezone-aware")
        fingerprint = self._fingerprint(data)
        quantized = self._quantized(data)
        mode = getattr(data, "working_state", None)
        mode = mode if isinstance(mode, str) else ""
        previous_receipt = self.latest_received_at
        self.responses += 1
        self.latest_data = data
        self.latest_received_at = received
        self.latest_sun_times_raw = sun_times

        if self._last_fingerprint is None:
            # First response after start/restart is the baseline: nothing to compare with.
            self._accept(fingerprint, quantized, mode, received)
            return
        if fingerprint == self._last_fingerprint:
            self.repeats += 1
            return

        self.changed_payloads += 1
        previous_mode = self._last_mode
        previous = self._last_quantized or {}
        changed = [name for name, value in quantized.items() if value != previous.get(name)]
        if mode != previous_mode:
            changed.insert(0, "working_mode")
        continuity = "contiguous"
        if previous_receipt is not None and (received - previous_receipt).total_seconds() > SAMPLE_GAP_SECONDS:
            continuity = "gap"
        soc = _number(getattr(data, "battery_capacity", None))
        if previous_mode in TRANSITION_MODES and mode in TRANSITION_MODES and previous_mode != mode:
            self.mode_transitions += 1
            self._enqueue("mode_transition", previous_mode, mode, tuple(changed), quantized, received,
                          fingerprint, previous_receipt, continuity, soc=soc, metrics=self._metrics(data))
        elif changed:
            self.significant_changes += 1
            self._enqueue("significant_payload_change", previous_mode, mode, tuple(changed),
                          {name: quantized[name] for name in changed if name in quantized},
                          received, fingerprint, previous_receipt, continuity, soc=soc,
                          metrics=self._metrics(data))
        self._accept(fingerprint, quantized, mode, received)

    def _accept(self, fingerprint: str, quantized: dict[str, float | None], mode: str,
                received: datetime) -> None:
        if mode != self._last_mode or self._mode_since is None:
            self._mode_since = received
        self._last_fingerprint = fingerprint
        self._last_quantized = quantized
        self._last_mode = mode

    # -- bounded queue ----------------------------------------------------

    def _enqueue(self, kind: str, from_mode: str, to_mode: str, changed: tuple[str, ...],
                 values: dict[str, float | None], received: datetime, fingerprint: str,
                 previous_receipt: datetime | None, continuity: str, *, soc: float | None = None,
                 payload_changed: bool | None = None, metrics: dict[str, object] | None = None) -> None:
        if not self.writer_attached:
            return
        if len(self._events) >= self.queue_size:
            # Shed the oldest checkpoint marker first; observed changes are kept longer.
            victim = next((item for item in self._events if item.event_kind == "checkpoint"), self._events[0])
            self._events.remove(victim)
            if victim.seq in self._in_flight:
                # Being written right now: its fate is decided by that write.
                self._evicted_in_flight.add(victim.seq)
            else:
                self.queue_drops += 1
        self._seq += 1
        self._events.append(TelemetryEvent(
            seq=self._seq, event_kind=kind, from_mode=from_mode, to_mode=to_mode,
            changed_fields=changed, quantized_values=values, receipt_at=received,
            payload_fingerprint=fingerprint, previous_receipt_at=previous_receipt,
            sample_continuity=continuity, payload_changed_since_last_checkpoint=payload_changed,
            battery_soc=soc, metrics=metrics or {},
        ))

    def pending(self, limit: int = MAX_WRITE_BATCH) -> tuple[TelemetryEvent, ...]:
        """Read-only view of the oldest queued events."""
        return tuple(list(self._events)[:max(0, limit)])

    @property
    def latest_fingerprint(self) -> str | None:
        """Fingerprint of the latest accepted response, for read-only hand-off."""
        return self._last_fingerprint

    def begin_write(self, limit: int = MAX_WRITE_BATCH) -> tuple[TelemetryEvent, ...]:
        """Hand the oldest events to the single writer and mark them in flight."""
        batch = self.pending(limit)
        self._in_flight = {event.seq for event in batch}
        self._evicted_in_flight = set()
        return batch

    def acknowledge(self, persisted_seqs: Iterable[int], *, reconciled: int = 0) -> int:
        """Remove exactly the events whose storage the writer verified.

        ``persisted_seqs`` must contain only sequence numbers from the current
        ``begin_write`` batch that were inserted (or confirmed already stored).
        Events that arrived during the write, and in-flight events that were
        not confirmed, stay queued. Returns the number of confirmed events.
        """
        confirmed = {seq for seq in persisted_seqs if seq in self._in_flight}
        kept = [event for event in self._events if event.seq not in confirmed]
        removed = len(self._events) - len(kept)
        self._events = deque(kept)
        # An in-flight event shed by overflow is persisted if confirmed, else lost.
        evicted_confirmed = len(self._evicted_in_flight & confirmed)
        self.queue_drops += len(self._evicted_in_flight - confirmed)
        self.events_persisted += removed + evicted_confirmed
        self.events_reconciled_existing += max(0, min(reconciled, removed + evicted_confirmed))
        self._in_flight = set()
        self._evicted_in_flight = set()
        return removed + evicted_confirmed

    def release_write(self) -> None:
        """The write attempt failed: nothing was confirmed, everything still queued stays."""
        self.queue_drops += len(self._evicted_in_flight)
        self._in_flight = set()
        self._evicted_in_flight = set()

    # -- checkpoints ------------------------------------------------------

    def receipt_is_current(self, now: datetime) -> bool:
        current = _utc(now)
        if current is None or self.latest_received_at is None:
            return False
        age = (current - self.latest_received_at).total_seconds()
        return 0 <= age <= RECEIPT_STALE_SECONDS

    def note_stale_checkpoint(self) -> None:
        self.stale_checkpoints_skipped += 1

    def add_checkpoint(self, *, collected_at: datetime) -> bool:
        """Queue a checkpoint marker for a collection of the current receipt.

        Returns False (and queues nothing) when no current receipt exists.
        """
        collected = _utc(collected_at)
        if collected is None or not self.receipt_is_current(collected):
            self.note_stale_checkpoint()
            return False
        if (self._last_marker_collected_at is not None
                and (collected - self._last_marker_collected_at).total_seconds()
                < CHECKPOINT_MARKER_MIN_SPACING_SECONDS):
            return True
        self.checkpoints += 1
        fingerprint = self._last_fingerprint or "none"
        changed = (None if self._last_marker_fingerprint is None
                   else fingerprint != self._last_marker_fingerprint)
        self._last_marker_collected_at = collected
        self._last_marker_fingerprint = fingerprint
        soc = _number(getattr(self.latest_data, "battery_capacity", None))
        self._enqueue("checkpoint", self._last_mode, self._last_mode, (), dict(self._last_quantized or {}),
                      self.latest_received_at, fingerprint, None, "checkpoint", soc=soc,
                      payload_changed=changed)
        return True

    def checkpoint_context(self, now: datetime) -> tuple[int, str]:
        """Return (maximum checkpoint interval seconds, reason label).

        Only a validated, fresh, timezone-aware sun-times tuple, an instant
        outside the sunrise/sunset transition window, and stable observed
        ``Line Mode`` can select the 15-minute interval.
        """
        current = _utc(now)
        if current is None:
            return UNKNOWN_CONTEXT_CHECKPOINT_SECONDS, "unknown_solar_context"
        sun = validate_sun_times(self.latest_sun_times_raw, current, self.forecast_max_age_seconds)
        if sun is None:
            return UNKNOWN_CONTEXT_CHECKPOINT_SECONDS, "unknown_solar_context"
        _weather, sunrise, sunset = sun
        margin = timedelta(seconds=SUN_TRANSITION_MARGIN_SECONDS)
        if sunrise - margin <= current <= sunset + margin:
            return DAY_CHECKPOINT_SECONDS, "daylight_or_transition"
        stable = bool(
            self._last_mode == "Line Mode"
            and self.receipt_is_current(current)
            and self._mode_since is not None
            and (current - self._mode_since).total_seconds() >= STABLE_NIGHT_CHECKPOINT_SECONDS
        )
        if not stable:
            return DAY_CHECKPOINT_SECONDS, "night_not_stable_grid"
        return STABLE_NIGHT_CHECKPOINT_SECONDS, "stable_grid_night"

    def checkpoint_interval(self, now: datetime) -> int:
        return self.checkpoint_context(now)[0]

    # -- diagnostics ------------------------------------------------------

    def statistics(self, now: datetime | None = None) -> dict[str, object]:
        current = _utc(now) or datetime.now(timezone.utc)
        age = None
        if self.latest_received_at is not None:
            age = round((current - self.latest_received_at).total_seconds(), 1)
        interval, context = self.checkpoint_context(current)
        return {
            "responses": self.responses,
            "repeated_responses": self.repeats,
            "repeat_ratio": round(self.repeats / self.responses, 3) if self.responses else None,
            "changed_payloads": self.changed_payloads,
            "mode_transitions_observed": self.mode_transitions,
            "significant_changes": self.significant_changes,
            "checkpoints": self.checkpoints,
            "stale_checkpoints_skipped": self.stale_checkpoints_skipped,
            "events_persisted": self.events_persisted,
            "events_reconciled_existing": self.events_reconciled_existing,
            "pending_events": len(self._events),
            "queue_capacity": self.queue_size,
            "queue_drops": self.queue_drops,
            "verified_independent_observations": self.verified_independent_observations,
            "device_time_verified_observations": self.device_time_verified_observations,
            "last_receipt_at": self.latest_received_at.isoformat() if self.latest_received_at else None,
            "last_receipt_age_seconds": age,
            "last_verified_measurement_at": None,
            "last_persisted_at": self.last_persisted_at.isoformat() if self.last_persisted_at else None,
            "persistence_status": self.persistence_status,
            "checkpoint_interval_seconds": interval,
            "checkpoint_context": context,
            "timestamp_quality": TIMESTAMP_QUALITY,
        }


__all__ = [
    "AdaptiveTelemetryRecorder",
    "CHECKPOINT_MARKER_MIN_SPACING_SECONDS",
    "DAY_CHECKPOINT_SECONDS",
    "EVENT_KINDS",
    "MAX_WRITE_BATCH",
    "PERSISTENCE_NOISE_BINS",
    "RECEIPT_STALE_SECONDS",
    "SAMPLE_GAP_SECONDS",
    "STABLE_NIGHT_CHECKPOINT_SECONDS",
    "SUN_TRANSITION_MARGIN_SECONDS",
    "TIMESTAMP_QUALITY",
    "TelemetryEvent",
    "UNKNOWN_CONTEXT_CHECKPOINT_SECONDS",
    "quantize",
    "validate_sun_times",
]
