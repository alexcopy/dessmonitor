"""Read-only validation of the proposed output-source policy configuration."""

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from app.control.adaptive_output_policy import OutputPolicyBaseline


class OutputPolicyConfigError(ValueError):
    """An invalid policy value, identified by its YAML path."""


@dataclass(frozen=True)
class UnconfiguredOutputPolicy:
    configured: bool = False

    def __post_init__(self):
        if self.configured is not False:
            raise OutputPolicyConfigError("configured: must be false")


@dataclass(frozen=True)
class BaselineSettings:
    exit_battery_v: float | None
    enter_battery_v: float | None
    min_hysteresis_v: float | None
    max_battery_voltage_v: float | None = None
    max_battery_discharge_a: float | None = None
    max_battery_charge_a: float | None = None

    def __post_init__(self):
        for name in ("exit_battery_v", "enter_battery_v", "min_hysteresis_v", "max_battery_voltage_v", "max_battery_discharge_a", "max_battery_charge_a"):
            _number(getattr(self, name), f"output_source_policy.baseline.{name}", nullable=True, positive=name != "min_hysteresis_v")
        if self.exit_battery_v is not None and self.enter_battery_v is not None:
            if self.enter_battery_v <= self.exit_battery_v:
                raise OutputPolicyConfigError("output_source_policy.baseline.enter_battery_v: must exceed exit_battery_v")
            if self.min_hysteresis_v is not None and self.enter_battery_v - self.exit_battery_v < self.min_hysteresis_v:
                raise OutputPolicyConfigError("output_source_policy.baseline.min_hysteresis_v: exceeds voltage gap")
        if self.max_battery_voltage_v is not None:
            if self.exit_battery_v is not None and self.max_battery_voltage_v <= self.exit_battery_v:
                raise OutputPolicyConfigError("output_source_policy.baseline.max_battery_voltage_v: must exceed exit_battery_v")
            if self.enter_battery_v is not None and self.max_battery_voltage_v < self.enter_battery_v:
                raise OutputPolicyConfigError("output_source_policy.baseline.max_battery_voltage_v: must not be below enter_battery_v")
            if self.exit_battery_v is not None and self.min_hysteresis_v is not None and self.max_battery_voltage_v - self.exit_battery_v < self.min_hysteresis_v:
                raise OutputPolicyConfigError("output_source_policy.baseline.min_hysteresis_v: exceeds maximum voltage gap")
        if self.enter_battery_v is not None and self.min_hysteresis_v is not None and self.enter_battery_v <= self.min_hysteresis_v:
            raise OutputPolicyConfigError("output_source_policy.baseline.min_hysteresis_v: leaves no positive exit voltage")


@dataclass(frozen=True)
class SolarSettings:
    timezone: str | None
    sunrise_offset_min: float
    sunset_reserve_lead_min: float
    forecast_max_age_min: float
    pv_stability_window_min: float

    def __post_init__(self):
        _timezone_syntax(self.timezone)
        for name in ("sunrise_offset_min", "sunset_reserve_lead_min", "forecast_max_age_min", "pv_stability_window_min"):
            _number(getattr(self, name), f"output_source_policy.solar_opportunity.{name}", positive=name in ("forecast_max_age_min", "pv_stability_window_min"))

    @property
    def timezone_verified(self):
        """Syntax or database resolution never verifies the installation timezone."""
        return False


@dataclass(frozen=True)
class DemandSettings:
    use_measured_output_w: bool
    use_measured_battery_current: bool
    voltage_trend_window_min: float
    post_transition_settle_min: float
    min_eligible_runtime_min: float
    unknown_risk_blocks_battery_return: bool

    def __post_init__(self):
        for name in ("use_measured_output_w", "use_measured_battery_current", "unknown_risk_blocks_battery_return"):
            _boolean(getattr(self, name), f"output_source_policy.demand_and_sag.{name}", must_be_true=True)
        for name in ("voltage_trend_window_min", "post_transition_settle_min", "min_eligible_runtime_min"):
            _number(getattr(self, name), f"output_source_policy.demand_and_sag.{name}", positive=name != "post_transition_settle_min")


@dataclass(frozen=True)
class AdaptationSettings:
    enabled_in_shadow: bool
    max_delta_exit_v: float | None
    max_delta_enter_v: float | None
    expiry_min: float
    midday_reassessment: bool
    restore_before_sunset: bool

    def __post_init__(self):
        for name in ("enabled_in_shadow", "midday_reassessment", "restore_before_sunset"):
            _boolean(getattr(self, name), f"output_source_policy.hysteresis_adaptation.{name}")
        for name in ("max_delta_exit_v", "max_delta_enter_v", "expiry_min"):
            _number(getattr(self, name), f"output_source_policy.hysteresis_adaptation.{name}", nullable=name != "expiry_min", positive=name == "expiry_min")


