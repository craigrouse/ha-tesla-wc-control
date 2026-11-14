"""Tesla Wall Connector Control: control a Gen 3 Wall Connector through Tesla's Fleet API.

Sends undocumented energy-site `grpc_command` messages (WCMessages from
Teslemetry/tesla-protocol wall_connector.proto) to one Wall Connector,
addressed by its DIN. It borrows the cloud client of an existing Home
Assistant integration, preferring core `tesla_fleet` and falling back to
`teslemetry`, so it needs no login of its own. It reads those integrations'
internals (entry.runtime_data.energysites), so re-check it after updates
that change them.
"""

from __future__ import annotations

from datetime import time
import logging
from typing import Any

from tesla_fleet_api.const import Method
from tesla_fleet_api.exceptions import TeslaFleetError
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import DOMAIN, TESLA_FLEET_DOMAIN, TESLEMETRY_DOMAIN
from .http import TeslaPublicKeyView
from .payload import (
    ALL_DAYS,
    SCHEDULE_ERROR_NONE,
    SCHEDULE_ERRORS,
    VEHICLE_ACCESS_MODES,
    VEHICLE_ACCESS_NAMES,
    build_charge_command,
    build_command,
    build_schedule,
    find_key,
    find_schedule_error,
    response_key,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

TARGET_SCHEMA = vol.Schema({vol.Optional("wall_connector"): cv.string})
SET_SCHEDULE_SCHEMA = TARGET_SCHEMA.extend(
    {
        vol.Required("enabled"): cv.boolean,
        vol.Optional("start", default=time(23, 30)): cv.time,
        vol.Optional("end", default=time(5, 30)): cv.time,
        vol.Optional("day_bitmask", default=ALL_DAYS): vol.All(vol.Coerce(int), vol.Range(min=1, max=127)),
        vol.Optional("dry_run", default=False): cv.boolean,
    }
)
VEHICLE_ACCESS_SCHEMA = TARGET_SCHEMA.extend({vol.Required("mode"): vol.In(list(VEHICLE_ACCESS_MODES))})
RAW_SCHEMA = TARGET_SCHEMA.extend(
    {
        vol.Required("message"): vol.All(cv.string, vol.Match(r"^[a-z_]+_request$")),
        vol.Optional("params", default={}): dict,
    }
)

Target = tuple[Any, str, int, str]  # (cloud client, transport, site id, DIN)


def _cloud_sites(hass: HomeAssistant, domain: str) -> dict[int, Any]:
    """{site id: cloud energy-site client} for a loaded tesla_fleet or teslemetry entry."""
    sites: dict[int, Any] = {}
    for entry in hass.config_entries.async_entries(domain):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        for site in entry.runtime_data.energysites:
            # With Teslemetry local Powerwall control, api is an EnergySiteRouter
            # whose backends are (local, cloud); the Wall Connector is only
            # reachable through the cloud client, the one with Fleet-style _request.
            api = site.api
            cloud = api if hasattr(api, "_request") else next(
                (b for b in getattr(api, "_backends", ()) if hasattr(b, "_request")), None
            )
            if cloud is not None:
                sites[site.id] = cloud
    return sites


def _wall_connector_dins(hass: HomeAssistant) -> list[tuple[int, str]]:
    """(site id, DIN) for every Wall Connector device under a Tesla energy site.

    Both integrations register Wall Connectors as devices whose identifier is
    the DIN, linked (via_device) to the energy site device whose identifier is
    the numeric site id.
    """
    dev_reg = dr.async_get(hass)
    found: set[tuple[int, str]] = set()
    for domain in (TESLA_FLEET_DOMAIN, TESLEMETRY_DOMAIN):
        for entry in hass.config_entries.async_entries(domain):
            devices = dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
            sites = {
                d.id: int(value)
                for d in devices
                for ident_domain, value in d.identifiers
                if ident_domain == domain and value.isdigit()
            }
            for device in devices:
                if device.via_device_id not in sites or "Wall Connector" not in (device.model or ""):
                    continue
                for ident_domain, din in device.identifiers:
                    if ident_domain == domain:
                        found.add((sites[device.via_device_id], din))
    return sorted(found)


def _wall_connectors(hass: HomeAssistant) -> list[Target]:
    """Every Wall Connector with a client to reach it, preferring tesla_fleet.

    Teslemetry currently answers HTTP 400 "The request body is not valid" to
    these commands, so it is only the fallback.
    """
    fleet = _cloud_sites(hass, TESLA_FLEET_DOMAIN)
    teslemetry = _cloud_sites(hass, TESLEMETRY_DOMAIN)
    found: list[Target] = []
    for site_id, din in _wall_connector_dins(hass):
        if site_id in fleet:
            found.append((fleet[site_id], TESLA_FLEET_DOMAIN, site_id, din))
        elif site_id in teslemetry:
            found.append((teslemetry[site_id], TESLEMETRY_DOMAIN, site_id, din))
    return found


def _targets(hass: HomeAssistant, call: ServiceCall) -> list[Target]:
    targets = _wall_connectors(hass)
    if wanted := call.data.get("wall_connector"):
        targets = [t for t in targets if t[3] == wanted]
    if not targets:
        raise HomeAssistantError("No Wall Connector found on a loaded Tesla Fleet or Teslemetry energy site")
    return targets


async def _post(target: Target, payload: dict[str, Any]) -> Any:
    """Send one grpc_command to one Wall Connector; raise HomeAssistantError if refused."""
    cloud, transport, site_id, din = target
    message = next(iter(payload["command_properties"]["message"]["wc"]))
    try:
        response = await cloud._request(  # noqa: SLF001 - no public method sends a DIN-targeted grpc_command
            Method.POST, f"api/1/energy_sites/{site_id}/command", json=payload
        )
    except TeslaFleetError as err:  # BaseException subclass, so it must be named
        raise HomeAssistantError(f"{transport} refused {message} for {din}: {err.message}") from err
    _LOGGER.debug("%s response for %s: %s", message, din, response)
    return response


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions and the public key view."""

    async def set_schedule(call: ServiceCall) -> ServiceResponse:
        """The Wall Connector's own charge schedule, as in the Tesla app."""
        offset = dt_util.now().utcoffset()
        results: dict[str, Any] = {}
        for target in _targets(hass, call):
            _, transport, site_id, din = target
            payload = build_schedule(
                din=din,
                enabled=call.data["enabled"],
                start=call.data["start"],
                end=call.data["end"],
                day_bitmask=call.data["day_bitmask"],
                time_zone_id=hass.config.time_zone,
                utc_offset_seconds=int(offset.total_seconds()) if offset else 0,
            )
            if call.data["dry_run"]:
                results[din] = {"site_id": site_id, "transport": transport, "payload": payload}
                continue
            response = await _post(target, payload)
            code = find_schedule_error(response)
            if code is not None and code != SCHEDULE_ERROR_NONE:
                raise HomeAssistantError(
                    f"Wall Connector {din} rejected the schedule: {SCHEDULE_ERRORS.get(code, code)} ({response})"
                )
            results[din] = {"transport": transport, "connector_result": SCHEDULE_ERRORS.get(code, "not reported")}
        return results if call.return_response else None

    def charge_command(start: bool):
        async def handler(call: ServiceCall) -> ServiceResponse:
            """START or STOP the session on the connector (push_charge_command_request).

            START overrides the schedule for the current session; STOP ends it.
            """
            results: dict[str, Any] = {}
            message = "push_charge_command_request"
            for target in _targets(hass, call):
                response = await _post(target, build_charge_command(target[3], start))
                results[target[3]] = {
                    "transport": target[1],
                    "acknowledged": find_key(response, response_key(message)) is not None,
                }
            return results if call.return_response else None

        return handler

    async def set_vehicle_access(call: ServiceCall) -> ServiceResponse:
        """Tesla app access control: any vehicle / Tesla only / authorised Teslas / compatibility."""
        mode = VEHICLE_ACCESS_MODES[call.data["mode"]]
        message = "configure_third_party_vehicle_mode_request"
        results: dict[str, Any] = {}
        for target in _targets(hass, call):
            response = await _post(target, build_command(target[3], message, {"third_party_vehicle_mode": mode}))
            results[target[3]] = {
                "transport": target[1],
                "acknowledged": find_key(response, response_key(message)) is not None,
            }
        return results if call.return_response else None

    async def get_status(call: ServiceCall) -> ServiceResponse:
        """Read-only: settings (access mode, schedule, max current), VIN allow list and recently seen vehicles."""
        results: dict[str, Any] = {}
        for target in _targets(hass, call):
            entry: dict[str, Any] = {"site_id": target[2], "transport": target[1]}
            for message, key in (
                ("get_config_request", "config"),
                ("get_access_control_settings_request", "access_list"),
                ("get_recent_vehicles_request", "recent_vehicles"),
            ):
                try:
                    response = await _post(target, build_command(target[3], message))
                except HomeAssistantError as err:
                    entry[f"{key}_error"] = str(err)
                    continue
                entry[key] = find_key(response, response_key(message))
            settings = (entry.get("config") or {}).get("settings") or {}
            if isinstance(mode := settings.get("third_party_vehicle_mode"), int):
                entry["vehicle_access"] = VEHICLE_ACCESS_NAMES.get(mode, mode)
            results[target[3]] = entry
        return results

    async def send_raw(call: ServiceCall) -> ServiceResponse:
        """Advanced: send any WCMessages request by name, e.g. to test what Tesla's gateway relays."""
        message = call.data["message"]
        results: dict[str, Any] = {}
        for target in _targets(hass, call):
            try:
                response = await _post(target, build_command(target[3], message, call.data["params"]))
            except HomeAssistantError as err:
                results[target[3]] = {"transport": target[1], "error": str(err)}
                continue
            results[target[3]] = {"transport": target[1], "reply": find_key(response, response_key(message)), "response": response}
        return results

    optional = SupportsResponse.OPTIONAL
    hass.services.async_register(DOMAIN, "set_schedule", set_schedule, schema=SET_SCHEDULE_SCHEMA, supports_response=optional)
    hass.services.async_register(DOMAIN, "start_charging", charge_command(True), schema=TARGET_SCHEMA, supports_response=optional)
    hass.services.async_register(DOMAIN, "stop_charging", charge_command(False), schema=TARGET_SCHEMA, supports_response=optional)
    hass.services.async_register(DOMAIN, "set_vehicle_access", set_vehicle_access, schema=VEHICLE_ACCESS_SCHEMA, supports_response=optional)
    hass.services.async_register(DOMAIN, "get_status", get_status, schema=TARGET_SCHEMA, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "send_raw", send_raw, schema=RAW_SCHEMA, supports_response=SupportsResponse.ONLY)

    hass.http.register_view(TeslaPublicKeyView())
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Nothing to set up per entry; the entry only makes the integration load from the UI."""
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return True
