"""
Unit test cho handle_config_update() trong gateway.py (SV3, §5.17.3).
Chạy: python -m unittest discover -s iot_gateway -p "test_*.py" -v

Bao phủ vòng lặp: REST API POST /config hoặc ThingsBoard Shared Attributes
publish lên topic waste/gateway/config (retained) → gateway subscribe →
handle_config_update() áp dụng vào THRESHOLDS dùng chung với rule_engine.evaluate().
Trước khi có test này, đường publish tồn tại nhưng KHÔNG có ai subscribe nên
update không có tác dụng — xem SV3-Tasks.md mục "Gaps đã vá".

Import gateway.py an toàn ở mức module (không có I/O/MQTT connect khi import,
chỉ main() mới connect) — không cần broker/InfluxDB thật.
"""
import unittest

import gateway
from rule_engine import evaluate


class TestHandleConfigUpdate(unittest.TestCase):
    def setUp(self):
        # THRESHOLDS là instance dùng chung toàn module — reset về default
        # trước mỗi test để các test độc lập nhau.
        gateway.THRESHOLDS.fill_dispatch = 85.0
        gateway.THRESHOLDS.fill_critical = 95.0
        gateway.THRESHOLDS.temp_fire = 60.0
        gateway.THRESHOLDS.methane_alert = 500.0
        gateway.THRESHOLDS.weight_lock = 60.0

    def test_applies_known_thresholds(self):
        gateway.handle_config_update({
            "thresholds": {"TEMP_FIRE_THRESHOLD": 50, "WEIGHT_LOCK_THRESHOLD": 40}
        })
        self.assertEqual(gateway.THRESHOLDS.temp_fire, 50.0)
        self.assertEqual(gateway.THRESHOLDS.weight_lock, 40.0)
        # Các ngưỡng không được nhắc tới giữ nguyên giá trị cũ
        self.assertEqual(gateway.THRESHOLDS.fill_dispatch, 85.0)

    def test_ignores_unknown_keys(self):
        gateway.handle_config_update({"thresholds": {"NOT_A_REAL_THRESHOLD": 1}})
        self.assertEqual(gateway.THRESHOLDS.fill_dispatch, 85.0)

    def test_ignores_non_numeric_values(self):
        gateway.handle_config_update({"thresholds": {"TEMP_FIRE_THRESHOLD": "rất nóng"}})
        self.assertEqual(gateway.THRESHOLDS.temp_fire, 60.0)

    def test_empty_payload_is_noop(self):
        gateway.handle_config_update({})
        self.assertEqual(gateway.THRESHOLDS.fill_dispatch, 85.0)
        self.assertEqual(gateway.THRESHOLDS.temp_fire, 60.0)

    def test_update_takes_effect_immediately_in_rule_engine(self):
        """
        Đây là test "đóng vòng lặp": chứng minh update runtime thật sự ảnh
        hưởng tới evaluate() ngay telemetry kế tiếp, không cần restart.
        """
        telemetry = dict(area_id="district-1", bin_id="bin-01", fill_level=10.0,
                          weight_kg=8.0, methane_ppm=120.0, temperature=52.0, tilt=False)

        # Với ngưỡng mặc định (temp_fire=60) → 52°C chưa kích hoạt fire_risk
        result_before = evaluate(telemetry, gateway.THRESHOLDS)
        self.assertNotIn("fire_risk", result_before["events"])

        # Hạ ngưỡng xuống 50 qua đúng đường dữ liệu mà REST API/TB dùng
        gateway.handle_config_update({"thresholds": {"TEMP_FIRE_THRESHOLD": 50}})

        # Cùng telemetry (52°C) giờ phải vượt ngưỡng mới
        result_after = evaluate(telemetry, gateway.THRESHOLDS)
        self.assertIn("fire_risk", result_after["events"])
        self.assertEqual(result_after["desired"]["buzzer"], "on")


if __name__ == "__main__":
    unittest.main(verbosity=2)
