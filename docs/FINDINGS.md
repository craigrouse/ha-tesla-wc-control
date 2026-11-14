# Findings: Gen 3 Wall Connector over Tesla's Fleet API

Tested 2026-11-14 on one Gen 3 Wall Connector (UK, single phase, 32 A,
firmware as of November 2026) on an energy site with Powerwall 3s. Serial
numbers, site ids and VINs below are placeholders.

## Request shape

All requests:

```
POST /api/1/energy_sites/{site_id}/command
```

```json
{
  "command_type": "grpc_command",
  "command_properties": {
    "message": {"wc": {"<message>_request": { ... }}},
    "identifier_type": 4,
    "target_id": "<WALL_CONNECTOR_DIN>"
  }
}
```

- Message names come from `WCMessages` in
  [Teslemetry/tesla-protocol `proto/energy_device/wall_connector.proto`](https://github.com/Teslemetry/tesla-protocol).
- `identifier_type: 4` is `EnergyDeviceIdentifierType.WALL_CONNECTOR_DIN`.
  `target_id` is required: without it the message goes to the site gateway,
  not the Wall Connector.
- The reply wraps the connector's protobuf response, e.g.
  `{"response": {"request_id": "...", "message": {"Payload": {"Wc": {"Message": {"ConfigureChargeScheduleResponse": {"error": 1}}}}}}}`.
  The response key is the PascalCase message name with `Response` in place of `_request`.

## Relayed by Tesla's gateway

### `configure_charge_schedule_request`: works

```json
{"config": {
   "schedule": {"day_time_periods": [{"day_bitmask": 127, "time_periods": [
     {"start_seconds": 84600, "end_seconds": 86400},
     {"start_seconds": 0, "end_seconds": 19800}]}]},
   "delay": {"max_delay_seconds": 0},
   "enable_schedule": true},
 "time_zone": {"time_zone_id": "Europe/London",
   "time_zone_info": {"transitions": [{"local_time_utc_offset": 0, "timestamp": {"nanos": 0, "seconds": 0}}]}}}
```

- Reply `ConfigureChargeScheduleResponse.error`: 1 = `NONE` (applied).
- A single period with end before start (`84600 → 19800`) returns error 4
  (`INTERNAL`). Split at midnight as above, it is accepted.
- `day_bitmask` bits (`WCChargeScheduleDay`): Sunday 1, Monday 2, Tuesday 4,
  Wednesday 8, Thursday 16, Friday 32, Saturday 64.
- Enabling the schedule stopped an active non-Tesla charge within about 30 s.
  Disabling it let the charge start within about 30 s. The schedule applies to
  every vehicle, Teslas included.

### `push_charge_command_request`: works

```json
{"charge_command": {"charging_command": 2}}
```

- `2` = STOP: the contactor opened within about 30 s. `1` = START: it closed
  again within about 30 s.
- Reply: `PushChargeCommandResponse: {}`.
- START overrides the schedule for the current session: after START,
  re-enabling the schedule did not stop the charge, but STOP did.

### `configure_third_party_vehicle_mode_request`: works, checked at plug-in

```json
{"third_party_vehicle_mode": 2}
```

- `WCThirdPartyVehicleMode`: 1 `ENABLED` (any EV), 2 `DISABLED` (Tesla only),
  3 `DISABLED_NO_ROADSTER`, 4 `VIN_ALLOW_LIST`, 5 `COMPATIBILITY`.
- Reply `ConfigureThirdPartyVehicleModeResponse: {}`, and `get_config` then
  shows the new mode.
- The mode is checked when a car is plugged in. Tested with a non-Tesla car:
  - Mode 2 set while it was charging: the charge carried on.
  - Re-plugged with mode 2: refused. The connector stayed in `negotiating`
    with the contactor open for over 6 minutes, including about 3 minutes
    after switching back to mode 1 without re-plugging.
  - Re-plugged with mode 1: charged.
  So a change applies from the next plug-in, in both directions.

### `get_config_request`: works

Reply `GetConfigResponse` includes `settings` (`max_output_current_amps`,
`gmi_mode`, `country`, `third_party_vehicle_mode`), `wifi_config`, `wifi`
(addresses, connectivity, RSSI), `charge_schedule` and `time_source`.

### `get_access_control_settings_request`: works

Reply `GetAccessControlSettingsResponse.entries: [{"vin": {"value": "<VIN>"}, "name": "<name>", "model": 4, "model_year": 2022}]`.
This is the allow list used by mode 4 (`VIN_ALLOW_LIST`, "authorised Teslas").
Changing it (`configure_access_control_settings_request`, operation
ADD/REMOVE/CLEAR_ALL/RENAME) has not been tested.

### `get_recent_vehicles_request`: works

Reply `GetRecentVehiclesResponse.recent_vehicles: [{"value": "<VIN>"}]`.

## Blocked by Tesla's gateway

Each returns **HTTP 400 "The request body is not valid"** (tesla_fleet_api
`InvalidRequest`):

| Message | Would allow |
|---|---|
| `configure_settings_request` (`settings.max_output_current_amps`, sent back with the other read settings unchanged) | Changing max current after commissioning |
| `get_provisional_operational_params_request` / `set_provisional_operational_params_request` | Temporary current limit with timeout, `inhibit_charging` |
| `push_smart_charging_command_request` (`ttl_seconds`, `charge_disallowed`, `target_charge_power_params`) | Dynamic target power / current |
| `get_vitals_request` | Live status (also available locally from the Wall Connector's own API) |

## Teslemetry

Through `https://<region>.teslemetry.com/api/1/energy_sites/{id}/command`,
the schedule body above (which works straight to Tesla) returns the same
HTTP 400 "The request body is not valid".

In `tesla_fleet_api` 1.23.1, `EnergySite._unsigned_command` sends
`identifier_type` but never `target_id`, so the library cannot address a Wall
Connector yet.

Suggested support:

1. `target_id` in unsigned energy commands.
2. Schedule, start/stop and vehicle-access commands with Home Assistant
   actions or entities (switch for schedule, buttons for start/stop, select for
   access mode).
3. Whatever Teslemetry's proxy needs to relay `wc` messages with a `target_id`.
