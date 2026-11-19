"""Narrow, disabled-by-default live transport for Output Source Priority.

This module is intentionally not wired into runtime startup. It does not call
DessAPI.ctrl_device(); the only write request it can construct is the exact
``los_output_source_priority`` ctrlDevice action with an approved catalogue
key. Unit tests inject a request function and never contact DESS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.control.easun_output_mapping import OutputField


API_BASE = "https://api.dessmonitor.com/public/"
_READ_ACTIONS = frozenset({"queryDeviceCtrlField", "queryDeviceCtrlValue"})
_PRIORITY_ID = OutputField.PRIORITY.value


class LiveTransportRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class CatalogueRead:
    catalogue: dict[str, Any]
    received_at: datetime


@dataclass(frozen=True)
class SettingRead:
    value: str
    received_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LiveDessTransport:
    """A production-shaped transport with a single explicit write surface.

    ``request`` is injectable only for deterministic tests. The default path
    uses the existing signed request implementation with the pinned endpoint.
    No request is made while constructing this object.
    """

    def __init__(self, api: object, *, enabled: bool = False,
                 request: Callable[[dict[str, Any]], dict[str, Any]] | None = None):
        if type(enabled) is not bool:
            raise TypeError("enabled must be bool")
        if not hasattr(api, "_do_api_request"):
            raise TypeError("approved DessAPI instance required")
        self.api = api
        self.enabled = enabled
        self._request_override = request
        self._advertised_priority_keys: frozenset[str] | None = None
        self._advertised_utility_key: str | None = None

    def _identity(self) -> dict[str, Any]:
        names = ("pn", "dev_code", "dev_addr", "sn")
        values = {name: getattr(self.api, name, None) for name in names}
        if any(not isinstance(value, (str, int)) or isinstance(value, bool) or value == ""
               for value in values.values()):
            raise LiveTransportRefused("device identity unavailable")
        return values

    def _request(self, params: dict[str, Any], *, write: bool = False) -> dict[str, Any]:
        if write and self.enabled is not True:
            raise LiveTransportRefused("live transport disabled")
        if params.get("action") not in _READ_ACTIONS | {"ctrlDevice"}:
            raise LiveTransportRefused("unsupported DESS action")
        if params.get("action") == "ctrlDevice":
            if set(params) != {"action", "pn", "devcode", "devaddr", "sn", "id", "val"}:
                raise LiveTransportRefused("invalid output-priority request")
            if params.get("id") != _PRIORITY_ID or not isinstance(params.get("val"), str):
                raise LiveTransportRefused("output-priority field required")
        if self._request_override is not None:
            response = self._request_override(dict(params))
        else:
            try:
                response = self.api._do_api_request(
                    dict(params), need_auth=True, base_url=API_BASE,
                    transport="utility-hold", reject_redirects=True,
                )
            except Exception as exc:
                raise LiveTransportRefused("DESS request failed") from exc
        if not isinstance(response, dict) or response.get("err") != 0:
            raise LiveTransportRefused("invalid DESS response")
        return response

    def _common(self) -> dict[str, Any]:
        identity = self._identity()
        return {"pn": identity["pn"], "devcode": identity["dev_code"],
                "devaddr": identity["dev_addr"], "sn": identity["sn"],
                "i18n": "en_US"}

    async def read_catalogue(self) -> CatalogueRead:
        response = self._request({"action": "queryDeviceCtrlField", **self._common()})
        fields = response.get("dat", {}).get("field") if isinstance(response.get("dat"), dict) else None
        if not isinstance(fields, list):
            raise LiveTransportRefused("catalogue unavailable")
        selected = next((field for field in fields if isinstance(field, dict)
                         and field.get("id") == _PRIORITY_ID), None)
        if selected is None or not isinstance(selected.get("item"), list):
            raise LiveTransportRefused("output-priority catalogue field unavailable")
        items = selected["item"]
        keys = {item.get("key") for item in items
                if isinstance(item, dict) and isinstance(item.get("key"), str)}
        self._advertised_priority_keys = frozenset(keys)
        utility_keys = {item.get("key") for item in items
                        if isinstance(item, dict) and item.get("val") == "Utility"
                        and isinstance(item.get("key"), str)}
        self._advertised_utility_key = next(iter(utility_keys)) if len(utility_keys) == 1 else None
        return CatalogueRead({"id": selected["id"], "item": list(selected["item"])}, _now())

    async def read_setting(self) -> SettingRead:
        response = self._request({"action": "queryDeviceCtrlValue", **self._common(),
                                  "id": _PRIORITY_ID})
        data = response.get("dat")
        if not isinstance(data, dict) or data.get("id") != _PRIORITY_ID or not isinstance(data.get("val"), str):
            raise LiveTransportRefused("output-priority readback unavailable")
        return SettingRead(data["val"], _now())

    async def write_output_priority(self, key: str) -> None:
        if not isinstance(key, str) or not key:
            raise LiveTransportRefused("invalid output-priority key")
        if self._advertised_priority_keys is None or key not in self._advertised_priority_keys:
            raise LiveTransportRefused("priority key was not advertised")
        if key != self._advertised_utility_key:
            raise LiveTransportRefused("only the advertised Utility key is writable")
        common = self._common()
        common.pop("i18n", None)
        self._request({"action": "ctrlDevice", **common,
                       "id": _PRIORITY_ID, "val": key}, write=True)


__all__ = ["API_BASE", "CatalogueRead", "LiveDessTransport", "LiveTransportRefused", "SettingRead"]
