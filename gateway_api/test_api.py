"""
Unit test cho gateway_api/api.py (SV3, REST API).
Chạy: python -m unittest discover -s gateway_api -p "test_*.py" -v

api.py mở kết nối MQTT thật (vòng lặp retry vô hạn) và InfluxDBClient ngay khi
import module — không có cách "lazy" để né. Vì vậy test này patch
mqtt.Client.connect / loop_start TRƯỚC khi import api, để import không treo
chờ broker không tồn tại. Sau khi import, mock mqtt_client.publish và
query_api.query trực tiếp để kiểm soát input/output từng test case, không
cần Docker/broker/InfluxDB thật.
"""
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

import paho.mqtt.client as mqtt  # noqa: E402

_orig_connect = mqtt.Client.connect
_orig_loop_start = mqtt.Client.loop_start
mqtt.Client.connect = lambda self, *a, **kw: 0
mqtt.Client.loop_start = lambda self: None

import api  # noqa: E402  (import sau khi patch để không treo ở _connect_mqtt)
from fastapi.testclient import TestClient  # noqa: E402

# Khôi phục lại hành vi gốc của paho cho mọi code khác import sau test này.
mqtt.Client.connect = _orig_connect
mqtt.Client.loop_start = _orig_loop_start


class FakeRecord:
    """Giả lập influxdb_client.FluxRecord — chỉ cần .values và .get_time()."""

    def __init__(self, values: dict, ts=None):
        self.values = values
        self._ts = ts or datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)

    def get_time(self):
        return self._ts


class FakeTable:
    def __init__(self, records):
        self.records = records


class ApiTestCase(unittest.TestCase):
    """Base: mock mqtt_client.publish + query_api.query trước mỗi test."""

    def setUp(self):
        self.client = TestClient(api.app)
        api.mqtt_client.publish = mock.MagicMock()
        api.query_api.query = mock.MagicMock(return_value=[])  # mặc định: không có data


class TestHealth(ApiTestCase):
    def test_health_returns_configured_bins(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["bins"], api.BINS)


class TestListBins(ApiTestCase):
    def test_no_data_returns_status_no_data_per_bin(self):
        resp = self.client.get("/bins")
        self.assertEqual(resp.status_code, 200)
        bins = resp.json()["bins"]
        self.assertEqual(len(bins), len(api.BINS))
        for b in bins:
            self.assertEqual(b["status"], "no_data")

    def test_returns_telemetry_shape_when_data_present(self):
        api.query_api.query = mock.MagicMock(return_value=[
            FakeTable([FakeRecord({
                "fill_level": 42.0, "weight_kg": 30.0,
                "methane_ppm": 120.0, "temperature": 31.0,
            })])
        ])
        resp = self.client.get("/bins")
        bins = resp.json()["bins"]
        self.assertEqual(bins[0]["fill_level"], 42.0)
        self.assertEqual(bins[0]["weight_kg"], 30.0)


class TestGetBinState(ApiTestCase):
    def test_unknown_bin_returns_404(self):
        resp = self.client.get("/bins/bin-does-not-exist/state")
        self.assertEqual(resp.status_code, 404)

    def test_known_bin_returns_telemetry_and_actuator(self):
        resp = self.client.get(f"/bins/{api.BINS[0]}/state")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("telemetry", body)
        self.assertIn("actuator", body)


class TestCollectionRoute(ApiTestCase):
    """GET /collection/route — đề bài §5.9, suy từ fill_level mới nhất trên InfluxDB."""

    def test_returns_empty_when_no_bin_above_threshold(self):
        api.query_api.query = mock.MagicMock(return_value=[
            FakeTable([FakeRecord({"fill_level": 10.0})])
        ])
        resp = self.client.get("/collection/route")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["bins_due_for_collection"], [])

    def test_returns_bins_above_threshold_sorted_by_fill_desc(self):
        fill_by_bin = {api.BINS[0]: 92.0, api.BINS[1]: 10.0, api.BINS[2]: 88.0}

        def fake_query(query, org=None):
            for bin_id, fill in fill_by_bin.items():
                if bin_id in query:
                    return [FakeTable([FakeRecord({"fill_level": fill})])]
            return []

        api.query_api.query = mock.MagicMock(side_effect=fake_query)
        resp = self.client.get("/collection/route")
        due = resp.json()["bins_due_for_collection"]
        self.assertEqual([b["bin_id"] for b in due], [api.BINS[0], api.BINS[2]])
        self.assertEqual(due[0]["fill_level"], 92.0)

    def test_includes_threshold_used(self):
        resp = self.client.get("/collection/route")
        self.assertIn("threshold", resp.json())


