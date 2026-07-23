"""Canonical device property mapping for dessmonitor.

Provides typed, immutable mapping between logical devices and Tuya DP
property codes.  Resolved once during initialization by
app.device_initializer and consumed consistently by both
RelayTuyaController and TuyaStatusUpdaterAsync.

Separates:
- control_property (DP code for command submission)
- state_property (DP code for observation)
- command_kind (binary, numeric, none)
- mapping validity and source tracking

No imports of Tuya, hardware, web, or runtime modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# CommandKind — what type of command a device accepts
# ---------------------------------------------------------------------------


class CommandKind(Enum):
    """The type of Tuya command a device accepts.

    binary: boolean true/false (switches, relays).
    numeric: integer value (pump speed).
    none: device receives no commands (sensors, thermometers).
    """
    BINARY = "binary"
    NUMERIC = "numeric"
    NONE = "none"


# ---------------------------------------------------------------------------
# MappingValidity — whether the mapping is usable
# ---------------------------------------------------------------------------


class MappingValidity(Enum):
    """Whether the device property mapping is valid for use.

    valid: both control_property and state_property are configured
           and non-empty.
    invalid: one or both properties are missing, empty, or
             unrecognized.
    unavailable: device is not available (available=false).
    """
    VALID = "valid"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# MappingSource — how the mapping was resolved
# ---------------------------------------------------------------------------


class MappingSource(Enum):
    """Describes how the device property mapping was resolved.

    Used for diagnostics and audit.  Does not affect runtime behavior.
    """
    EXPLICIT_CONFIG = "explicit_config"
    EXPLICIT_CONTROL_KEY = "explicit_control_key"
    EXPLICIT_STATE_KEY = "explicit_state_key"
    MULTI_SWITCH_KEY = "multi_switch_key"
    LEGACY_CHANNEL = "legacy_channel"
    LEGACY_API_SW = "legacy_api_sw"
    PUMP_COMPAT = "pump_compat"
    INFERRED = "inferred"
    FALLBACK_REJECTED = "fallback_rejected"


# ---------------------------------------------------------------------------
# CommandResult — typed result of a command submission
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandResult:
    """Result of a single Tuya command submission.

    success: True when the Tuya SDK returned success=true.
    accepted: True when the command was accepted by the cloud.
        Distinct from physical confirmation.
    error: A safe, controlled error string or None.
        Contains no IDs, keys, or secrets.
    """
    success: bool = False
    accepted: bool = False
    error: str | None = None

    @classmethod
    def ok(cls) -> "CommandResult":
        return cls(success=True, accepted=True, error=None)

    @classmethod
    def rejected(cls, reason: str = "command-rejected") -> "CommandResult":
        return cls(success=False, accepted=False, error=reason)

    @classmethod
    def not_capable(cls) -> "CommandResult":
        return cls(success=False, accepted=False,
                   error="device-not-command-capable")


# ---------------------------------------------------------------------------
# DevicePropertyMapping — immutable mapping resolved once per device
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DevicePropertyMapping:
    """Immutable mapping between a logical device and its Tuya DP properties.

    Resolved once during device initialization.  Consumers MUST NOT
    independently reinterpret configuration fields.

    Attributes:
        control_property: The Tuya DP property code for command
            submission, or None when the device has no command
            capability.
        state_property: The Tuya DP property code for status
            observation, or None when nothing observable is
            configured.
        command_kind: The type of command the device accepts.
        startup_off_value: The command value used during startup
            reset.  False for binary, explicit config value for
            numeric, None for command_kind=none.
        command_capable: True when the device can receive commands
            (command_kind is not none, control_property is set,
            mapping is valid).
        observable: True when the device has a configured state
            property and a valid mapping.
        mapping_validity: Whether this mapping is usable.
        mapping_source: How the mapping was resolved.
        safe_error: A controlled error string for invalid mappings.
            Contains no IDs, keys, or secrets.
    """
    control_property: str | None = None
    state_property: str | None = None
    command_kind: CommandKind = CommandKind.NONE
    startup_off_value: bool | int | None = None
    mapping_validity: MappingValidity = MappingValidity.INVALID
    mapping_source: MappingSource = MappingSource.FALLBACK_REJECTED
    safe_error: str | None = "no-mapping-resolved"

    @property
    def command_capable(self) -> bool:
        """True when the device can receive commands."""
        return (
            self.command_kind != CommandKind.NONE
            and self.control_property is not None
            and self.control_property != ""
            and self.mapping_validity == MappingValidity.VALID
        )

    @property
    def observable(self) -> bool:
        """True when the device has a configured state property."""
        return (
            self.state_property is not None
            and self.state_property != ""
            and self.mapping_validity == MappingValidity.VALID
        )

    @classmethod
    def unavailable_default(cls) -> "DevicePropertyMapping":
        """Create a mapping for an unavailable device."""
        return cls(
            command_kind=CommandKind.NONE,
            mapping_validity=MappingValidity.UNAVAILABLE,
            mapping_source=MappingSource.FALLBACK_REJECTED,
            safe_error="device-unavailable",
        )

    @classmethod
    def invalid_default(cls) -> "DevicePropertyMapping":
        """Create a default invalid mapping."""
        return cls(
            command_kind=CommandKind.NONE,
            mapping_validity=MappingValidity.INVALID,
            mapping_source=MappingSource.FALLBACK_REJECTED,
            safe_error="no-mapping-resolved",
        )

    @classmethod
    def multi_switch_child(cls, sw_key: str) -> "DevicePropertyMapping":
        """Create a mapping for a multi-switch child channel."""
        if not sw_key or not isinstance(sw_key, str) or not sw_key.strip():
            return cls(
                command_kind=CommandKind.BINARY,
                mapping_validity=MappingValidity.INVALID,
                mapping_source=MappingSource.MULTI_SWITCH_KEY,
                safe_error="empty-switch-key",
            )
        return cls(
            control_property=sw_key.strip(),
            state_property=sw_key.strip(),
            command_kind=CommandKind.BINARY,
            startup_off_value=False,
            mapping_validity=MappingValidity.VALID,
            mapping_source=MappingSource.MULTI_SWITCH_KEY,
            safe_error=None,
        )

    @classmethod
    def single_switch(
        cls,
        control_key: str | None = None,
        channel: str | None = None,
        api_sw: str | None = None,
        state_key: str | None = None,
    ) -> "DevicePropertyMapping":
        """Create a mapping for a single switch device.

        Precedence: control_key > channel > api_sw.
        No universal switch_1 fallback.
        """
        cp = control_key or channel or api_sw
        if not cp:
            return cls(
                command_kind=CommandKind.BINARY,
                mapping_validity=MappingValidity.INVALID,
                mapping_source=MappingSource.FALLBACK_REJECTED,
                safe_error="no-control-property",
            )
        sp = state_key or cp
        src = MappingSource.EXPLICIT_CONFIG
        if control_key:
            src = MappingSource.EXPLICIT_CONTROL_KEY
        elif channel:
            src = MappingSource.LEGACY_CHANNEL
        elif api_sw:
            src = MappingSource.LEGACY_API_SW
        return cls(
            control_property=cp.strip(),
            state_property=sp.strip(),
            command_kind=CommandKind.BINARY,
            startup_off_value=False,
            mapping_validity=MappingValidity.VALID,
            mapping_source=src,
            safe_error=None,
        )

    @classmethod
    def pump_device(
        cls,
        control_key: str | None = None,
        channel: str | None = None,
        api_sw: str | None = None,
        state_key: str | None = None,
        p_code: str | None = None,
    ) -> "DevicePropertyMapping":
        """Create a mapping for a pump device.

        Explicit control_key/channel/api_sw take precedence.
        Bounded compat rule: fall back to "P"/"Power".
        """
        cp = control_key or channel or api_sw or "P"
        sp = state_key or p_code or "Power"
        # When using compat defaults, state is always Power
        if not state_key and not p_code:
            sp = "Power"
        src = MappingSource.PUMP_COMPAT if not (control_key or channel or api_sw) else (
            MappingSource.EXPLICIT_CONTROL_KEY if control_key else MappingSource.LEGACY_CHANNEL if channel else MappingSource.LEGACY_API_SW
        )
        return cls(
            control_property=cp.strip(),
            state_property=sp.strip(),
            command_kind=CommandKind.NUMERIC,
            startup_off_value=None,  # numeric: no default startup value
            mapping_validity=MappingValidity.VALID,
            mapping_source=src,
            safe_error=None,
        )

    @classmethod
    def sensor_device(
        cls,
        state_key: str | None = None,
        channel: str | None = None,
        api_sw: str | None = None,
    ) -> "DevicePropertyMapping":
        """Create a mapping for an observation-only sensor.

        Sensors have command_kind=none — they receive NO commands.
        A state_property may exist for observation.
        """
        sp = state_key or channel or api_sw
        valid = sp is not None and sp != ""
        return cls(
            control_property=None,
            state_property=sp.strip() if sp else None,
            command_kind=CommandKind.NONE,
            startup_off_value=None,
            mapping_validity=MappingValidity.VALID if valid else MappingValidity.INVALID,
            mapping_source=MappingSource.INFERRED,
            safe_error=None if valid else "no-state-property",
        )

    @classmethod
    def inferred_device(
        cls,
        control_key: str | None = None,
        state_key: str | None = None,
    ) -> "DevicePropertyMapping":
        """Create a mapping for an unknown device type (conservative).

        Assumes binary command kind.  If no properties are provided,
        the mapping is invalid.
        """
        cp = control_key or None
        sp = state_key or cp
        if not cp:
            return cls(
                command_kind=CommandKind.BINARY,
                mapping_validity=MappingValidity.INVALID,
                mapping_source=MappingSource.INFERRED,
                safe_error="unknown-device-no-properties",
            )
        return cls(
            control_property=cp.strip(),
            state_property=sp.strip() if sp else None,
            command_kind=CommandKind.BINARY,
            startup_off_value=False,
            mapping_validity=MappingValidity.VALID if sp else MappingValidity.INVALID,
            mapping_source=MappingSource.INFERRED,
            safe_error=None if sp else "no-state-property",
        )
