from app.control.domain import (
    SwitchableLoad,
    DesiredState,
    ObservedState,
    ControlState,
    ControlCommand,
    CommandSource,
    CommandResult,
    TelemetryPoint,
    PolicyDecision,
)

from app.control.relay_mapping import (
    relay_channel_to_switchable_load,
    relay_channels_to_switchable_loads,
)

__all__ = [
    "SwitchableLoad",
    "DesiredState",
    "ObservedState",
    "ControlState",
    "ControlCommand",
    "CommandSource",
    "CommandResult",
    "TelemetryPoint",
    "PolicyDecision",
    "relay_channel_to_switchable_load",
    "relay_channels_to_switchable_loads",
]
