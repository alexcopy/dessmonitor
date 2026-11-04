"""Opt-in, offline sag learning and shadow-only hysteresis proposals.

Callers must provide verified timestamps and explicit comparison parameters.
No measurement is requested from hardware and no safety bound is learned here.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from app.control.adaptive_output_policy import (OutputDecision, OutputPolicyBaseline, OutputPolicyReason,
                                                OutputTelemetry, evaluate_output_policy)
from app.control.seasonal_output_strategy import (ApprovedBands, EnergyInputs, _valid_bands,
                                                  evaluate_seasonal_strategy)


def _finite(value, minimum=None, maximum=None):
    try:
        return (type(value) in (int, float) and math.isfinite(value)
                and (minimum is None or value >= minimum) and (maximum is None or value <= maximum))
    except OverflowError:
        return False


def _utc(value):
    if type(value) is not datetime or value.tzinfo is None:
        return None
    try:
        return value.astimezone(timezone.utc) if value.utcoffset() is not None else None
    except (OverflowError, ValueError):
        return None


@dataclass(frozen=True)
class LearningConfig:
    # Revision must change whenever hardware, calibration or approved policy changes.
    context_id: str
    policy_fingerprint: str
    min_pairs: int
    stable_updates: int
    max_pairs: int
    max_age_seconds: float
    max_pair_gap_seconds: float
    max_duration_delta_seconds: float
    max_load_delta_w: float
    max_discharge_delta_a: float
    max_temperature_delta_c: float
    max_soc_delta_pct: float
    max_uncertainty_v: float
    min_confidence: float
    change_threshold_v: float
    max_adjustment_step_v: float

    def __post_init__(self):
        if type(self.context_id) is not str or not self.context_id or len(self.context_id) > 128:
            raise ValueError("context_id must identify an operator-controlled revision")
        if (type(self.policy_fingerprint) is not str or len(self.policy_fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in self.policy_fingerprint)):
            raise ValueError("valid policy fingerprint required")
        if any(type(x) is not int for x in (self.min_pairs, self.stable_updates, self.max_pairs)):
            raise ValueError("pair counts must be integers")
        if not (3 <= self.min_pairs <= self.max_pairs <= 256
                and 1 <= self.stable_updates <= self.max_pairs - self.min_pairs + 1):
            raise ValueError("invalid pair counts")
        for name in ("max_age_seconds", "max_pair_gap_seconds", "max_duration_delta_seconds", "max_load_delta_w",
                     "max_discharge_delta_a", "max_temperature_delta_c", "max_soc_delta_pct", "max_uncertainty_v",
                     "change_threshold_v", "max_adjustment_step_v"):
            value = getattr(self, name)
            if not _finite(value, 0) or value == 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.max_age_seconds > 86400 * 365 or self.max_pair_gap_seconds > 86400:
            raise ValueError("learning time bounds are too large")
        if not _finite(self.min_confidence, 0, 1) or self.min_confidence == 0:
            raise ValueError("min_confidence must be in (0, 1]")
        if self.change_threshold_v <= self.max_uncertainty_v:
            raise ValueError("change threshold must exceed allowed uncertainty")


@dataclass(frozen=True)
class SagSample:
    observed_at: datetime
    received_at: datetime
    episode_id: str
    mode: str
    voltage_v: float
    output_w: float
    discharge_a: float
    charge_a: float
    source_verified: bool
    temperature_c: float | None = None
    soc_pct: float | None = None


@dataclass(frozen=True)
class SagPair:
    episode_id: str
    start_at: datetime
    end_at: datetime
    drop_v: float
    load_w: float
    load_min_w: float
    load_max_w: float
    discharge_a: float
    discharge_min_a: float
    discharge_max_a: float
    duration_seconds: float
    temperature_c: float | None
    temperature_min_c: float | None
    temperature_max_c: float | None
    soc_pct: float | None
    soc_min_pct: float | None
    soc_max_pct: float | None


@dataclass(frozen=True)
class SagEstimate:
    observation_count: int
    sag_v: float | None
    uncertainty_v: float | None
    confidence: float
    load_range_w: tuple[float, float] | None
    temperature_range_c: tuple[float, float] | None
    soc_range_pct: tuple[float, float] | None
    stable_updates: int
    usable: bool
    reason: str


@dataclass(frozen=True)
class SagProposal:
    status: str
    reason: str
    battery_threshold_v: float | None = None
    hysteresis_v: float | None = None
    expires_at: datetime | None = None
    shadow_only: bool = True
    command_executed: bool = False

    def __post_init__(self):
        if (self.status not in ("NO_PROPOSAL", "COOLDOWN", "SHADOW_PROPOSAL")
                or type(self.reason) is not str or self.shadow_only is not True or self.command_executed is not False):
            raise ValueError("invalid shadow proposal")
        expiry = _utc(self.expires_at) if self.expires_at is not None else None
        if self.status == "SHADOW_PROPOSAL":
            if not _finite(self.battery_threshold_v, 0) or not _finite(self.hysteresis_v, 0) or expiry is None:
                raise ValueError("shadow proposal requires valid thresholds and expiry")
        elif self.battery_threshold_v is not None or self.hysteresis_v is not None or self.expires_at is not None:
            raise ValueError("non-proposals cannot contain thresholds")
        object.__setattr__(self, "expires_at", expiry)


def policy_fingerprint(baseline: OutputPolicyBaseline, bands: ApprovedBands) -> str:
    """Bind learning to exact approved baseline and proposal bands."""
    if type(baseline) is not OutputPolicyBaseline or not _valid_bands(bands, baseline):
        raise ValueError("valid baseline and approved bands required")
    encoded = json.dumps({"baseline": asdict(baseline), "bands": asdict(bands)},
                         sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _comparable(a, b, cfg):
    if (max(a.load_max_w, b.load_max_w) - min(a.load_min_w, b.load_min_w) > cfg.max_load_delta_w
            or max(a.discharge_max_a, b.discharge_max_a) - min(a.discharge_min_a, b.discharge_min_a) > cfg.max_discharge_delta_a):
        return False
    if abs(a.duration_seconds - b.duration_seconds) > cfg.max_duration_delta_seconds:
        return False
    for x, y, lo_x, hi_x, lo_y, hi_y, limit in (
            (a.temperature_c, b.temperature_c, a.temperature_min_c, a.temperature_max_c,
             b.temperature_min_c, b.temperature_max_c, cfg.max_temperature_delta_c),
            (a.soc_pct, b.soc_pct, a.soc_min_pct, a.soc_max_pct,
             b.soc_min_pct, b.soc_max_pct, cfg.max_soc_delta_pct)):
        if ((x is None) != (y is None)
                or (x is not None and max(hi_x, hi_y) - min(lo_x, lo_y) > limit)):
            return False
    return True


class SagLearner:
    """One independent pair per externally identified trial; no active probing."""
    def __init__(self, config: LearningConfig):
        if type(config) is not LearningConfig:
            raise TypeError("validated LearningConfig required")
        self.config = config
        self._pairs = []
        self._start = None
        self._last_at = None
        self._stable = 0
        self._fresh_pairs = 0
        self._adjustment_v = 0.0
        self._reason = "INSUFFICIENT_OBSERVATIONS"

    def _invalidate(self, reason):
        self._start = None
        self._stable = self._fresh_pairs = 0
        self._adjustment_v = 0.0
        self._reason = reason
        return reason

    def observe(self, sample: SagSample, *, evaluated_at: datetime) -> str:
        now = _utc(evaluated_at)
        if now is None or type(sample) is not SagSample:
            raise ValueError("valid evaluation time and SagSample required")
        obs, received = _utc(sample.observed_at), _utc(sample.received_at)
        numeric = (_finite(sample.voltage_v, 0) and sample.voltage_v > 0 and _finite(sample.output_w, 0)
                   and _finite(sample.discharge_a, 0) and _finite(sample.charge_a, 0))
        optional = ((sample.temperature_c is None or _finite(sample.temperature_c, -100, 100))
                    and (sample.soc_pct is None or _finite(sample.soc_pct, 0, 100)))
        if (sample.source_verified is not True or obs is None or received is None or not numeric or not optional
                or type(sample.episode_id) is not str or not sample.episode_id or len(sample.episode_id) > 128
                or obs > received or received > now or (now - obs).total_seconds() > self.config.max_age_seconds
                or (self._last_at is not None and obs <= self._last_at)):
            return self._invalidate("UNTRUSTED_OR_OUT_OF_ORDER")
        self._last_at = obs
        if sample.mode not in ("Line Mode", "Invert Mode"):
            return self._invalidate("UNKNOWN_MODE")
        if sample.charge_a > 0 and sample.discharge_a > 0:
            return self._invalidate("CONFLICTING_CURRENT")
        if sample.mode == "Line Mode":
            self._start = None
            self._reason = ("CHARGE_CURRENT_REPORTED_NOT_LEARNING_RECHARGE" if sample.charge_a > 0
                            else "MODE_CHANGE_REBOUND_NOT_PROOF_OF_RECHARGE")
            return self._reason
        if sample.charge_a > 0:
            return self._invalidate("CONFLICTING_CURRENT")
        if sample.discharge_a == 0:
            return self._invalidate("DISCHARGE_NOT_OBSERVED")
        # One pair per trial; duplicate episodes cannot inflate confidence.
        if any(pair.episode_id == sample.episode_id for pair in self._pairs):
            self._start = None
            self._reason = "EPISODE_ALREADY_COUNTED"
            return self._reason
        if self._start is None or self._start.episode_id != sample.episode_id:
            self._start = sample
            self._reason = "WAITING_FOR_COMPARABLE_SAMPLE"
            return self._reason
        first = self._start
        self._start = None
        elapsed = (obs - _utc(first.observed_at)).total_seconds()
        if (elapsed <= 0 or elapsed > self.config.max_pair_gap_seconds
                or (now - _utc(first.observed_at)).total_seconds() > self.config.max_age_seconds
                or abs(sample.output_w - first.output_w) > self.config.max_load_delta_w
                or abs(sample.discharge_a - first.discharge_a) > self.config.max_discharge_delta_a):
            return self._invalidate("INCOMPARABLE_LOAD_OR_TIME")
        for a, b, limit in ((first.temperature_c, sample.temperature_c, self.config.max_temperature_delta_c),
                            (first.soc_pct, sample.soc_pct, self.config.max_soc_delta_pct)):
            if (a is None) != (b is None) or (a is not None and abs(a-b) > limit):
                return self._invalidate("INCOMPARABLE_TEMPERATURE_OR_SOC")
        drop = first.voltage_v - sample.voltage_v
        if drop < 0:
            return self._invalidate("VOLTAGE_RISE_UNCLASSIFIED")
        pair = SagPair(sample.episode_id, _utc(first.observed_at), obs, drop,
                       (first.output_w + sample.output_w) / 2, min(first.output_w, sample.output_w), max(first.output_w, sample.output_w),
                       (first.discharge_a + sample.discharge_a) / 2, min(first.discharge_a, sample.discharge_a), max(first.discharge_a, sample.discharge_a),
                       elapsed, None if first.temperature_c is None else (first.temperature_c + sample.temperature_c)/2,
                       None if first.temperature_c is None else min(first.temperature_c, sample.temperature_c),
                       None if first.temperature_c is None else max(first.temperature_c, sample.temperature_c),
                       None if first.soc_pct is None else (first.soc_pct + sample.soc_pct)/2,
                       None if first.soc_pct is None else min(first.soc_pct, sample.soc_pct),
                       None if first.soc_pct is None else max(first.soc_pct, sample.soc_pct))
        if not all(_finite(value, 0) for value in (pair.drop_v, pair.load_w, pair.discharge_a)):
            return self._invalidate("NONFINITE_PAIR")
        return self._accept(pair, now)

    def _accept(self, pair, now):
        # Expired evidence is removed rather than used to sustain confidence.
        prior_count = len(self._pairs)
        self._pairs = [p for p in self._pairs if 0 <= (now-p.end_at).total_seconds() <= self.config.max_age_seconds]
        if len(self._pairs) != prior_count:
            self._stable = self._fresh_pairs = 0
            self._adjustment_v = 0.0
        if self._pairs and not all(_comparable(pair, p, self.config) for p in self._pairs):
            self._pairs = []
            self._stable = self._fresh_pairs = 0
            self._adjustment_v = 0.0
            self._reason = "CONTEXT_CHANGED"
        elif self._pairs and abs(pair.drop_v - median(p.drop_v for p in self._pairs)) > self.config.change_threshold_v:
            self._pairs = []
            self._stable = self._fresh_pairs = 0
            self._adjustment_v = 0.0
            self._reason = "BEHAVIOR_CHANGED_CAUSE_UNKNOWN"
        else:
            self._reason = "OBSERVATION_ACCEPTED"
        self._pairs.append(pair)
        self._pairs = self._pairs[-self.config.max_pairs:]
        self._fresh_pairs = min(self._fresh_pairs + 1, self.config.max_pairs)
        values = [p.drop_v for p in self._pairs]
        uncertainty = (max(values) - min(values)) / 2
        confidence = min(1.0, len(values) / self.config.min_pairs) * max(0.0, 1 - uncertainty / self.config.max_uncertainty_v)
        if len(values) >= self.config.min_pairs and uncertainty <= self.config.max_uncertainty_v and confidence >= self.config.min_confidence:
            self._stable = min(self._stable + 1, self.config.stable_updates)
        else:
            self._stable = 0
        if self._stable >= self.config.stable_updates and self._fresh_pairs >= self.config.min_pairs:
            target = median(values)
            step = self.config.max_adjustment_step_v
            self._adjustment_v += max(-step, min(step, target - self._adjustment_v))
        return self._reason

    def estimate(self, *, evaluated_at: datetime) -> SagEstimate:
        now = _utc(evaluated_at)
        if now is None:
            raise ValueError("aware evaluation time required")
        pairs = self._pairs
        if not pairs:
            return SagEstimate(0, None, None, 0.0, None, None, None, 0, False, self._reason)
        if any(not 0 <= (now-p.end_at).total_seconds() <= self.config.max_age_seconds for p in pairs):
            return SagEstimate(len(pairs), None, None, 0.0, None, None, None, 0, False, "EVIDENCE_EXPIRED")
        values = [p.drop_v for p in pairs]
        uncertainty = (max(values)-min(values))/2
        confidence = min(1.0, len(values)/self.config.min_pairs) * max(0.0, 1-uncertainty/self.config.max_uncertainty_v)
        def span(low_name, high_name):
            lows = [getattr(p, low_name) for p in pairs]
            highs = [getattr(p, high_name) for p in pairs]
            return (min(lows), max(highs)) if all(x is not None for x in lows + highs) else None
        usable = (len(values) >= self.config.min_pairs and self._fresh_pairs >= self.config.min_pairs
                  and self._stable >= self.config.stable_updates and confidence >= self.config.min_confidence)
        return SagEstimate(len(values), median(values), uncertainty, confidence, span("load_min_w", "load_max_w"),
                           span("temperature_min_c", "temperature_max_c"), span("soc_min_pct", "soc_max_pct"), self._stable, usable,
                           "STABLE" if usable else self._reason)

    def propose(self, snapshot: OutputTelemetry, baseline: OutputPolicyBaseline, history: tuple[OutputTelemetry, ...],
                energy: EnergyInputs | None, bands: ApprovedBands, *, evaluated_at: datetime) -> SagProposal:
        """Re-evaluate both approved guards and seasonal prerequisites at proposal time."""
        now = _utc(evaluated_at)
        if now is None or type(snapshot) is not OutputTelemetry or type(baseline) is not OutputPolicyBaseline:
            return SagProposal("NO_PROPOSAL", "INVALID_INPUT")
        policy = evaluate_output_policy(snapshot, baseline, history, evaluated_at=now)
        if any(reason in policy.reason_codes for reason in (OutputPolicyReason.COOLDOWN, OutputPolicyReason.CHURN_LOCKOUT,
                                                             OutputPolicyReason.FAST_VOLTAGE_SAG)):
            return SagProposal("COOLDOWN", "PROTECTIVE_POLICY_RESTRICTION")
        seasonal = evaluate_seasonal_strategy(snapshot, baseline, history, evaluated_at=now, energy=energy, bands=bands)
        estimate = self.estimate(evaluated_at=now)
        if (policy.decision is not OutputDecision.CONSIDER or seasonal.policy_decision is not policy.decision
                or policy.evaluated_at != now or seasonal.evaluated_at != now or seasonal.expires_at is None
                or seasonal.expires_at <= now or not estimate.usable or not _valid_bands(bands, baseline)
                or seasonal.proposed_utility_v is None or seasonal.proposed_battery_v is None):
            return SagProposal("NO_PROPOSAL", "SAFETY_OR_LEARNING_NOT_READY")
        if policy_fingerprint(baseline, bands) != self.config.policy_fingerprint:
            return SagProposal("NO_PROPOSAL", "POLICY_CONTEXT_CHANGED")
        utility, battery = seasonal.proposed_utility_v, seasonal.proposed_battery_v
        if not (bands.utility_min_v <= utility <= bands.utility_max_v
                and baseline.exit_battery_v <= utility <= baseline.exit_battery_v + bands.max_utility_increase_v
                and bands.battery_min_v <= battery <= bands.battery_max_v
                and baseline.enter_battery_v <= battery <= baseline.enter_battery_v + bands.max_battery_increase_v
                and battery > utility and battery - utility >= baseline.min_hysteresis_v):
            return SagProposal("NO_PROPOSAL", "INVALID_BASE_PROPOSAL")
        # Learning can only widen hysteresis upward, within explicitly approved
        # bands/deltas. This remains a shadow suggestion, never a safety bound.
        ceiling = min(bands.battery_max_v, baseline.enter_battery_v + bands.max_battery_increase_v)
        proposed = min(ceiling, battery + self._adjustment_v)
        try:
            expiry = min(seasonal.expires_at, min(p.end_at for p in self._pairs) + timedelta(seconds=self.config.max_age_seconds))
        except OverflowError:
            return SagProposal("NO_PROPOSAL", "INVALID_LEARNING_EXPIRY")
        if expiry <= now:
            return SagProposal("NO_PROPOSAL", "LEARNING_EXPIRED")
        return SagProposal("SHADOW_PROPOSAL", "STABLE_COMPARABLE_SAG", proposed, proposed-utility, expiry)

    def save(self, path: str | Path, *, evaluated_at: datetime) -> None:
        """Persist only a usable model; never persist a pending sample."""
        if not self.estimate(evaluated_at=evaluated_at).usable:
            raise ValueError("only a validated stable model can be persisted")
        payload = {"version": 1, "config": asdict(self.config), "pairs": [
            {**asdict(pair), "start_at": pair.start_at.isoformat(), "end_at": pair.end_at.isoformat()}
            for pair in self._pairs]}
        destination = Path(path)
        fd, tmp = tempfile.mkstemp(prefix=".sag-learning-", dir=destination.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, allow_nan=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, destination)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @classmethod
    def load(cls, path: str | Path, config: LearningConfig, *, evaluated_at: datetime) -> SagLearner:
        """Load compatible evidence, requiring a full fresh revalidation period."""
        now = _utc(evaluated_at)
        if now is None or type(config) is not LearningConfig:
            raise ValueError("valid time and configuration required")
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result
        try:
            with open(path, encoding="utf-8") as stream:
                text = stream.read(262145)
            if len(text) > 262144:
                raise ValueError("state too large")
            payload = json.loads(text, object_pairs_hook=unique)
            if (type(payload) is not dict or set(payload) != {"version", "config", "pairs"}
                    or type(payload["version"]) is not int or payload["version"] != 1
                    or json.dumps(payload["config"], sort_keys=True) != json.dumps(asdict(config), sort_keys=True)
                    or type(payload["pairs"]) is not list
                    or not config.min_pairs <= len(payload["pairs"]) <= config.max_pairs):
                raise ValueError("incompatible learning state")
            learner = cls(config)
            keys = set(SagPair.__dataclass_fields__)
            seen = set()
            for raw in payload["pairs"]:
                if type(raw) is not dict or set(raw) != keys:
                    raise ValueError("invalid pair")
                start = _utc(datetime.fromisoformat(raw["start_at"]))
                end = _utc(datetime.fromisoformat(raw["end_at"]))
                if (start is None or end is None or start >= end or end > now
                        or (now-end).total_seconds() > config.max_age_seconds
                        or not _finite(raw["drop_v"], 0) or not _finite(raw["load_w"], 0)
                        or not _finite(raw["discharge_a"], 0) or raw["discharge_a"] == 0
                        or not _finite(raw["load_min_w"], 0) or not _finite(raw["load_max_w"], 0)
                        or not raw["load_min_w"] <= raw["load_w"] <= raw["load_max_w"]
                        or raw["load_max_w"] - raw["load_min_w"] > config.max_load_delta_w
                        or not _finite(raw["discharge_min_a"], 0) or raw["discharge_min_a"] == 0
                        or not _finite(raw["discharge_max_a"], 0)
                        or not raw["discharge_min_a"] <= raw["discharge_a"] <= raw["discharge_max_a"]
                        or raw["discharge_max_a"] - raw["discharge_min_a"] > config.max_discharge_delta_a
                        or not _finite(raw["duration_seconds"], 0) or raw["duration_seconds"] != (end-start).total_seconds()
                        or raw["duration_seconds"] > config.max_pair_gap_seconds
                        or type(raw["episode_id"]) is not str or not raw["episode_id"] or len(raw["episode_id"]) > 128
                        or raw["episode_id"] in seen
                        or (raw["temperature_c"] is not None and not _finite(raw["temperature_c"], -100, 100))
                        or (raw["soc_pct"] is not None and not _finite(raw["soc_pct"], 0, 100))):
                    raise ValueError("invalid or stale pair")
                if learner._pairs and start <= learner._pairs[-1].end_at:
                    raise ValueError("non-chronological pair")
                for mid, low, high, minimum, maximum, tolerance in (
                    ("temperature_c", "temperature_min_c", "temperature_max_c", -100, 100, config.max_temperature_delta_c),
                    ("soc_pct", "soc_min_pct", "soc_max_pct", 0, 100, config.max_soc_delta_pct),
                ):
                    if raw[mid] is None:
                        if raw[low] is not None or raw[high] is not None:
                            raise ValueError("inconsistent optional context")
                    elif (not _finite(raw[low], minimum, maximum) or not _finite(raw[high], minimum, maximum)
                          or not raw[low] <= raw[mid] <= raw[high] or raw[high] - raw[low] > tolerance):
                        raise ValueError("invalid optional context range")
                seen.add(raw["episode_id"])
                learner._accept(SagPair(**{**raw, "start_at": start, "end_at": end}), now)
            if not learner.estimate(evaluated_at=now).usable or len(learner._pairs) != len(payload["pairs"]):
                raise ValueError("unstable learning state")
            # Persistent values are historical, not trusted live evidence.
            learner._start = None
            learner._last_at = learner._pairs[-1].end_at
            learner._fresh_pairs = learner._stable = 0
            learner._adjustment_v = 0.0
            learner._reason = "REVALIDATION_REQUIRED"
            return learner
        except (OSError, ValueError, TypeError, KeyError, OverflowError, json.JSONDecodeError) as exc:
            raise ValueError("learning state unavailable, invalid, stale or incompatible") from exc