@dataclass(frozen=True)
class AntiFlapSettings:
    stable_condition_min: float
    min_mains_dwell_min: float
    min_battery_dwell_min: float
    failed_trial_window_min: float
    failed_trial_lockout_min: float
    max_optional_output_commands_per_hour: int

    def __post_init__(self):
        for name in ("stable_condition_min", "min_mains_dwell_min", "min_battery_dwell_min", "failed_trial_window_min", "failed_trial_lockout_min"):
            _number(getattr(self, name), f"output_source_policy.anti_flap.{name}", positive=True)
        if type(self.max_optional_output_commands_per_hour) is not int or self.max_optional_output_commands_per_hour < 0:
            raise OutputPolicyConfigError("output_source_policy.anti_flap.max_optional_output_commands_per_hour: must be a non-negative integer")


@dataclass(frozen=True)
class SafetySettings:
    honor_bms_and_inverter_protection: bool
    no_optimistic_action_on_stale_data: bool
    respect_manual_override: bool
    leave_charge_policy_independent: bool

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            _boolean(getattr(self, name), f"output_source_policy.safety.{name}", must_be_true=True)


@dataclass(frozen=True)
class AuditSettings:
    decision_log_enabled: bool
    source_transition_log_enabled: bool
    command_observation_correlation: bool

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            _boolean(getattr(self, name), f"output_source_policy.audit.{name}")


@dataclass(frozen=True)
class OutputPolicyConfig:
    version: int
    mode: str
    allow_output_writes: bool
    baseline: BaselineSettings
    solar_opportunity: SolarSettings
    demand_and_sag: DemandSettings
    hysteresis_adaptation: AdaptationSettings
    anti_flap: AntiFlapSettings
    safety: SafetySettings
    audit: AuditSettings
    configured: bool = True

    def __post_init__(self):
        if type(self.version) is not int or self.version != 1:
            raise OutputPolicyConfigError("output_source_policy.version: only version 1 is supported")
        if type(self.mode) is not str or self.mode != "observe":
            raise OutputPolicyConfigError("output_source_policy.mode: only observe is supported")
        if self.allow_output_writes is not False or self.configured is not True:
            raise OutputPolicyConfigError("output_source_policy: writes must be false and configured must be true")
        for name, expected in (("baseline", BaselineSettings), ("solar_opportunity", SolarSettings), ("demand_and_sag", DemandSettings), ("hysteresis_adaptation", AdaptationSettings), ("anti_flap", AntiFlapSettings), ("safety", SafetySettings), ("audit", AuditSettings)):
            if type(getattr(self, name)) is not expected:
                raise OutputPolicyConfigError(f"output_source_policy.{name}: invalid settings type")

    def to_baseline(self) -> OutputPolicyBaseline:
        """Project fields whose meanings match the Phase 1A model."""
        b, s, d, a = self.baseline, self.solar_opportunity, self.demand_and_sag, self.anti_flap
        return OutputPolicyBaseline(
            exit_battery_v=b.exit_battery_v, enter_battery_v=b.enter_battery_v,
            min_hysteresis_v=b.min_hysteresis_v,
            max_battery_voltage_v=b.max_battery_voltage_v,
            max_battery_discharge_a=b.max_battery_discharge_a,
            max_battery_charge_a=b.max_battery_charge_a,
            sunset_reserve_seconds=s.sunset_reserve_lead_min * 60,
            forecast_max_age_seconds=s.forecast_max_age_min * 60,
            pv_history_window_seconds=s.pv_stability_window_min * 60,
            pv_minimum_span_seconds=s.pv_stability_window_min * 60,
            voltage_sag_window_seconds=d.voltage_trend_window_min * 60,
            post_transition_settle_seconds=d.post_transition_settle_min * 60,
            min_mains_dwell_seconds=a.min_mains_dwell_min * 60,
            min_battery_dwell_seconds=a.min_battery_dwell_min * 60,
            failed_trial_max_seconds=a.failed_trial_window_min * 60,
            failed_trial_lockout_seconds=a.failed_trial_lockout_min * 60,
        )