class TestGetBinEvents(ApiTestCase):
    def test_unknown_bin_returns_404(self):
        resp = self.client.get("/bins/bin-does-not-exist/events")
        self.assertEqual(resp.status_code, 404)

    def test_parses_event_records(self):
        api.query_api.query = mock.MagicMock(return_value=[
            FakeTable([FakeRecord({
                "event_type": "bin_full", "severity": "warning",
                "value": 88.0, "threshold": 85.0,
            })])
        ])
        resp = self.client.get(f"/bins/{api.BINS[0]}/events")
        events = resp.json()["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "bin_full")
        self.assertEqual(events[0]["severity"], "warning")

    def test_influx_error_returns_empty_list_not_500(self):
        api.query_api.query = mock.MagicMock(side_effect=RuntimeError("influxdb down"))
        resp = self.client.get(f"/bins/{api.BINS[0]}/events")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["events"], [])


class TestSendCommand(ApiTestCase):
    def test_unknown_bin_returns_404(self):
        resp = self.client.post(
            "/bins/bin-does-not-exist/command",
            json={"target": "lock", "action": "on"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_invalid_target_returns_400(self):
        resp = self.client.post(
            f"/bins/{api.BINS[0]}/command",
            json={"target": "motor", "action": "on"},
        )
        self.assertEqual(resp.status_code, 400)
        api.mqtt_client.publish.assert_not_called()

    def test_invalid_action_returns_400(self):
        resp = self.client.post(
            f"/bins/{api.BINS[0]}/command",
            json={"target": "lock", "action": "maybe"},
        )
        self.assertEqual(resp.status_code, 400)
        api.mqtt_client.publish.assert_not_called()

    def test_valid_command_publishes_to_correct_topic(self):
        bin_id = api.BINS[0]
        resp = self.client.post(
            f"/bins/{bin_id}/command",
            json={"target": "dispatch", "action": "on", "reason": "manual_test"},
        )
        self.assertEqual(resp.status_code, 200)

        api.mqtt_client.publish.assert_called_once()
        topic, payload = api.mqtt_client.publish.call_args[0][:2]
        self.assertEqual(topic, f"waste/{bin_id}/actuator/command")

        sent = json.loads(payload)
        self.assertEqual(sent["bin_id"], bin_id)
        self.assertEqual(sent["target"], "dispatch")
        self.assertEqual(sent["action"], "on")
        self.assertEqual(sent["reason"], "manual_test")

    def test_default_reason_when_omitted(self):
        resp = self.client.post(
            f"/bins/{api.BINS[0]}/command",
            json={"target": "buzzer", "action": "off"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["command"]["reason"], "manual_api")


class TestConfigEndpoints(ApiTestCase):
    def test_get_config_returns_defaults(self):
        resp = self.client.get("/config")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in ("fill_dispatch", "fill_critical", "temp_fire", "methane_alert", "weight_lock"):
            self.assertIn(key, body)

    def test_post_config_with_no_fields_returns_400(self):
        resp = self.client.post("/config", json={})
        self.assertEqual(resp.status_code, 400)

    def test_post_config_publishes_retained_message(self):
        resp = self.client.post("/config", json={"temp_fire": 55})
        self.assertEqual(resp.status_code, 200)

        api.mqtt_client.publish.assert_called_once()
        args, kwargs = api.mqtt_client.publish.call_args
        topic, payload = args[:2]
        self.assertEqual(topic, "waste/gateway/config")
        self.assertTrue(kwargs.get("retain"))

        sent = json.loads(payload)
        self.assertEqual(sent["thresholds"], {"TEMP_FIRE_THRESHOLD": 55.0})

    def test_post_config_ignores_none_fields(self):
        # Pydantic phải coi "fill_critical": null là "không cung cấp", không
        # phải lỗi validation (xem ghi chú Optional[float] trong api.py).
        resp = self.client.post("/config", json={"fill_dispatch": 80, "fill_critical": None})
        self.assertEqual(resp.status_code, 200)
        sent = json.loads(api.mqtt_client.publish.call_args[0][1])
        self.assertEqual(sent["thresholds"], {"FILL_DISPATCH_THRESHOLD": 80.0})


if __name__ == "__main__":
    unittest.main(verbosity=2)
