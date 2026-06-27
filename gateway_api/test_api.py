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
from datetime import datetime, timedelta, timezone
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

    def test_actuator_query_uses_wide_time_window_not_5_minutes(self):
        """
        Regression test: actuator chỉ publish status khi NHẬN LỆNH MỚI (event-
        driven), không định kỳ như sensor. Range "-5m" từng khiến API báo nhầm
        "no_data" cho actuator đã idle quá 5 phút dù trạng thái vẫn hợp lệ —
        phát hiện khi tập demo. Phải dùng cửa sổ rộng (ví dụ -24h), khớp cách
        panel Grafana "Trạng thái thiết bị theo thùng" đã làm từ trước.
        """
        captured_queries = []

        def fake_query(query, org=None):
            captured_queries.append(query)
            return []

        api.query_api.query = mock.MagicMock(side_effect=fake_query)
        self.client.get(f"/bins/{api.BINS[0]}/state")

        actuator_queries = [q for q in captured_queries if "actuator_status" in q]
        self.assertTrue(actuator_queries, "Không thấy truy vấn actuator_status nào")
        self.assertNotIn("-5m", actuator_queries[0])
        self.assertIn("-24h", actuator_queries[0])


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


class TestSystemSummary(ApiTestCase):
    """GET /summary — tổng quan hệ thống."""

    def test_structure_always_present(self):
        resp = self.client.get("/summary")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in ("total_bins", "online", "offline", "due_for_collection", "critical", "bins"):
            self.assertIn(key, body)

    def test_total_bins_matches_config(self):
        resp = self.client.get("/summary")
        self.assertEqual(resp.json()["total_bins"], len(api.BINS))

    def test_offline_when_no_data(self):
        resp = self.client.get("/summary")
        body = resp.json()
        # Không có data → tất cả offline
        self.assertEqual(body["offline"], len(api.BINS))
        self.assertEqual(body["online"], 0)

    def test_counts_due_and_critical_bins(self):
        def fake_query(query, org=None):
            if "bin_telemetry" in query:
                return [FakeTable([FakeRecord(
                    {"fill_level": 97.0, "weight_kg": 50.0, "methane_ppm": 100.0, "temperature": 25.0},
                    ts=datetime.now(timezone.utc),
                )])]
            return []

        api.query_api.query = mock.MagicMock(side_effect=fake_query)
        resp = self.client.get("/summary")
        body = resp.json()
        # 3 bins, đều 97% → critical=3, due=3
        self.assertEqual(body["critical"], len(api.BINS))
        self.assertEqual(body["due_for_collection"], len(api.BINS))


class TestOfflineBins(ApiTestCase):
    """GET /bins/offline — sensor offline detection."""

    def test_all_offline_when_no_data(self):
        resp = self.client.get("/bins/offline")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["offline"]), len(api.BINS))
        self.assertEqual(len(body["online"]), 0)

    def test_online_when_fresh_data(self):
        def fake_query(query, org=None):
            return [FakeTable([FakeRecord(
                {"fill_level": 50.0, "weight_kg": 30.0, "methane_ppm": 100.0, "temperature": 25.0},
                ts=datetime.now(timezone.utc),
            )])]

        api.query_api.query = mock.MagicMock(side_effect=fake_query)
        resp = self.client.get("/bins/offline")
        body = resp.json()
        self.assertEqual(len(body["online"]), len(api.BINS))
        self.assertEqual(len(body["offline"]), 0)

    def test_includes_timeout_in_response(self):
        resp = self.client.get("/bins/offline")
        self.assertIn("offline_timeout_seconds", resp.json())


class TestBinEta(ApiTestCase):
    """GET /bins/{bin_id}/eta — dự báo thời điểm thùng đầy."""

    def test_unknown_bin_returns_404(self):
        resp = self.client.get("/bins/bin-does-not-exist/eta")
        self.assertEqual(resp.status_code, 404)

    def test_unavailable_when_no_data(self):
        resp = self.client.get(f"/bins/{api.BINS[0]}/eta")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["confidence"], "unavailable")
        self.assertIsNone(body["eta_minutes"])

    def test_returns_eta_when_fill_increasing(self):
        now_ts = datetime.now(timezone.utc)
        # 5 điểm, tăng đều 1%/phút, hiện tại 50%
        points = [
            FakeRecord({"fill_level": None}, ts=now_ts),  # sẽ bị override
        ]
        # Tạo 5 bản ghi tăng dần: t-4min→46%, t-3min→47%, ... t0→50%
        records = []
        for i in range(5):
            offset = (4 - i) * 60
            ts = now_ts - timedelta(seconds=offset)

            class _R:
                def __init__(self, v, t):
                    self._v = v
                    self._t = t
                def get_time(self):
                    return self._t
                def get_value(self):
                    return self._v

            records.append(_R(46.0 + i, ts))

        class _Table:
            def __init__(self, recs):
                self.records = recs

        api.query_api.query = mock.MagicMock(return_value=[_Table(records)])
        resp = self.client.get(f"/bins/{api.BINS[0]}/eta")
        body = resp.json()
        # slope ~1%/min, từ 50% → cần 50 phút
        self.assertIsNotNone(body["eta_minutes"])
        self.assertGreater(body["eta_minutes"], 0)
        self.assertIn("eta_timestamp", body)
        self.assertIn("fill_rate_per_minute", body)

    def test_no_eta_when_fill_stable_or_decreasing(self):
        now_ts = datetime.now(timezone.utc)

        class _R:
            def __init__(self, v, t):
                self._v = v
                self._t = t
            def get_time(self):
                return self._t
            def get_value(self):
                return self._v

        class _Table:
            def __init__(self, recs):
                self.records = recs

        # 3 điểm, fill_level giảm dần
        records = [_R(60.0 - i * 2, now_ts - timedelta(seconds=(2 - i) * 60)) for i in range(3)]
        api.query_api.query = mock.MagicMock(return_value=[_Table(records)])
        resp = self.client.get(f"/bins/{api.BINS[0]}/eta")
        body = resp.json()
        self.assertIsNone(body["eta_minutes"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
