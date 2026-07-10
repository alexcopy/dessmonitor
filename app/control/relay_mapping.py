"""Adapter mapping from relay-channel-shaped objects to SwitchableLoad.

This module provides pure data transformation functions. It does NOT import
RelayChannelDevice, Tuya adapters, or any runtime service code. It relies on
a duck-typed Protocol to define the expected relay object shape.

Must NOT:
- Execute hardware calls
- Read environment variables
- Read config files
- Open network connections
- Mutate files
"""

from typing import Any, Iterable, Optional, Protocol

from app.control.domain import SwitchableLoad


class RelayChannelLike(Protocol):
    """Duck-typed protocol for objects that can be mapped to SwitchableLoad.

    This is NOT RelayChannelDevice. It describes the minimal attribute surface
    expected by the mapping functions.
    """
    id: str
    name: str
    desc: str
    device_type: str
    control_key: Optional[str] = None
    state_key: Optional[str] = None
    extra: Optional[dict] = None


_ANALOG_HINTS = {
    "thermo", "thermometer", "termo", "temp_sensor",
    "watertemp", "water_thermo",
}


def _control_kind(device_type: Optional[str]) -> str:
    """Return 'discrete' for ON/OFF devices, 'analog' for numeric sensors."""
    if device_type and device_type.lower() in _ANALOG_HINTS:
        return "analog"
    return "discrete"


def relay_channel_to_switchable_load(relay: object) -> SwitchableLoad:
    """Map a single relay-channel-shaped object to SwitchableLoad.

    Args:
        relay: An object with id, name, desc, device_type attributes
               (duck-typed — does not require RelayChannelDevice).

    Returns:
        A SwitchableLoad instance with safe metadata extracted from the relay.

    Raises:
        ValueError: If the relay has neither id nor name.
    """
    relay_id: Optional[str] = getattr(relay, "id", None)
    relay_name: Optional[str] = getattr(relay, "name", None)

    if not relay_id and not relay_name:
        raise ValueError(
            "Cannot map relay to SwitchableLoad: "
            "relay object has neither 'id' nor 'name' attribute"
        )

    load_id: str = str(relay_id) if relay_id else str(relay_name)
    display_name: str = str(relay_name) if relay_name else load_id
    device_type: Optional[str] = getattr(relay, "device_type", None)
    device_type_str: Optional[str] = str(device_type) if device_type is not None else None

    # Build metadata with safe, non-secret hints
    metadata: dict[str, Any] = {}
    metadata["legacy_device_type"] = device_type_str
    metadata["source_class"] = "RelayChannelDevice"

    control_key = getattr(relay, "control_key", None)
    state_key = getattr(relay, "state_key", None)
    metadata["has_control_key"] = control_key is not None
    metadata["has_state_key"] = state_key is not None
    metadata["control_kind"] = _control_kind(device_type_str)

    desc: Optional[str] = getattr(relay, "desc", None)
    if desc:
        metadata["legacy_desc"] = str(desc)

    extra: Optional[dict] = getattr(relay, "extra", None)
    if extra and isinstance(extra, dict):
        # Copy only a few safe keys — never the full extra dict
        safe_extra_keys = {"switch_time", "min_trashhold"}
        for key in safe_extra_keys:
            if key in extra:
                metadata[key] = extra[key]

    return SwitchableLoad(
        id=load_id,
        name=display_name,
        device_type=device_type_str,
        metadata=metadata,
    )


def relay_channels_to_switchable_loads(
    relays: Iterable[object],
) -> list[SwitchableLoad]:
    """Map multiple relay-channel-shaped objects to SwitchableLoad instances.

    Args:
        relays: Iterable of objects with id, name, desc, device_type attributes.

    Returns:
        List of SwitchableLoad instances in input order.

    Raises:
        ValueError: If any entry is invalid (no id and no name).
    """
    return [relay_channel_to_switchable_load(r) for r in relays]
