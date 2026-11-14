"""Wall Connector gRPC request/response helpers. Pure Python, no Home Assistant imports.

Message names, fields and enums come from Teslemetry/tesla-protocol
proto/energy_device/wall_connector.proto (the WCMessages oneof). The schedule
request shape was first published by TheBarnHome/tesla-wc3-scheduller (MIT).
Tesla does not document these commands.
"""

from __future__ import annotations

from datetime import time
from typing import Any

# EnergyDeviceIdentifierType.WALL_CONNECTOR_DIN in tesla_fleet_api.
IDENTIFIER_TYPE_WALL_CONNECTOR_DIN = 4

# WCChargeScheduleError: NONE (1) is success, INVALID is 0.
SCHEDULE_ERROR_NONE = 1
SCHEDULE_ERRORS = {
    0: "INVALID",
    1: "NONE",
    2: "NO_INTERNET",
    3: "NON_VOLATILE_DATA_READ_WRITE_FAIL",
    4: "INTERNAL",
}

# WCChargeScheduleDay bits: Sunday 1, Monday 2, ... Saturday 64.
ALL_DAYS = 127
DAY_SECONDS = 86400

# WCChargingCommand.
CHARGING_COMMAND_START = 1
CHARGING_COMMAND_STOP = 2

# WCThirdPartyVehicleMode, named after the Tesla app's access-control options.
VEHICLE_ACCESS_MODES = {
    "any_vehicle": 1,  # ENABLED: any EV with a matching connector
    "tesla_only": 2,  # DISABLED: non-Tesla vehicles blocked
    "authorised_teslas": 4,  # VIN_ALLOW_LIST
    "compatibility": 5,  # COMPATIBILITY
}
VEHICLE_ACCESS_NAMES = {value: name for name, value in VEHICLE_ACCESS_MODES.items()} | {
    3: "tesla_only_no_roadster",
}


def response_key(message_name: str) -> str:
    """Response key as Tesla's gateway returns it: configure_x_request -> ConfigureXResponse."""
    base = message_name.removesuffix("_request")
    return "".join(part.capitalize() for part in base.split("_")) + "Response"


def build_command(din: str, message_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Body for POST api/1/energy_sites/{id}/command, addressed to one Wall Connector by DIN."""
    return {
        "command_type": "grpc_command",
        "command_properties": {
            "message": {"wc": {message_name: params or {}}},
            "identifier_type": IDENTIFIER_TYPE_WALL_CONNECTOR_DIN,
            "target_id": din,
        },
    }


def build_charge_command(din: str, start: bool) -> dict[str, Any]:
    """push_charge_command_request: START or STOP the session on the connector."""
    command = CHARGING_COMMAND_START if start else CHARGING_COMMAND_STOP
    return build_command(din, "push_charge_command_request", {"charge_command": {"charging_command": command}})


def find_key(data: Any, key: str) -> Any:
    """First value stored under `key` anywhere in a nested response, or None."""
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = find_key(value, key)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_key(item, key)
            if found is not None:
                return found
    return None


def seconds_since_midnight(value: time) -> int:
    """Seconds since local midnight, as the connector expects."""
    return value.hour * 3600 + value.minute * 60 + value.second


def time_periods(start: time, end: time) -> list[dict[str, int]]:
    """One period, or two when the window crosses midnight.

    The connector answers INTERNAL (error 4) to a single period whose end is
    before its start (e.g. 23:30-05:30), so an overnight window is split at
    midnight.
    """
    start_s, end_s = seconds_since_midnight(start), seconds_since_midnight(end)
    if start_s < end_s:
        return [{"start_seconds": start_s, "end_seconds": end_s}]
    periods = [{"start_seconds": start_s, "end_seconds": DAY_SECONDS}]
    if end_s > 0:
        periods.append({"start_seconds": 0, "end_seconds": end_s})
    return periods


def build_schedule(
    *,
    din: str,
    enabled: bool,
    start: time,
    end: time,
    day_bitmask: int,
    time_zone_id: str,
    utc_offset_seconds: int,
) -> dict[str, Any]:
    """configure_charge_schedule_request.

    The connector reads the periods in the declared time zone, so local times
    with the zone's current offset give the same hours the Tesla app shows.
    A period is always sent, even when disabling: the connector rejects an
    empty list.
    """
    return build_command(
        din,
        "configure_charge_schedule_request",
        {
            "config": {
                "schedule": {
                    "day_time_periods": [
                        {"day_bitmask": day_bitmask, "time_periods": time_periods(start, end)}
                    ]
                },
                "delay": {"max_delay_seconds": 0},
                "enable_schedule": enabled,
            },
            "time_zone": {
                "time_zone_id": time_zone_id,
                "time_zone_info": {
                    "transitions": [
                        {"local_time_utc_offset": utc_offset_seconds, "timestamp": {"nanos": 0, "seconds": 0}}
                    ]
                },
            },
        },
    )


def find_schedule_error(data: Any) -> int | None:
    """The connector's own result code for a schedule command, or None if absent.

    HTTP 200 only means Tesla relayed the command; the connector answers with
    ConfigureChargeScheduleResponse.error, where 1 (NONE) is success.
    """
    response = find_key(data, "ConfigureChargeScheduleResponse")
    if isinstance(response, dict) and isinstance(response.get("error"), int):
        return response["error"]
    return None
