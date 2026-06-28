"""
test_eta_predictor.py — Unit test cho module eta_predictor (OLS Linear Regression)

Không cần Docker, MQTT, InfluxDB hay bất kỳ I/O nào.
Chạy: python -m unittest gateway_api.test_eta_predictor -v
"""
import math
import unittest
from datetime import datetime, timezone

from gateway_api.eta_predictor import ETAResult, _ols_slope, predict_eta

BASE_TS = 1_700_000_000.0  # Unix timestamp cố định để test dễ reproduce


def _make_series(start_fill: float, rate_per_sec: float, n: int, interval_s: float = 60.0):
    """Tạo chuỗi (timestamp, fill) tăng tuyến tính hoàn hảo."""
    return [
        (BASE_TS + i * interval_s, start_fill + i * interval_s * rate_per_sec)
        for i in range(n)
    ]


# ──────────────────────────────────────────────────────────
class TestOlsSlope(unittest.TestCase):
    """Kiểm tra hàm _ols_slope() riêng lẻ."""

    def test_perfect_linear_increasing(self):
        """Dữ liệu hoàn toàn tuyến tính → slope chính xác 100%."""
        # y = 0.5x + 10, slope = 0.5 %/s
        xs = [0.0, 60.0, 120.0, 180.0, 240.0]
        ys = [10.0, 40.0, 70.0, 100.0, 130.0]
        # slope mong đợi: (130-10)/(240-0) = 0.5
        slope = _ols_slope(xs, ys)
        self.assertAlmostEqual(slope, 0.5, places=6)

    def test_perfect_linear_decreasing(self):
        """Slope âm khi fill giảm."""
        xs = [0.0, 60.0, 120.0]
        ys = [80.0, 60.0, 40.0]
        slope = _ols_slope(xs, ys)
        self.assertLess(slope, 0)

    def test_constant_series(self):
        """Fill không đổi → slope = 0."""
        xs = [0.0, 60.0, 120.0, 180.0]
        ys = [50.0, 50.0, 50.0, 50.0]
        slope = _ols_slope(xs, ys)
        self.assertAlmostEqual(slope, 0.0, places=6)

    def test_same_timestamp_returns_zero(self):
        """SS_xx = 0 (tất cả cùng thời điểm) → không chia cho 0."""
        xs = [0.0, 0.0, 0.0]
        ys = [10.0, 20.0, 30.0]
        slope = _ols_slope(xs, ys)
        self.assertEqual(slope, 0.0)

    def test_two_points(self):
        """2 điểm → slope = (y₂−y₁)/(x₂−x₁)."""
        xs = [0.0, 120.0]
        ys = [40.0, 52.0]   # tăng 12% trong 120 giây = 0.1 %/s
        slope = _ols_slope(xs, ys)
        self.assertAlmostEqual(slope, 0.1, places=6)

    def test_noisy_data_close_to_true_slope(self):
        """Dữ liệu có nhiễu nhỏ → slope xấp xỉ đúng (sai số < 5%)."""
        true_slope = 0.2  # %/s
        import random
        random.seed(42)
        xs = [i * 60.0 for i in range(20)]
        ys = [30.0 + true_slope * x + random.gauss(0, 0.5) for x in xs]
        slope = _ols_slope(xs, ys)
        self.assertAlmostEqual(slope, true_slope, delta=true_slope * 0.05)


# ──────────────────────────────────────────────────────────
class TestPredictEtaEdgeCases(unittest.TestCase):
    """Các trường hợp biên của predict_eta()."""

    def test_empty_series_returns_unavailable(self):
        r = predict_eta("bin-01", [])
        self.assertEqual(r.confidence, "unavailable")
        self.assertIsNone(r.eta_minutes)
        self.assertIsNone(r.current_fill)

    def test_single_point_returns_unavailable(self):
        r = predict_eta("bin-01", [(BASE_TS, 45.0)])
        self.assertEqual(r.confidence, "unavailable")
        self.assertAlmostEqual(r.current_fill, 45.0)
        self.assertIsNone(r.eta_minutes)

    def test_negative_slope_returns_none_eta(self):
        """Fill đang giảm (xe vừa thu gom) → không có ETA."""
        series = _make_series(start_fill=80.0, rate_per_sec=-0.1, n=10)
        r = predict_eta("bin-01", series)
        self.assertIsNone(r.eta_minutes)
        self.assertIsNone(r.eta_timestamp)
        self.assertLess(r.fill_rate_per_minute, 0)

    def test_flat_series_returns_none_eta(self):
        """Fill không đổi → slope = 0 → không có ETA."""
        series = [(BASE_TS + i * 60, 50.0) for i in range(5)]
        r = predict_eta("bin-01", series)
        self.assertIsNone(r.eta_minutes)
        self.assertAlmostEqual(r.fill_rate_per_minute, 0.0, places=3)

    def test_already_full_returns_zero_eta(self):
        """fill_level >= 100 → eta_minutes = 0.0."""
        series = [(BASE_TS + i * 60, 100.0) for i in range(5)]
        r = predict_eta("bin-01", series)
        self.assertEqual(r.eta_minutes, 0.0)
        self.assertIsNotNone(r.eta_timestamp)


