"""
Rule Engine — Edge Waste Gateway (Đề tài 5, IT6130)
=====================================================
Vai trò trong hệ thống (SV2):
  Nhận telemetry ĐÃ normalize của một thùng, áp dụng tập luật và trả về:
    1. events  — các sự kiện bất thường đang xảy ra (kèm severity, value, threshold)
    2. desired — trạng thái MONG MUỐN của từng actuator (theo mức độ, level-based)

Nguyên tắc thiết kế quan trọng:
  Rule engine là HÀM THUẦN (pure function): cùng input → cùng output, không
  publish MQTT, không ghi InfluxDB, không giữ state. Mọi副 tác (gửi command,
  phát event, ghi DB) do gateway.py điều phối. Nhờ vậy rule engine test được
  độc lập, không cần broker (đáp ứng yêu cầu nâng cao §5.17.7 unit test).

Tách bạch hai khái niệm:
  - desired (level-based): "thùng đang đầy → dispatch PHẢI on". Gateway so sánh
    với lệnh đã gửi gần nhất (debounce) để chỉ gửi khi trạng thái THAY ĐỔI,
    tránh spam command mỗi 5s. Khi điều kiện hết (fill tụt sau thu gom) →
    desired tự về "off" → gateway gửi lệnh tắt. Xử lý được cả hai chiều on/off.
  - events (edge-based): gateway chỉ phát/ghi event tại CẠNH LÊN (lần đầu điều
    kiện đúng), không lặp lại mỗi tick → biểu đồ "số event theo thời gian" của
    Grafana (§5.12 panel 5) sạch và đúng ngữ nghĩa.
"""
from dataclasses import dataclass


@dataclass
class Thresholds:
    """
    Ngưỡng của 4+ luật, đọc từ biến môi trường (không hard-code) để có thể
    chỉnh từ xa qua REST/ThingsBoard Shared Attributes (§5.17.3).
    """
    fill_dispatch: float = 85.0   # > ngưỡng này → điều xe thu gom (rule 1)
    fill_critical: float = 95.0   # > ngưỡng này → bin_full nâng lên critical
    temp_fire: float = 60.0       # > ngưỡng này → fire_risk, bật còi (rule 2)
    methane_alert: float = 500.0  # > ngưỡng này → gas_alert (rule 3)
    weight_lock: float = 60.0     # > ngưỡng này → khóa nắp chống quá tải (rule 4)


def evaluate(t: dict, th: Thresholds) -> dict:
    """
    Áp dụng tập luật lên một bản ghi telemetry đã normalize.

    Args:
      t:  dict telemetry đã normalize (có fill_level, temperature, methane_ppm,
          weight_kg, tilt ...)
      th: bộ ngưỡng Thresholds.

    Returns:
      {
        "events":  { event_type: {severity, value, threshold, action_taken, reason} },
        "desired": { target: action }    # trạng thái actuator mong muốn
      }
    """
    fill    = float(t.get("fill_level", 0.0))
    temp    = float(t.get("temperature", 0.0))
    methane = float(t.get("methane_ppm", 0.0))
    weight  = float(t.get("weight_kg", 0.0))
    tilt    = bool(t.get("tilt", False))

    events: dict = {}
    desired: dict = {}

    # ── Rule 1: Thùng đầy → điều xe thu gom ────────────────────────────────
    # fill > 85 ⇒ dispatch=on, event bin_full (warning); > 95 ⇒ critical.
    if fill > th.fill_dispatch:
        severity = "critical" if fill > th.fill_critical else "warning"
        events["bin_full"] = {
            "severity":     severity,
            "value":        round(fill, 2),
            "threshold":    th.fill_dispatch,
            "action_taken": "dispatch_on",
            "reason":       "bin_full",
        }
        desired["dispatch"] = "on"
    else:
        # Hết đầy (đã thu gom, fill tụt) → tắt tín hiệu điều xe.
        desired["dispatch"] = "off"

    # ── Rule 2: Nguy cơ cháy → bật còi, CẤM nén rác ────────────────────────
    # temperature > 60 ⇒ buzzer=on, compactor=off, event fire_risk (critical).
    # Đây là quyết định AN TOÀN nên xử lý ngay tại edge (§5.14.6); actuator
    # còn một lớp safety-check nữa (cấm compactor khi buzzer on) — defense in depth.
    if temp > th.temp_fire:
        events["fire_risk"] = {
            "severity":     "critical",
            "value":        round(temp, 2),
            "threshold":    th.temp_fire,
            "action_taken": "buzzer_on,compactor_off",
            "reason":       "fire_risk",
        }
        desired["buzzer"]    = "on"
        desired["compactor"] = "off"
    else:
        desired["buzzer"] = "off"
        # Không tự bật lại compactor khi hết cháy: compactor không được điều
        # khiển tự động ở luật nào khác → để nguyên trạng (gateway không gửi lệnh).

    # ── Rule 3: Khí methane cao → cảnh báo (gợi ý mở nắp thông khí) ────────
    # methane > 500 ⇒ event gas_alert (warning). Không có actuator thông khí
    # nên chỉ phát event để vận hành xử lý.
    if methane > th.methane_alert:
        events["gas_alert"] = {
            "severity":     "warning",
            "value":        round(methane, 2),
            "threshold":    th.methane_alert,
            "action_taken": "ventilation_suggested",
            "reason":       "gas_alert",
        }

    # ── Rule 4: Quá tải → khóa nắp chống chất thêm ─────────────────────────
    # weight_kg > 60 ⇒ lock=on, event overweight (warning).
    if weight > th.weight_lock:
        events["overweight"] = {
            "severity":     "warning",
            "value":        round(weight, 2),
            "threshold":    th.weight_lock,
            "action_taken": "lock_on",
            "reason":       "overweight",
        }
        desired["lock"] = "on"
    else:
        desired["lock"] = "off"

    # ── Rule 5 (mở rộng): Đổ nghiêng → lịch bảo trì ────────────────────────
    # tilt=true ⇒ event bin_tilted (info). Không khẩn cấp như fire_risk,
    # không sinh command — chỉ đưa vào lịch bảo trì.
    if tilt:
        events["bin_tilted"] = {
            "severity":     "info",
            "value":        1,
            "threshold":    0,
            "action_taken": "maintenance_scheduled",
            "reason":       "bin_tilted",
        }

    return {"events": events, "desired": desired}
