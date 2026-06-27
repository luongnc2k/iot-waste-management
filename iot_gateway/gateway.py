"""
Edge Waste Gateway — thành phần trung tâm (Đề tài 5, IT6130)
==============================================================
Vai trò trong hệ thống (SV2):
  Là "bộ não" tại biên (edge). Một mình gateway này phục vụ TẤT CẢ các thùng:
    1. Subscribe wildcard toàn bộ telemetry + status của mọi thùng.
    2. Validate + normalize telemetry → publish lên gateway/normalized.
    3. Chạy rule engine → gửi command tự động xuống actuator + phát event.
    4. Ghi telemetry/event/status vào InfluxDB.
    5. Duy trì danh sách thùng cần thu gom và mô phỏng vòng đời thu gom.
    6. Phát hiện sensor offline.

Luồng dữ liệu:
  waste/+/sensor/telemetry  ──▶ gateway ──▶ waste/{bin}/gateway/normalized
                                          ──▶ waste/{bin}/gateway/event
                                          ──▶ waste/{bin}/actuator/command
                                          ──▶ waste/{bin}/sensor/reset (sau thu gom)
  waste/+/actuator/status   ──▶ gateway (lưu state + ghi InfluxDB)

Vì sao gateway tại biên thay vì mỗi sensor gửi thẳng cloud? (§5.14.6)
  - Độ trễ: quyết định an toàn (báo cháy) phải tức thì, không round-trip cloud.
  - Mất kết nối: vẫn điều khiển được cục bộ khi internet chập chờn.
  - Gộp kết nối: 1 gateway đại diện nhiều thùng lên ThingsBoard (giảm tải).

Kiến trúc threading:
  - loop_start(): thread mạng xử lý message đến (telemetry/status) qua callback.
  - main thread: vòng lặp bảo trì định kỳ (offline-detect + mô phỏng thu gom).
"""
import os
import json
import time
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from rule_engine import Thresholds, evaluate
from state_store import StateStore
from influx_writer import InfluxWriter


# ══════════════════════════════════════════════════════════════════════════════
# CẤU HÌNH — đọc từ biến môi trường (12-factor, không hard-code)
# ══════════════════════════════════════════════════════════════════════════════

GATEWAY_ID  = os.environ.get("GATEWAY_ID", "waste-gateway")
MQTT_BROKER = os.environ.get("MQTT_BROKER", "mosquitto")
MQTT_PORT   = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER   = os.environ.get("MQTT_USER", "")
MQTT_PASS   = os.environ.get("MQTT_PASS", "")

INFLUXDB_URL    = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
INFLUXDB_TOKEN  = os.environ.get("INFLUXDB_TOKEN", "")
INFLUXDB_ORG    = os.environ.get("INFLUXDB_ORG", "hust")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "iot")

# Ngưỡng rule engine — cấu hình được để chỉnh từ xa (§5.17.3).
THRESHOLDS = Thresholds(
    fill_dispatch=float(os.environ.get("FILL_DISPATCH_THRESHOLD", "85")),
    fill_critical=float(os.environ.get("FILL_CRITICAL_THRESHOLD", "95")),
    temp_fire=float(os.environ.get("TEMP_FIRE_THRESHOLD", "60")),
    methane_alert=float(os.environ.get("METHANE_ALERT_THRESHOLD", "500")),
    weight_lock=float(os.environ.get("WEIGHT_LOCK_THRESHOLD", "60")),
)

# Sau bao lâu không nhận telemetry thì coi sensor offline (rule 5 mở rộng).
SENSOR_OFFLINE_TIMEOUT = float(os.environ.get("SENSOR_OFFLINE_TIMEOUT", "30"))

# Mô phỏng: sau khi điều xe (dispatch=on), bao lâu thì xe tới nơi & thu gom xong
# → gateway publish reset cho sensor, đóng vòng đời. (Trong thực tế là tín hiệu
#  từ xe thu gom; ở lab ta mô phỏng bằng timer để demo chu kỳ đầy→thu gom→reset.)
COLLECTION_DELAY = float(os.environ.get("COLLECTION_DELAY", "20"))

