"""Generic control domain types for dessmonitor.

These types define a shared vocabulary for controllable loads, commands,
state observation, and policy decisions. They are passive data definitions
only and must NOT import any runtime modules (Tuya, DESS, OpenWeather, ML,
service loops, device models, or hardware adapters).

This module uses only Python standard library: dataclasses, enum, typing, uuid, time.
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DesiredState(Enum):
    """The requested target state for a controllable load."""
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"


class ObservedState(Enum):
    """The currently observed state of a controllable load."""
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"


class CommandSource(Enum):
    """Origin of a control command.

    ML_ADVISORY indicates an advisory recommendation from an ML model,
    NOT direct ML control. ML control is deferred until safety-reviewed gates
    are passed (per ADR-0003).
    """
    MANUAL = "manual"
    API = "api"
    POLICY = "policy"
    ML_ADVISORY = "ml_advisory"
    TEST = "test"
    UNKNOWN = "unknown"


@dataclass
class SwitchableLoad:
    """A logical controllable electrical load.

    This is NOT a pump. It represents any device that can be switched ON/OFF
    (and optionally set to a numeric level).
    
    Must NOT contain Tuya command keys as required fields.
    """
    id: str
    name: str
    device_type: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ControlState:
    """Combined desired and observed state snapshot for a load."""
    load_id: str
    desired: DesiredState
    observed: ObservedState
    last_updated: float = field(default_factory=time.time)


@dataclass
class ControlCommand:
    """A command to set a load to a desired state.

    Must NOT execute hardware calls. This is a passive data object.
    """
    target_id: str
    desired_state: DesiredState
    source: CommandSource
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)


@dataclass
class CommandResult:
    """Outcome of executing a ControlCommand."""
    command_id: str
    success: bool
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class TelemetryPoint:
    """A generic timestamped metric reading for any load.

    Does NOT replace the current MLDataPoint schema. Data schema migration
    is deferred to a later PR (PR 0013 per PLATFORM_CONTROL_REDESIGN.md).
    """
    load_id: str
    metric: str
    value: Optional[float] = None
    unit: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class PolicyDecision:
    """A decision produced by a policy engine.

    Must NOT call hardware. Must NOT be enabled in PR 0009.
    The policy engine will be introduced in a later PR.
    """
    load_id: str
    recommended_state: DesiredState
    priority: int = 0
    reason: str = ""
    timestamp: float = field(default_factory=time.time)
