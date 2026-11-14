"""Tests for custom_components/tesla_wc_control/payload.py (pure Python, no Home Assistant needed).

Run: python3 -m unittest discover tests
"""

import importlib.util
import unittest
from datetime import time
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "custom_components" / "tesla_wc_control" / "payload.py"
_spec = importlib.util.spec_from_file_location("tesla_wc_control_payload", _PATH)
payload = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(payload)

DIN = "0000000-00-A--TEST0000000000"


def _wc(body):
    return body["command_properties"]["message"]["wc"]


class CommandTests(unittest.TestCase):
    def test_targets_wall_connector_by_din(self):
        body = payload.build_command(DIN, "get_config_request")
        self.assertEqual(body["command_type"], "grpc_command")
        self.assertEqual(body["command_properties"]["identifier_type"], 4)
        self.assertEqual(body["command_properties"]["target_id"], DIN)
        self.assertEqual(_wc(body), {"get_config_request": {}})

    def test_response_key(self):
        self.assertEqual(payload.response_key("configure_charge_schedule_request"), "ConfigureChargeScheduleResponse")
        self.assertEqual(payload.response_key("push_charge_command_request"), "PushChargeCommandResponse")

    def test_charge_commands(self):
        self.assertEqual(
            _wc(payload.build_charge_command(DIN, True)),
            {"push_charge_command_request": {"charge_command": {"charging_command": 1}}},
        )
        self.assertEqual(
            _wc(payload.build_charge_command(DIN, False)),
            {"push_charge_command_request": {"charge_command": {"charging_command": 2}}},
        )

    def test_access_modes_match_proto(self):
        self.assertEqual(
            payload.VEHICLE_ACCESS_MODES,
            {"any_vehicle": 1, "tesla_only": 2, "authorised_teslas": 4, "compatibility": 5},
        )


class ScheduleTests(unittest.TestCase):
    def test_overnight_window_is_split_at_midnight(self):
        body = payload.build_schedule(
            din=DIN, enabled=True, start=time(23, 30), end=time(5, 30),
            day_bitmask=127, time_zone_id="Europe/London", utc_offset_seconds=0,
        )
        req = _wc(body)["configure_charge_schedule_request"]
        period = req["config"]["schedule"]["day_time_periods"][0]
        self.assertEqual(period["day_bitmask"], 127)
        self.assertEqual(
            period["time_periods"],
            [{"start_seconds": 84600, "end_seconds": 86400}, {"start_seconds": 0, "end_seconds": 19800}],
        )
        self.assertTrue(req["config"]["enable_schedule"])
        self.assertEqual(req["time_zone"]["time_zone_id"], "Europe/London")

    def test_same_day_window_is_one_period(self):
        self.assertEqual(payload.time_periods(time(22, 0), time(23, 0)), [{"start_seconds": 79200, "end_seconds": 82800}])

    def test_window_ending_at_midnight(self):
        self.assertEqual(payload.time_periods(time(23, 30), time(0, 0)), [{"start_seconds": 84600, "end_seconds": 86400}])

    def test_disabled_still_sends_a_period(self):
        body = payload.build_schedule(
            din=DIN, enabled=False, start=time(23, 30), end=time(5, 30),
            day_bitmask=127, time_zone_id="Europe/London", utc_offset_seconds=3600,
        )
        req = _wc(body)["configure_charge_schedule_request"]
        self.assertFalse(req["config"]["enable_schedule"])
        self.assertTrue(req["config"]["schedule"]["day_time_periods"][0]["time_periods"])
        self.assertEqual(req["time_zone"]["time_zone_info"]["transitions"][0]["local_time_utc_offset"], 3600)


class ResponseTests(unittest.TestCase):
    def test_schedule_success_code(self):
        resp = {"response": {"message": {"Payload": {"Wc": {"Message": {"ConfigureChargeScheduleResponse": {"error": 1}}}}}}}
        self.assertEqual(payload.find_schedule_error(resp), 1)

    def test_schedule_failure_code_inside_list(self):
        self.assertEqual(payload.find_schedule_error({"r": [{"ConfigureChargeScheduleResponse": {"error": 4}}]}), 4)

    def test_absent(self):
        self.assertIsNone(payload.find_schedule_error({"response": {"code": 0}}))

    def test_find_key_empty_reply_is_found(self):
        resp = {"response": {"message": {"Payload": {"Wc": {"Message": {"PushChargeCommandResponse": {}}}}}}}
        self.assertEqual(payload.find_key(resp, "PushChargeCommandResponse"), {})


if __name__ == "__main__":
    unittest.main()