# Chu kỳ chạy vòng lặp bảo trì (offline-detect + thu gom).
MAINTENANCE_INTERVAL = float(os.environ.get("MAINTENANCE_INTERVAL", "5"))

# Webhook URL để thông báo khi sự kiện critical xảy ra (§5.17.5 Alarm+thông báo).
# Bỏ trống = tắt, có thể dùng webhook.site, Discord, Slack, n8n, hay bất kỳ URL nào.
# Ví dụ: WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
#         WEBHOOK_URL=https://webhook.site/xxxx
WEBHOOK_URL      = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_SEVERITY = os.environ.get("WEBHOOK_SEVERITY", "critical")  # "critical" | "warning" | "all"

# Wildcard subscribe — 1 lệnh bắt mọi thùng (lý do thiết kế topic của SV1).
TOPIC_TELEMETRY_WILDCARD = "waste/+/sensor/telemetry"
TOPIC_STATUS_WILDCARD    = "waste/+/actuator/status"

# Topic config runtime — REST API (SV3 POST /config) và ThingsBoard Shared
# Attributes (qua tb_gateway.py) đều publish ngưỡng mới (retained) lên đây.
# Gateway subscribe để chỉnh THRESHOLDS không cần restart container (§5.17.3).
TOPIC_CONFIG = "waste/gateway/config"

# Mapping tên biến môi trường ↔ field của Thresholds — dùng để áp dụng
# update runtime từ payload {"thresholds": {"FILL_DISPATCH_THRESHOLD": 80, ...}}.
_THRESHOLD_ENV_TO_FIELD = {
    "FILL_DISPATCH_THRESHOLD":  "fill_dispatch",
    "FILL_CRITICAL_THRESHOLD":  "fill_critical",
    "TEMP_FIRE_THRESHOLD":      "temp_fire",
    "METHANE_ALERT_THRESHOLD":  "methane_alert",
    "WEIGHT_LOCK_THRESHOLD":    "weight_lock",
}