def _mapping(value, path, allowed, required=()):
    if not isinstance(value, dict):
        raise OutputPolicyConfigError(f"{path}: must be a mapping")
    unknown = set(value) - set(allowed)
    if unknown:
        raise OutputPolicyConfigError(f"{path}.{sorted(map(str, unknown))[0]}: unsupported field")
    missing = set(required) - set(value)
    if missing:
        raise OutputPolicyConfigError(f"{path}.{sorted(missing)[0]}: required field missing")
    return value


def _number(value, path, *, nullable=False, positive=False):
    if value is None and nullable:
        return None
    try:
        valid = type(value) in (int, float) and isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise OutputPolicyConfigError(f"{path}: must be a finite number" + (" or null" if nullable else ""))
    if value < 0 or (positive and value == 0):
        raise OutputPolicyConfigError(f"{path}: must be {'positive' if positive else 'non-negative'}")
    # Software-domain bound: seven days per configured duration. Phase 1A's
    # composed history is under one year for snapshots in years 2 through 9998.
    if path.endswith("_min") and value > 7 * 24 * 60:
        raise OutputPolicyConfigError(f"{path}: duration exceeds seven days")
    return float(value)


def _boolean(value, path, *, must_be_true=False):
    if type(value) is not bool or (must_be_true and value is not True):
        raise OutputPolicyConfigError(f"{path}: must be {'true' if must_be_true else 'boolean'}")
    return value


def _timezone_syntax(value):
    if value is not None and (type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_+.-]+(?:/[A-Za-z0-9_+.-]+)*", value) or any(part in (".", "..") for part in value.split("/"))):
        raise OutputPolicyConfigError("output_source_policy.solar_opportunity.timezone: invalid identifier syntax")


def _section(root, name, cls, *, booleans=(), required_true=(), nullable=(), positive=(), integer=(), optional=()):
    path = f"output_source_policy.{name}"
    names = tuple(cls.__dataclass_fields__)
    data = _mapping(root.get(name, {}), path, names, set(names) - set(optional))
    values = {}
    for key in names:
        value = data.get(key)
        field_path = f"{path}.{key}"
        if key == "timezone":
            _timezone_syntax(value)
            values[key] = value
        elif key in booleans:
            values[key] = _boolean(value, field_path, must_be_true=key in required_true)
        elif key in integer:
            if type(value) is not int or value < 0:
                raise OutputPolicyConfigError(f"{field_path}: must be a non-negative integer")
            values[key] = value
        else:
            values[key] = _number(value, field_path, nullable=key in nullable, positive=key in positive)
    return cls(**values)


def parse_output_policy(document: dict) -> OutputPolicyConfig | UnconfiguredOutputPolicy:
    """Validate an already parsed document without mutating it."""
    if not isinstance(document, dict):
        raise OutputPolicyConfigError("document: must be a mapping")
    if "output_source_policy" not in document:
        return UnconfiguredOutputPolicy()
    path = "output_source_policy"
    groups = ("baseline", "solar_opportunity", "demand_and_sag", "hysteresis_adaptation", "anti_flap", "safety", "audit")
    root = _mapping(document[path], path, ("version", "mode", "allow_output_writes", *groups), ("version", "mode", "allow_output_writes", *groups))
    if type(root["version"]) is not int or root["version"] != 1:
        raise OutputPolicyConfigError(f"{path}.version: only version 1 is supported")
    if root["mode"] != "observe" or type(root["mode"]) is not str:
        raise OutputPolicyConfigError(f"{path}.mode: only observe is supported")
    if root["allow_output_writes"] is not False:
        raise OutputPolicyConfigError(f"{path}.allow_output_writes: must be false")
    limits = ("max_battery_voltage_v", "max_battery_discharge_a", "max_battery_charge_a")
    baseline = _section(root, "baseline", BaselineSettings, nullable=BaselineSettings.__dataclass_fields__, optional=BaselineSettings.__dataclass_fields__, positive=limits)
    if baseline.exit_battery_v == 0 or baseline.enter_battery_v == 0:
        raise OutputPolicyConfigError(f"{path}.baseline: voltage thresholds must be positive")
    if baseline.exit_battery_v is not None and baseline.enter_battery_v is not None:
        if baseline.enter_battery_v <= baseline.exit_battery_v:
            raise OutputPolicyConfigError(f"{path}.baseline.enter_battery_v: must exceed exit_battery_v")
        if baseline.min_hysteresis_v is not None and baseline.enter_battery_v - baseline.exit_battery_v < baseline.min_hysteresis_v:
            raise OutputPolicyConfigError(f"{path}.baseline.min_hysteresis_v: exceeds voltage gap")
    if baseline.max_battery_voltage_v is not None and baseline.enter_battery_v is not None and baseline.max_battery_voltage_v < baseline.enter_battery_v:
        raise OutputPolicyConfigError(f"{path}.baseline.max_battery_voltage_v: must not be below enter_battery_v")
    solar = _section(root, "solar_opportunity", SolarSettings, nullable=("timezone",), optional=("timezone",), positive=("forecast_max_age_min", "pv_stability_window_min"))
    demand = _section(root, "demand_and_sag", DemandSettings, booleans=("use_measured_output_w", "use_measured_battery_current", "unknown_risk_blocks_battery_return"), required_true=("use_measured_output_w", "use_measured_battery_current", "unknown_risk_blocks_battery_return"), positive=("voltage_trend_window_min", "min_eligible_runtime_min"))
    adaptation = _section(root, "hysteresis_adaptation", AdaptationSettings, booleans=("enabled_in_shadow", "midday_reassessment", "restore_before_sunset"), nullable=("max_delta_exit_v", "max_delta_enter_v"), optional=("max_delta_exit_v", "max_delta_enter_v"), positive=("expiry_min",))
    anti = _section(root, "anti_flap", AntiFlapSettings, integer=("max_optional_output_commands_per_hour",), positive=("stable_condition_min", "min_mains_dwell_min", "min_battery_dwell_min", "failed_trial_window_min", "failed_trial_lockout_min"))
    safety_names = tuple(SafetySettings.__dataclass_fields__)
    safety = _section(root, "safety", SafetySettings, booleans=safety_names, required_true=safety_names)
    audit_names = tuple(AuditSettings.__dataclass_fields__)
    audit = _section(root, "audit", AuditSettings, booleans=audit_names)
    return OutputPolicyConfig(1, "observe", False, baseline, solar, demand, adaptation, anti, safety, audit)