# ──────────────────────────────────────────────────────────
class TestPredictEtaNormalCases(unittest.TestCase):
    """Các trường hợp thông thường, kiểm tra giá trị cụ thể."""

    def test_high_confidence_when_5_or_more_points(self):
        series = _make_series(40.0, rate_per_sec=0.1, n=5)
        r = predict_eta("bin-01", series)
        self.assertEqual(r.confidence, "high")

    def test_low_confidence_when_2_to_4_points(self):
        for n in (2, 3, 4):
            series = _make_series(40.0, rate_per_sec=0.1, n=n)
            r = predict_eta("bin-01", series)
            self.assertEqual(r.confidence, "low", msg=f"n={n}")

    def test_eta_minutes_calculation(self):
        """
        fill bắt đầu = 40%, rate = 0.1 %/giây = 6 %/phút
        fill_current sau 4 khoảng (4 × 60s) = 40 + 4×60×0.1 = 64%
        còn lại: 100 - 64 = 36%
        ETA = 36 / 0.1 = 360 giây = 6.0 phút
        """
        series = _make_series(start_fill=40.0, rate_per_sec=0.1, n=5, interval_s=60.0)
        r = predict_eta("bin-01", series)
        # current_fill tại điểm cuối (index 4): 40 + 4*60*0.1 = 64%
        self.assertAlmostEqual(r.current_fill, 64.0, places=1)
        self.assertAlmostEqual(r.fill_rate_per_minute, 6.0, places=2)
        # ETA = (100 - 64) / 0.1 / 60 = 360/60 = 6 phút
        self.assertAlmostEqual(r.eta_minutes, 6.0, delta=0.2)

    def test_eta_timestamp_is_after_last_point(self):
        """eta_timestamp phải nằm SAU timestamp điểm đo cuối."""
        series = _make_series(40.0, rate_per_sec=0.05, n=10, interval_s=60.0)
        r = predict_eta("bin-01", series)
        self.assertIsNotNone(r.eta_timestamp)
        t_last = datetime.fromtimestamp(series[-1][0], tz=timezone.utc)
        eta_dt = datetime.fromisoformat(r.eta_timestamp)
        self.assertGreater(eta_dt, t_last)

    def test_fill_rate_per_minute_unit(self):
        """fill_rate_per_minute = slope_per_sec × 60."""
        series = _make_series(20.0, rate_per_sec=0.05, n=6, interval_s=60.0)
        r = predict_eta("bin-01", series)
        self.assertAlmostEqual(r.fill_rate_per_minute, 0.05 * 60, delta=0.05)

    def test_bin_id_preserved(self):
        """bin_id phải được giữ nguyên trong kết quả."""
        series = _make_series(50.0, rate_per_sec=0.1, n=5)
        r = predict_eta("bin-99", series)
        self.assertEqual(r.bin_id, "bin-99")

    def test_to_dict_serializable(self):
        """to_dict() không được raise exception và trả về dict."""
        series = _make_series(30.0, rate_per_sec=0.1, n=8)
        r = predict_eta("bin-01", series)
        d = r.to_dict()
        self.assertIsInstance(d, dict)
        self.assertIn("eta_minutes", d)
        self.assertIn("confidence", d)
        self.assertIn("fill_rate_per_minute", d)

    def test_large_series_high_confidence(self):
        """Mô phỏng thực tế: sensor publish 5-7s → 15 phút ≈ 128-180 điểm."""
        series = _make_series(10.0, rate_per_sec=0.02, n=150, interval_s=6.0)
        r = predict_eta("bin-01", series)
        self.assertEqual(r.confidence, "high")
        self.assertIsNotNone(r.eta_minutes)
        self.assertGreater(r.eta_minutes, 0)

    def test_timestamp_normalization_correctness(self):
        """
        Kết quả không được thay đổi khi dịch toàn bộ timestamp cùng một hằng số.
        Điều này xác nhận chuẩn hóa t₀ hoạt động đúng.
        """
        series_a = _make_series(50.0, rate_per_sec=0.1, n=5, interval_s=60.0)
        offset = 1_000_000_000.0
        series_b = [(t + offset, f) for t, f in series_a]
        ra = predict_eta("bin-01", series_a)
        rb = predict_eta("bin-01", series_b)
        self.assertAlmostEqual(ra.fill_rate_per_minute, rb.fill_rate_per_minute, places=4)
        if ra.eta_minutes is not None and rb.eta_minutes is not None:
            self.assertAlmostEqual(ra.eta_minutes, rb.eta_minutes, places=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
