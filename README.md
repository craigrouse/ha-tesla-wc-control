# Tesla Wall Connector Control for Home Assistant

Control a **Gen 3 Tesla Wall Connector** from Home Assistant through Tesla's
cloud: its charge schedule, starting and stopping a charge, and its vehicle
access setting. These are things the Tesla app can do but no Home Assistant
integration exposes yet.

It sends undocumented energy-site `grpc_command` messages to the Wall
Connector through Tesla's Fleet API, using the login of an integration you
already have. It adds no login of its own.

> **Status:** experimental, personal project. Tested on one Gen 3 Wall
> Connector (UK, single phase, firmware as of November 2026) on a site with
> Powerwalls. Tesla does not document these commands and could change or
> block them at any time.

## What works and what doesn't

Tested through Tesla's Fleet API (core `tesla_fleet` integration). "Blocked"
means Tesla's gateway answers **HTTP 400 "The request body is not valid"**
before the Wall Connector sees the message.

| Feature | Message | Result |
|---|---|---|
| Charge schedule on/off and window | `configure_charge_schedule_request` | **Works.** Applies to every vehicle. A window crossing midnight must be sent as two periods (a single 23:30–05:30 period gets connector error 4, `INTERNAL`); this integration splits it for you |
| Start / stop charging | `push_charge_command_request` | **Works.** STOP ends the charge within seconds, START resumes it. START overrides the schedule for the current session |
| Read settings | `get_config_request` | **Works.** Max current, country, ground monitoring mode, vehicle access mode, Wi-Fi, schedule |
| Recently seen vehicles | `get_recent_vehicles_request` | **Works.** Returns VINs |
| Vehicle access (any EV / Tesla only / authorised Teslas / compatibility) | `configure_third_party_vehicle_mode_request` | **Works, at plug-in.** With Tesla only set, a re-plugged non-Tesla car was refused; with any vehicle set, it charged. Changing the mode does not affect a car that is already plugged in, either way |
| Max output current | `configure_settings_request` | **Blocked** |
| Temporary current limit / charging inhibit | `get/set_provisional_operational_params_request` | **Blocked** |
| Smart charging target power / current with TTL | `push_smart_charging_command_request` | **Blocked** |
| Live vitals | `get_vitals_request` | **Blocked** |

Through **Teslemetry**, even the schedule command currently returns the same
HTTP 400, so in practice this needs the core **Tesla Fleet** integration. If
Teslemetry adds support, this integration already falls back to it. See
[docs/FINDINGS.md](docs/FINDINGS.md) for the request bodies and replies.

## Actions

| Action | What it does |
|---|---|
| `tesla_wc_control.set_schedule` | Schedule on/off, window start/end (local time), days bitmask. `dry_run` returns the request without sending |
| `tesla_wc_control.start_charging` | Start/resume charging the plugged-in vehicle |
| `tesla_wc_control.stop_charging` | Stop charging the plugged-in vehicle |
| `tesla_wc_control.set_vehicle_access` | `any_vehicle`, `tesla_only`, `authorised_teslas`, `compatibility` |
| `tesla_wc_control.get_status` | Read-only: settings and recent vehicles |
| `tesla_wc_control.send_raw` | Advanced: send any `WCMessages` request by name, for probing what Tesla relays |

Every action takes an optional `wall_connector` (DIN). Without it, the action
goes to every Wall Connector found.

Example: stop a car that ignores its own app's stop command, then let it
charge again later.

```yaml
action: tesla_wc_control.stop_charging
```

```yaml
action: tesla_wc_control.set_schedule
data:
  enabled: true
  start: "23:30:00"
  end: "05:30:00"
```

## Requirements

- Home Assistant 2026.8 or newer (tested on 2026.11).
- A Gen 3 Wall Connector registered to a Tesla energy site (it appears under
  your site in the Tesla app).
- The core **Tesla Fleet** integration set up with your own Tesla developer
  app, with the energy commands scope (`energy_cmds`). Teslemetry is used as a
  fallback only.

### Tesla Fleet setup in brief

1. Create an app at [developer.tesla.com](https://developer.tesla.com).
   Redirect URI `https://my.home-assistant.io/redirect/oauth`. Allowed origin:
   a domain you control that will serve your public key.
2. Tesla checks the key at
   `https://<domain>/.well-known/appspecific/com.tesla.3p.public-key.pem`.
   **This integration serves that file from Home Assistant**, using the key
   pair core Tesla Fleet creates at `/config/tesla_fleet.key`. If your allowed
   origin is an address that reaches Home Assistant (for example your Nabu
   Casa remote UI hostname), install this integration first and the
   registration step finds the key.
3. Add the Tesla Fleet integration. At the domain step enter just the
   hostname, with no `https://` or trailing slash. You can skip the virtual key
   step; it is only for vehicle commands.
4. Tesla bills Fleet API usage beyond a monthly credit, and Tesla Fleet polls
   your energy site every 10–15 s. If you only want it for this integration,
   turn off polling in its System options. Commands still work.

## Install

**HACS:** add this repository as a custom repository (category
*Integration*), install, restart.

**Manual:** copy `custom_components/tesla_wc_control` into your
`config/custom_components`, restart.

Then **Settings → Devices & services → Add integration → Tesla Wall
Connector Control** (no fields). It finds Wall Connectors from the Tesla
Fleet and Teslemetry device registries.

## How it works

- Requests go to `POST api/1/energy_sites/{site_id}/command` with
  `command_type: grpc_command`, a `wc` message, `identifier_type: 4`
  (`WALL_CONNECTOR_DIN`) and `target_id: <DIN>`.
- It reuses the cloud client of the loaded Tesla Fleet (or Teslemetry) entry,
  so tokens, regions and refresh are handled by that integration. This reads
  their internals (`entry.runtime_data.energysites`), so a big change there
  could break it.
- HTTP 200 does not mean the connector accepted a schedule. It answers with
  its own `ConfigureChargeScheduleResponse.error` (1 = `NONE` = applied),
  which is checked.
- Schedule periods are seconds since local midnight, in the time zone sent
  with the request (Home Assistant's zone and current UTC offset). Re-send
  after a clock change.

## Tests

```sh
python3 -m unittest discover tests
```

## Credits

- [TheBarnHome/tesla-wc3-scheduller](https://github.com/TheBarnHome/tesla-wc3-scheduller)
  (MIT) for the schedule request shape and the connector's result code.
- [Teslemetry/tesla-protocol](https://github.com/Teslemetry/tesla-protocol)
  for the Wall Connector protobuf definitions (`wall_connector.proto`).
- [tesla_fleet_api](https://github.com/Teslemetry/python-tesla-fleet-api),
  used through the Tesla Fleet and Teslemetry integrations.

Not affiliated with Tesla or Teslemetry.

## Licence

MIT
