"""
eta_predictor.py — Dự báo thời điểm thùng rác đầy (§5.17 Nâng cao #6)

Thuật toán: Ordinary Least Squares (OLS) Linear Regression
============================================================

Bài toán: cho chuỗi đo (tᵢ, fᵢ) với tᵢ là Unix timestamp (giây) và
fᵢ là fill_level (%), tìm đường thẳng f = a·t + b khớp tốt nhất với
dữ liệu theo nghĩa bình phương sai số nhỏ nhất, rồi suy ra thời điểm
f đạt 100%.

Công thức OLS slope:
    a = SS_xy / SS_xx
    SS_xy = Σ (xᵢ − x̄)(yᵢ − ȳ)   ← tổng tích sai lệch
    SS_xx = Σ (xᵢ − x̄)²            ← tổng bình phương sai lệch thời gian

với xᵢ = tᵢ − t₀ (chuẩn hóa để tránh tràn số khi bình phương timestamp).

ETA (giây từ điểm đo cuối): Δt = (100 − fill_current) / a

Độ phức tạp: O(n) thời gian, O(n) bộ nhớ.
Không dùng numpy / sklearn — chỉ Python stdlib.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass
class ETAResult:
    """Kết quả dự báo thời điểm thùng đầy."""
    bin_id: str
    current_fill: Optional[float]           # fill_level hiện tại (%)
    fill_rate_per_minute: Optional[float]   # tốc độ tăng (%/phút), âm = đang giảm
    eta_minutes: Optional[float]            # số phút đến khi đầy (None nếu không tính được)
    eta_timestamp: Optional[str]            # ISO 8601 UTC
    confidence: str                         # "high" | "low" | "unavailable"
    note: str                               # mô tả ngắn nếu không có ETA

    def to_dict(self) -> dict:
        return {
            "bin_id": self.bin_id,
            "current_fill": round(self.current_fill, 2) if self.current_fill is not None else None,
            "fill_rate_per_minute": self.fill_rate_per_minute,
            "eta_minutes": self.eta_minutes,
            "eta_timestamp": self.eta_timestamp,
            "confidence": self.confidence,
            "note": self.note,
        }


def _ols_slope(xs: list[float], ys: list[float]) -> float:
    """
    Hệ số góc OLS cho tập điểm (xᵢ, yᵢ).

        a = SS_xy / SS_xx
        SS_xy = Σ(xᵢ − x̄)(yᵢ − ȳ)
        SS_xx = Σ(xᵢ − x̄)²

    Trả về 0.0 khi SS_xx = 0 (tất cả điểm cùng timestamp).
    """
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    return ss_xy / ss_xx if ss_xx > 0 else 0.0


def predict_eta(
    bin_id: str,
    time_series: list[tuple[float, float]],
) -> ETAResult:
    """
    Dự báo thời điểm thùng đầy bằng OLS Linear Regression.

    Args:
        bin_id: ID thùng rác.
        time_series: danh sách (unix_timestamp_giây, fill_level_%)
                     đã sort tăng dần theo thời gian.

    Returns:
        ETAResult — xem docstring của class.

    Các trường hợp đặc biệt:
        - n < 2:     confidence="unavailable", eta_minutes=None
        - slope ≤ 0: thùng không tăng, eta_minutes=None
        - fill ≥ 100: thùng đã đầy, eta_minutes=0.0
    """
    n = len(time_series)

    if n < 2:
        return ETAResult(
            bin_id=bin_id,
            current_fill=time_series[0][1] if n == 1 else None,
            fill_rate_per_minute=None,
            eta_minutes=None,
            eta_timestamp=None,
            confidence="unavailable",
            note="Không đủ dữ liệu (cần ít nhất 2 điểm trong cửa sổ quan sát 15 phút)",
        )

    # Chuẩn hóa thời gian: xᵢ = tᵢ − t₀
    # Unix timestamp ~1.7×10⁹ giây, bình phương trực tiếp → ~2.9×10¹⁸ (gần float64 max)
    t0 = time_series[0][0]
    xs = [t - t0 for t, _ in time_series]
    ys = [f      for _, f in time_series]

    slope_per_sec = _ols_slope(xs, ys)
    fill_rate_per_minute = round(slope_per_sec * 60, 4)
    current_fill = ys[-1]
    confidence = "high" if n >= 5 else "low"

    if current_fill >= 100.0:
        return ETAResult(
            bin_id=bin_id,
            current_fill=current_fill,
            fill_rate_per_minute=fill_rate_per_minute,
            eta_minutes=0.0,
            eta_timestamp=datetime.fromtimestamp(time_series[-1][0], tz=timezone.utc).isoformat(),
            confidence=confidence,
            note="Thùng đã đầy",
        )

    if slope_per_sec <= 0:
        return ETAResult(
            bin_id=bin_id,
            current_fill=current_fill,
            fill_rate_per_minute=fill_rate_per_minute,
            eta_minutes=None,
            eta_timestamp=None,
            confidence=confidence,
            note="Thùng không tăng mức đầy (ổn định hoặc đang giảm — có thể vừa thu gom)",
        )

    # ETA tính từ timestamp của điểm đo CUỐI CÙNG (không phải now())
    # vì current_fill là fill_level tại điểm đó, không phải tại thời điểm gọi API
    seconds_to_full = (100.0 - current_fill) / slope_per_sec
    t_last = datetime.fromtimestamp(time_series[-1][0], tz=timezone.utc)
    eta_dt = t_last + timedelta(seconds=seconds_to_full)
    eta_minutes = round(seconds_to_full / 60, 1)

    return ETAResult(
        bin_id=bin_id,
        current_fill=current_fill,
        fill_rate_per_minute=fill_rate_per_minute,
        eta_minutes=eta_minutes,
        eta_timestamp=eta_dt.isoformat(),
        confidence=confidence,
        note="",
    )
