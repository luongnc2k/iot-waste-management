"""
Unit test cho tb_gateway.py — cầu nối ThingsBoard Cloud (SV3, §5.17.2).
Chạy: python -m unittest discover -s iot_gateway -p "test_*.py" -v

Chỉ test các hàm THUẦN (rpc_to_command, build_*, map_shared_attributes) — không
cần MQTT/ThingsBoard thật, cùng triết lý với test_rule_engine.py của SV2.
"""
import unittest

from tb_gateway import (
    rpc_action_from_params,
    rpc_to_command,
    build_telemetry_values,
    build_alarm_values,
    build_gateway_telemetry,
    map_shared_attributes,
)


class TestRpcActionFromParams(unittest.TestCase):
    def test_bool_true(self):
        self.assertEqual(rpc_action_from_params(True), "on")

    def test_bool_false(self):
        self.assertEqual(rpc_action_from_params(False), "off")

    def test_string_passthrough(self):
        self.assertEqual(rpc_action_from_params("off"), "off")
        self.assertEqual(rpc_action_from_params("on"), "on")

    def test_truthy_dict_defaults_on(self):
        # ThingsBoard RPC đôi khi gửi params rỗng {} cho lệnh dạng "bật"
        self.assertEqual(rpc_action_from_params({"some": "value"}), "on")

    def test_falsy_value_defaults_off(self):
        self.assertEqual(rpc_action_from_params(None), "off")
        self.assertEqual(rpc_action_from_params({}), "off")


class TestRpcToCommand(unittest.TestCase):
    def test_known_method_maps_to_target(self):
        cmd = rpc_to_command("bin-01", "setLock", True)
        self.assertEqual(cmd["bin_id"], "bin-01")
        self.assertEqual(cmd["target"], "lock")
        self.assertEqual(cmd["action"], "on")
        self.assertEqual(cmd["reason"], "thingsboard_rpc")
        self.assertIn("timestamp", cmd)

    def test_all_four_targets_mapped(self):
        expected = {
            "setLock": "lock",
            "setCompactor": "compactor",
            "setBuzzer": "buzzer",
            "setDispatch": "dispatch",
        }
        for method, target in expected.items():
            cmd = rpc_to_command("bin-02", method, "on")
            self.assertEqual(cmd["target"], target)

    def test_unknown_method_returns_none(self):
        self.assertIsNone(rpc_to_command("bin-01", "setSomethingElse", True))

    def test_empty_method_returns_none(self):
        self.assertIsNone(rpc_to_command("bin-01", "", True))


class TestBuildTelemetryValues(unittest.TestCase):
    def test_extracts_known_fields(self):
        data = {
            "fill_level": 88.5, "weight_kg": 70.0, "methane_ppm": 320.0,
            "temperature": 33.0, "fill_status": "high", "lid_status": "closed",
        }
        values = build_telemetry_values(data)
        self.assertEqual(values, {
            "fill_level": 88.5, "weight_kg": 70.0, "methane_ppm": 320.0,
            "temperature": 33.0, "fill_status": "high",
        })
        # lid_status không nằm trong telemetry đẩy lên TB — không bị lẫn vào
        self.assertNotIn("lid_status", values)

    def test_missing_fields_default_to_zero_or_low(self):
        self.assertEqual(build_telemetry_values({}), {
            "fill_level": 0, "weight_kg": 0, "methane_ppm": 0,
            "temperature": 0, "fill_status": "low",
        })


class TestBuildAlarmValues(unittest.TestCase):
    def test_extracts_event_fields(self):
        event = {"event_type": "fire_risk", "severity": "critical",
                  "value": 72.0, "threshold": 60.0, "action_taken": "buzzer_on,compactor_off"}
        self.assertEqual(build_alarm_values(event), {
            "alarm_type": "fire_risk", "alarm_severity": "critical",
            "alarm_value": 72.0, "alarm_threshold": 60.0,
            "alarm_action": "buzzer_on,compactor_off",
        })

    def test_missing_fields_have_safe_defaults(self):
        values = build_alarm_values({})
        self.assertEqual(values["alarm_type"], "unknown")
        self.assertEqual(values["alarm_severity"], "info")


class TestBuildGatewayTelemetry(unittest.TestCase):
    def test_wraps_values_in_thingsboard_gateway_format(self):
        payload = build_gateway_telemetry("bin-01", {"fill_level": 50.0}, 1718900000000)
        self.assertEqual(payload, {
            "bin-01": [{"ts": 1718900000000, "values": {"fill_level": 50.0}}]
        })


class TestMapSharedAttributes(unittest.TestCase):
    def test_maps_known_attrs_to_env_keys(self):
        thresholds = map_shared_attributes({"shared": {"temp_fire": 55, "weight_lock": 50}})
        self.assertEqual(thresholds, {
            "TEMP_FIRE_THRESHOLD": 55.0, "WEIGHT_LOCK_THRESHOLD": 50.0,
        })

    def test_accepts_flat_payload_without_shared_wrapper(self):
        # Response của v1/gateway/attributes/request không có wrapper "shared"
        thresholds = map_shared_attributes({"fill_dispatch": 80})
        self.assertEqual(thresholds, {"FILL_DISPATCH_THRESHOLD": 80.0})

    def test_unknown_attrs_ignored(self):
        thresholds = map_shared_attributes({"shared": {"unrelated_attr": 1}})
        self.assertEqual(thresholds, {})

    def test_non_numeric_value_ignored_not_raised(self):
        thresholds = map_shared_attributes({"shared": {"temp_fire": "not-a-number"}})
        self.assertEqual(thresholds, {})

    def test_empty_payload_returns_empty_dict(self):
        self.assertEqual(map_shared_attributes({}), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