# ══════════════════════════════════════════════════════════════════════════════
# HÀM TIỆN ÍCH
# ══════════════════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _send_webhook(event_payload: dict) -> None:
    """Gửi thông báo HTTP POST tới WEBHOOK_URL khi có sự kiện đáng chú ý.

    Chạy trên một thread riêng để không block callback MQTT. Lỗi mạng được
    log ra console nhưng KHÔNG raise — gateway tiếp tục chạy bình thường.
    Payload tương thích với Slack Incoming Webhooks, Discord Webhooks, và
    dịch vụ HTTP-to-SMS/Telegram như n8n hoặc webhook.site.
    """
    if not WEBHOOK_URL:
        return
    severity = event_payload.get("severity", "")
    if WEBHOOK_SEVERITY != "all" and severity != WEBHOOK_SEVERITY:
        return

    bin_id = event_payload.get("bin_id", "?")
    event_type = event_payload.get("event_type", "?")
    value = event_payload.get("value", "?")
    threshold = event_payload.get("threshold", "?")
    ts = event_payload.get("timestamp", _now_iso())

    # Slack / Discord / generic webhook — cùng field "text" và "content"
    body = json.dumps({
        "text": (
            f"🚨 *[{severity.upper()}]* Thùng `{bin_id}` — {event_type}\n"
            f"Giá trị: {value} | Ngưỡng: {threshold} | {ts}"
        ),
        "content": (
            f"🚨 [{severity.upper()}] Thùng {bin_id} — {event_type} "
            f"(val={value} thr={threshold}) {ts}"
        ),
    }).encode()

    def _post():
        try:
            req = urllib.request.Request(
                WEBHOOK_URL, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                print(f"[{GATEWAY_ID}] WEBHOOK {resp.status} ← {event_type} {bin_id}")
        except urllib.error.URLError as exc:
            print(f"[{GATEWAY_ID}] WEBHOOK ERROR: {exc}")

    threading.Thread(target=_post, daemon=True).start()


def _bin_id_from_topic(topic: str) -> str:
    """waste/bin-01/sensor/telemetry → 'bin-01' (phần tử thứ 2)."""
    parts = topic.split("/")
    return parts[1] if len(parts) >= 2 else ""


# Field bắt buộc của telemetry — thiếu bất kỳ field nào → loại bỏ message.
_REQUIRED_FIELDS = ("bin_id", "fill_level", "weight_kg", "methane_ppm",
                    "temperature", "timestamp")


def validate_and_normalize(raw: dict, bin_from_topic: str) -> dict | None:
    """
    Validate telemetry thô và trả về bản ĐÃ NORMALIZE, hoặc None nếu không hợp lệ.

    Validate (§5.9):
      - đủ field bắt buộc
      - bin_id trong payload khớp bin_id trong topic (chống nhầm/giả mạo)
      - timestamp đúng ISO 8601
    Normalize:
      - ép kiểu số, làm tròn, clamp fill về [0, 100]
      - phân loại fill_status (low/medium/high) cho dashboard
      - gắn thêm thời điểm gateway nhận (phục vụ truy vết độ trễ)
    """
    for f in _REQUIRED_FIELDS:
        if f not in raw:
            print(f"[{GATEWAY_ID}] DROP telemetry thiếu field '{f}': {raw}")
            return None

    bin_id = raw["bin_id"]
    if bin_from_topic and bin_id != bin_from_topic:
        print(f"[{GATEWAY_ID}] DROP telemetry bin_id lệch topic "
              f"(payload={bin_id}, topic={bin_from_topic})")
        return None

    try:
        datetime.strptime(raw["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        print(f"[{GATEWAY_ID}] DROP telemetry timestamp sai định dạng: {raw.get('timestamp')}")
        return None

    try:
        fill = max(0.0, min(100.0, float(raw["fill_level"])))
        norm = {
            "area_id":     raw.get("area_id", "unknown"),
            "bin_id":      bin_id,
            "fill_level":  round(fill, 2),
            "weight_kg":   round(float(raw["weight_kg"]), 2),
            "methane_ppm": round(float(raw["methane_ppm"]), 2),
            "temperature": round(float(raw["temperature"]), 2),
            "lid_status":  raw.get("lid_status", "closed"),
            "tilt":        bool(raw.get("tilt", False)),
            "fill_status": "high" if fill > 85 else ("medium" if fill >= 50 else "low"),
            "source_timestamp":   raw["timestamp"],
            "gateway_received_at": _now_iso(),
        }
        return norm
    except (ValueError, TypeError) as e:
        print(f"[{GATEWAY_ID}] DROP telemetry giá trị số không hợp lệ: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# XỬ LÝ TELEMETRY: normalize → store → rule engine → command/event → InfluxDB
# ══════════════════════════════════════════════════════════════════════════════

def handle_telemetry(client, store: StateStore, influx: InfluxWriter, topic: str, raw: dict):
    bin_id = _bin_id_from_topic(topic)
    norm = validate_and_normalize(raw, bin_id)
    if norm is None:
        return

    # 1) Lưu state mới nhất + đánh dấu thùng còn sống
    store.update_telemetry(bin_id, norm)

    # 2) Publish dữ liệu đã normalize cho các consumer khác (SV3/ThingsBoard)
    client.publish(f"waste/{bin_id}/gateway/normalized", json.dumps(norm), qos=1)

    # 3) Ghi telemetry vào InfluxDB
    influx.write_telemetry(norm)

    # 4) Chạy rule engine
    result = evaluate(norm, THRESHOLDS)
    events = result["events"]
    desired = result["desired"]

    # 4a) EVENT — chỉ phát tại CẠNH LÊN (event vừa active)
    fired = store.newly_fired_events(bin_id, set(events.keys()))
    for et in fired:
        ev = events[et]
        event_payload = {
            "bin_id":       bin_id,
            "event_type":   et,
            "severity":     ev["severity"],
            "value":        ev["value"],
            "threshold":    ev["threshold"],
            "action_taken": ev["action_taken"],
            "timestamp":    _now_iso(),
        }
        client.publish(f"waste/{bin_id}/gateway/event", json.dumps(event_payload), qos=1)
        influx.write_event(bin_id, et, ev)
        _send_webhook(event_payload)
        print(f"[{GATEWAY_ID}] EVENT {bin_id}: {et} ({ev['severity']}) "
              f"value={ev['value']} thr={ev['threshold']}")

    # 4b) COMMAND — chỉ gửi khi trạng thái mong muốn THAY ĐỔI (debounce)
    for target, action in desired.items():
        if store.command_changed(bin_id, target, action):
            reason = events.get(
                {"dispatch": "bin_full", "buzzer": "fire_risk",
                 "compactor": "fire_risk", "lock": "overweight"}.get(target, ""),
                {}
            ).get("reason", "auto")
            cmd = {
                "bin_id":    bin_id,
                "target":    target,
                "action":    action,
                "reason":    reason,
                "timestamp": _now_iso(),
            }
            client.publish(f"waste/{bin_id}/actuator/command", json.dumps(cmd), qos=1)
            print(f"[{GATEWAY_ID}] CMD  {bin_id}: {target}={action} (reason={reason})")

    # 5) Duy trì danh sách thu gom
    if desired.get("dispatch") == "on":
        store.mark_for_collection(bin_id)


def handle_status(store: StateStore, influx: InfluxWriter, topic: str, status: dict):
    """Nhận status phản hồi từ actuator → lưu state + ghi InfluxDB."""
    bin_id = _bin_id_from_topic(topic)
    store.update_actuator(bin_id, status)
    influx.write_actuator_status(bin_id, status)


def handle_config_update(payload: dict):
    """
    Áp dụng ngưỡng rule engine mới (runtime, không cần restart container).

    Nguồn payload (§5.17.3, hoàn thiện vòng lặp SV3 đã thiết kế nhưng chưa
    đấu nối — REST API POST /config và ThingsBoard Shared Attributes qua
    tb_gateway.py đều publish (retained) lên TOPIC_CONFIG, trước đây không
    có ai subscribe nên không có tác dụng thật):
      {"thresholds": {"TEMP_FIRE_THRESHOLD": 55, "WEIGHT_LOCK_THRESHOLD": 50}}

    Bỏ qua key không thuộc _THRESHOLD_ENV_TO_FIELD và giá trị không ép được
    về float — không để một payload sai làm rule engine crash.
    THRESHOLDS là dataclass mutable dùng chung với evaluate(), nên setattr
    ở đây có hiệu lực ngay từ bản telemetry kế tiếp.
    """
    updates = payload.get("thresholds", {})
    applied = {}
    for env_key, value in updates.items():
        field = _THRESHOLD_ENV_TO_FIELD.get(env_key)
        if field is None:
            continue
        try:
            setattr(THRESHOLDS, field, float(value))
            applied[field] = float(value)
        except (TypeError, ValueError):
            print(f"[{GATEWAY_ID}] Bỏ qua threshold không hợp lệ {env_key}={value!r}")
    if applied:
        print(f"[{GATEWAY_ID}] CONFIG cập nhật ngưỡng runtime: {applied}")


# ══════════════════════════════════════════════════════════════════════════════
# VÒNG LẶP BẢO TRÌ (main thread): offline-detect + mô phỏng thu gom
# ══════════════════════════════════════════════════════════════════════════════

def maintenance_loop(client, store: StateStore, influx: InfluxWriter, stop: threading.Event):
    while not stop.is_set():
        # ── Mô phỏng xe thu gom: thùng nằm trong danh sách quá COLLECTION_DELAY
        #    → publish reset cho sensor, đóng vòng đời đầy→thu gom→reset.
        for bin_id in store.due_for_collection(COLLECTION_DELAY):
            reset_payload = {"action": "reset", "timestamp": _now_iso()}
            client.publish(f"waste/{bin_id}/sensor/reset", json.dumps(reset_payload), qos=1)
            store.clear_collection(bin_id)
            print(f"[{GATEWAY_ID}] COLLECTED {bin_id} → publish reset cho sensor")

        # ── Phát hiện sensor offline / phục hồi
        newly_offline, recovered = store.offline_transitions(SENSOR_OFFLINE_TIMEOUT)
        for bin_id in newly_offline:
            ev = {"severity": "warning", "value": SENSOR_OFFLINE_TIMEOUT,
                  "threshold": SENSOR_OFFLINE_TIMEOUT, "action_taken": "alert_ops"}
            payload = {
                "bin_id": bin_id, "event_type": "sensor_offline",
                "severity": "warning", "value": SENSOR_OFFLINE_TIMEOUT,
                "threshold": SENSOR_OFFLINE_TIMEOUT, "action_taken": "alert_ops",
                "timestamp": _now_iso(),
            }
            client.publish(f"waste/{bin_id}/gateway/event", json.dumps(payload), qos=1)
            influx.write_event(bin_id, "sensor_offline", ev)
            _send_webhook(payload)
            print(f"[{GATEWAY_ID}] EVENT {bin_id}: sensor_offline (warning)")
        for bin_id in recovered:
            print(f"[{GATEWAY_ID}] {bin_id} đã gửi telemetry trở lại (online)")

        stop.wait(MAINTENANCE_INTERVAL)


# ══════════════════════════════════════════════════════════════════════════════
# MQTT CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════

def on_connect(client, _userdata, _flags, rc, _properties=None):
    if rc == 0:
        print(f"[{GATEWAY_ID}] Kết nối MQTT broker thành công")
        # Subscribe trong on_connect để tái lập sau reconnect (như SV1).
        client.subscribe(TOPIC_TELEMETRY_WILDCARD, qos=1)
        client.subscribe(TOPIC_STATUS_WILDCARD, qos=1)
        client.subscribe(TOPIC_CONFIG, qos=1)
        print(f"[{GATEWAY_ID}] Subscribe: {TOPIC_TELEMETRY_WILDCARD} , {TOPIC_STATUS_WILDCARD} , {TOPIC_CONFIG}")
    else:
        print(f"[{GATEWAY_ID}] Kết nối thất bại, rc={rc}")


def on_message(client, userdata, msg):
    """
    Định tuyến message theo topic. userdata mang {store, influx} để callback
    truy cập mà không cần biến global (thread-safe, dễ test).
    """
    store = userdata["store"]
    influx = userdata["influx"]
    try:
        payload = json.loads(msg.payload.decode())
    except json.JSONDecodeError as e:
        print(f"[{GATEWAY_ID}] Lỗi JSON trên {msg.topic}: {e}")
        return

    try:
        if msg.topic.endswith("/sensor/telemetry"):
            handle_telemetry(client, store, influx, msg.topic, payload)
        elif msg.topic.endswith("/actuator/status"):
            handle_status(store, influx, msg.topic, payload)
        elif msg.topic == TOPIC_CONFIG:
            handle_config_update(payload)
    except Exception as e:
        # Không để một message lỗi làm chết MQTT loop.
        print(f"[{GATEWAY_ID}] Lỗi xử lý {msg.topic}: {e}")


def on_disconnect(_client, _userdata, _flags, reason_code, _properties=None):
    # Chữ ký 5 tham số cho CallbackAPIVersion.VERSION2 (xem bài học từ SV1).
    print(f"[{GATEWAY_ID}] Mất kết nối MQTT (rc={reason_code}), đang thử kết nối lại...")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    store = StateStore()
    influx = InfluxWriter(INFLUXDB_URL, INFLUXDB_TOKEN, INFLUXDB_ORG, INFLUXDB_BUCKET)

    client = mqtt.Client(
        client_id=GATEWAY_ID,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        userdata={"store": store, "influx": influx},
    )
    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect

    print(f"[{GATEWAY_ID}] Khởi động — kết nối {MQTT_BROKER}:{MQTT_PORT}")
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    while True:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            break
        except Exception as e:
            print(f"[{GATEWAY_ID}] Chưa kết nối được: {e} — thử lại sau 5s")
            time.sleep(5)

    client.loop_start()  # thread mạng xử lý telemetry/status đến

    stop = threading.Event()
    print(f"[{GATEWAY_ID}] Bắt đầu giám sát. offline_timeout={SENSOR_OFFLINE_TIMEOUT}s "
          f"collection_delay={COLLECTION_DELAY}s")
    try:
        maintenance_loop(client, store, influx, stop)  # block tại main thread
    except KeyboardInterrupt:
        print(f"[{GATEWAY_ID}] Dừng.")
    finally:
        stop.set()
        client.loop_stop()
        client.disconnect()
        influx.close()


if __name__ == "__main__":
    main()
