"""
Unit test cho rule engine + state store (Đề tài 5, §5.17.7).
Chạy: python -m unittest discover -s iot_gateway   (không cần MQTT/InfluxDB)
"""
import unittest

from rule_engine import Thresholds, evaluate
from state_store import StateStore

TH = Thresholds()


def _telemetry(**kw) -> dict:
    base = dict(area_id="district-1", bin_id="bin-01", fill_level=10.0,
                weight_kg=8.0, methane_ppm=120.0, temperature=30.0, tilt=False)
    base.update(kw)
    return base


class TestRuleEngine(unittest.TestCase):
    def test_normal_no_events_all_off(self):
        r = evaluate(_telemetry(), TH)
        self.assertEqual(r["events"], {})
        self.assertEqual(r["desired"]["dispatch"], "off")
        self.assertEqual(r["desired"]["buzzer"], "off")
        self.assertEqual(r["desired"]["lock"], "off")

    def test_bin_full_warning_then_critical(self):
        self.assertEqual(evaluate(_telemetry(fill_level=88.0), TH)["events"]["bin_full"]["severity"], "warning")
        self.assertEqual(evaluate(_telemetry(fill_level=97.0), TH)["events"]["bin_full"]["severity"], "critical")
        self.assertEqual(evaluate(_telemetry(fill_level=88.0), TH)["desired"]["dispatch"], "on")

    def test_fire_risk_sets_buzzer_on_compactor_off(self):
        r = evaluate(_telemetry(temperature=72.0), TH)
        self.assertEqual(r["events"]["fire_risk"]["severity"], "critical")
        self.assertEqual(r["desired"]["buzzer"], "on")
        self.assertEqual(r["desired"]["compactor"], "off")

    def test_gas_alert(self):
        r = evaluate(_telemetry(methane_ppm=560.0), TH)
        self.assertIn("gas_alert", r["events"])
        self.assertEqual(r["events"]["gas_alert"]["severity"], "warning")

    def test_overweight_locks(self):
        r = evaluate(_telemetry(weight_kg=65.0), TH)
        self.assertIn("overweight", r["events"])
        self.assertEqual(r["desired"]["lock"], "on")

    def test_tilt_event_info_no_command(self):
        r = evaluate(_telemetry(tilt=True), TH)
        self.assertEqual(r["events"]["bin_tilted"]["severity"], "info")
        self.assertNotIn("tilt", r["desired"])


class TestStateStore(unittest.TestCase):
    def test_command_debounce(self):
        s = StateStore()
        # Lần đầu: 'off' trùng mặc định → không coi là đổi (tránh spam off lúc khởi động)
        self.assertFalse(s.command_changed("bin-01", "dispatch", "off"))
        # Chuyển sang 'on' → đổi
        self.assertTrue(s.command_changed("bin-01", "dispatch", "on"))
        # Lặp 'on' → không đổi
        self.assertFalse(s.command_changed("bin-01", "dispatch", "on"))
        # Quay về 'off' → đổi
        self.assertTrue(s.command_changed("bin-01", "dispatch", "off"))

    def test_event_edge_detection(self):
        s = StateStore()
        self.assertEqual(s.newly_fired_events("bin-01", {"bin_full"}), {"bin_full"})
        # Vẫn còn bin_full → không phát lại
        self.assertEqual(s.newly_fired_events("bin-01", {"bin_full"}), set())
        # Thêm fire_risk → chỉ fire_risk là cạnh lên mới
        self.assertEqual(s.newly_fired_events("bin-01", {"bin_full", "fire_risk"}), {"fire_risk"})
        # Hết hết → reset, lần sau bin_full lại tính cạnh lên
        self.assertEqual(s.newly_fired_events("bin-01", set()), set())
        self.assertEqual(s.newly_fired_events("bin-01", {"bin_full"}), {"bin_full"})

    def test_collection_route_order(self):
        s = StateStore()
        s.mark_for_collection("bin-02")
        s.mark_for_collection("bin-01")
        # Thứ tự theo thời điểm thêm (bin-02 chờ lâu hơn → trước)
        self.assertEqual(s.collection_route(), ["bin-02", "bin-01"])
        s.clear_collection("bin-02")
        self.assertEqual(s.collection_route(), ["bin-01"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