def _check_yaml_policy(node):
    """Inspect composed nodes before SafeLoader can discard duplicate keys."""
    if not isinstance(node, yaml.MappingNode):
        return
    policy = None
    for key, value in node.value:
        if key.tag == "tag:yaml.org,2002:merge":
            raise OutputPolicyConfigError(f"document: root merge is ambiguous (line {key.start_mark.line + 1})")
        if isinstance(key, yaml.ScalarNode) and key.value == "output_source_policy":
            if policy is not None:
                raise OutputPolicyConfigError(f"output_source_policy: duplicate key (line {key.start_mark.line + 1})")
            policy = value

    def walk(current, path, ancestors):
        if id(current) in ancestors:
            raise OutputPolicyConfigError(f"{path}: recursive YAML alias")
        ancestors = ancestors | {id(current)}
        if isinstance(current, yaml.MappingNode):
            seen = set()
            for key, value in current.value:
                if key.tag == "tag:yaml.org,2002:merge":
                    raise OutputPolicyConfigError(f"{path}: YAML merge is not supported (line {key.start_mark.line + 1})")
                if not isinstance(key, yaml.ScalarNode) or key.tag != "tag:yaml.org,2002:str":
                    raise OutputPolicyConfigError(f"{path}: policy keys must be strings (line {key.start_mark.line + 1})")
                child = f"{path}.{key.value}"
                if key.value in seen:
                    raise OutputPolicyConfigError(f"{child}: duplicate key (line {key.start_mark.line + 1})")
                seen.add(key.value)
                walk(value, child, ancestors)
        elif isinstance(current, yaml.SequenceNode):
            for index, value in enumerate(current.value):
                walk(value, f"{path}[{index}]", ancestors)
    if policy is not None:
        walk(policy, "output_source_policy", set())


def load_output_policy(path: str | Path) -> OutputPolicyConfig | UnconfiguredOutputPolicy:
    """Load YAML without writing or retaining mutable document state."""
    try:
        with open(path, encoding="utf-8") as stream:
            text = stream.read()
        _check_yaml_policy(yaml.compose(text, Loader=yaml.SafeLoader))
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise OutputPolicyConfigError("document: invalid YAML") from exc
    config = parse_output_policy(document)
    if isinstance(config, OutputPolicyConfig) and config.solar_opportunity.timezone is not None:
        try:
            ZoneInfo(config.solar_opportunity.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise OutputPolicyConfigError("output_source_policy.solar_opportunity.timezone: timezone unavailable or nonexistent") from None
    return config
