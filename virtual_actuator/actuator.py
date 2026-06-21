"""
Virtual Actuator — Thiết bị chấp hành thùng rác thông minh (Đề tài 5, IT6130)
===============================================================================
Vai trò trong hệ thống:
  Mỗi container chạy file này đại diện cho thiết bị chấp hành của MỘT thùng.
  Actuator subscribe topic command, nhận lệnh từ gateway (SV2), cập nhật
  trạng thái nội bộ, rồi publish status phản hồi.

Luồng dữ liệu:
  gateway  ──publish──▶  waste/{bin_id}/actuator/command  (actuator subscribe)
  actuator ──publish──▶  waste/{bin_id}/actuator/status   (gateway subscribe)

Bốn thiết bị có thể điều khiển:
  lock      — khóa nắp thùng (chống chất thêm khi quá tải)
  compactor — bộ nén rác (tăng dung tích chứa)
  buzzer    — còi báo động (cảnh báo cháy/khí gas)
  dispatch  — tín hiệu điều xe thu gom (đèn/cờ hiệu)

Nguyên tắc thiết kế quan trọng:
  Actuator xử lý safety check NGAY TẠI THIẾT BỊ (không chờ gateway).
  Lý do: trong kịch bản cháy, độ trễ mạng có thể gây thương vong.
  Nếu gateway lag hoặc mất kết nối, lệnh nén rác nguy hiểm vẫn có
  thể đến — actuator phải tự từ chối.
"""
import os
import json
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt  # thư viện MQTT client cho Python


# ══════════════════════════════════════════════════════════════════════════════
# CẤU HÌNH — đọc từ biến môi trường
# ══════════════════════════════════════════════════════════════════════════════

AREA_ID = os.environ.get("AREA_ID", "district-1")
# Khu vực địa lý — nhất quán với sensor cùng thùng để dễ lọc log.

BIN_ID = os.environ.get("BIN_ID", "bin-01")
# ID thùng rác này phục vụ. Phải khớp với BIN_ID của sensor cùng thùng.

DEVICE_ID = os.environ.get("DEVICE_ID", f"actuator-{BIN_ID}")
# Client ID trong MQTT — phải duy nhất trên toàn broker.
# Đặt khác với sensor (sensor-bin-01 vs actuator-bin-01) để tránh conflict.

MQTT_BROKER = os.environ.get("MQTT_BROKER", "mosquitto")
# Tên service trong Docker network — KHÔNG dùng "localhost".

MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))

# Topic actuator lắng nghe lệnh từ gateway
TOPIC_COMMAND = f"waste/{BIN_ID}/actuator/command"

# Topic actuator publish trạng thái phản hồi
TOPIC_STATUS = f"waste/{BIN_ID}/actuator/status"


# ══════════════════════════════════════════════════════════════════════════════
# TRẠNG THÁI NỘI BỘ ACTUATOR
# ══════════════════════════════════════════════════════════════════════════════

# Whitelist các giá trị hợp lệ — dùng set để O(1) lookup
VALID_TARGETS = {"lock", "compactor", "buzzer", "dispatch"}
# Giải thích từng target:
#   lock:      relay khóa nắp điện từ — "on" khi weight > 60kg (overweight)
#   compactor: motor nén rác — "on" để tăng dung tích, CẤM khi có fire_risk
#   buzzer:    còi báo động — "on" khi temperature > 60°C (fire_risk)
#   dispatch:  đèn/cờ hiệu điều xe — "on" khi fill_level > 85%

VALID_ACTIONS = {"on", "off"}
# Chỉ hai trạng thái nhị phân — thiết bị không có trạng thái trung gian.

# Trạng thái hiện tại của tất cả thiết bị chấp hành.
# Dùng dict module-level (không phải class) vì actuator chỉ có một thùng
# duy nhất per container — không cần encapsulation phức tạp hơn.
# Tất cả bắt đầu ở "off" — trạng thái an toàn mặc định khi khởi động.
state = {
    "lock":      "off",   # "on" | "off"
    "compactor": "off",   # "on" | "off" — KHÔNG bật khi buzzer đang on
    "buzzer":    "off",   # "on" | "off"
    "dispatch":  "off",   # "on" | "off"
}

# Lý do của lệnh cuối cùng được thực thi — dùng để debug và audit.
# Ví dụ: "bin_full", "fire_risk", "overweight", "manual"
last_command_reason = "none"


# ══════════════════════════════════════════════════════════════════════════════
# HÀM TIỆN ÍCH
# ══════════════════════════════════════════════════════════════════════════════

def build_status_payload() -> dict:
    """
    Xây dựng payload status đầy đủ để publish lên MQTT.

    Tại sao publish TOÀN BỘ trạng thái thay vì chỉ trường vừa thay đổi?
    - Gateway (SV2) cần snapshot đầy đủ để ghi vào InfluxDB và
      cập nhật state store — nếu chỉ gửi delta, gateway phải tự
      merge và có thể bị desync khi message bị mất.
    - ThingsBoard hiển thị tốt hơn khi nhận toàn bộ attributes.
    - Overhead không đáng kể: payload chỉ ~200 bytes.

    Dùng dict unpacking (**state) để tránh lặp code khi thêm target mới.
    """
    return {
        "device_id":           DEVICE_ID,
        "bin_id":              BIN_ID,
        **state,                                    # lock, compactor, buzzer, dispatch
        "last_command_reason": last_command_reason,
        "timestamp":           datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def apply_command(client, payload: dict):
    """
    Xử lý một lệnh command từ gateway.

    Pipeline xử lý (5 bước theo thứ tự):
      1. Lọc bin_id — bỏ qua lệnh không phải cho thùng này
      2. Validate target — whitelist check
      3. Validate action — whitelist check
      4. Safety check — từ chối lệnh nguy hiểm ngay tại thiết bị
      5. Áp dụng lệnh và publish status

    Tại sao validate kỹ vậy?
    - MQTT là pub/sub không có schema validation — bất kỳ client nào
      cũng có thể publish lên topic command với nội dung tùy ý.
    - Trong môi trường thực tế, thiết bị IoT là "last line of defense".
    - Trong lab, giúp phát hiện bug ở gateway (SV2) sớm hơn.

    Args:
      client:  MQTT client để publish status response
      payload: dict đã parse từ JSON message nhận được
    """
    global last_command_reason

    # ── Bước 1: Lọc theo bin_id ────────────────────────────────────────────
    # Trong một số thiết kế, gateway broadcast lệnh cho nhiều thùng trên
    # cùng một topic (không khuyến khích). Kiểm tra này bảo vệ thùng
    # khỏi lệnh không dành cho mình.
    # bin_id có thể vắng mặt trong payload (gateway gửi đến đúng topic rồi)
    # → chỉ filter khi bin_id có mặt VÀ không khớp.
    bin_id = payload.get("bin_id", "")
    target = payload.get("target", "")
    action = payload.get("action", "")
    reason = payload.get("reason", "manual")  # mặc định "manual" nếu thiếu

    if bin_id and bin_id != BIN_ID:
        # Không publish error response — lệnh này không thuộc thùng mình,
        # không cần phản hồi gì cả.
        print(f"[{DEVICE_ID}] Bỏ qua lệnh cho bin khác: {bin_id}")
        return

    # ── Bước 2: Validate target ────────────────────────────────────────────
    if target not in VALID_TARGETS:
        # Publish error response để gateway biết lệnh bị reject.
        # Dùng cùng topic TOPIC_STATUS để gateway chỉ cần subscribe một nơi.
        print(f"[{DEVICE_ID}] Lỗi: target không hợp lệ '{target}' — bỏ qua")
        _publish_error(client, f"invalid_target:{target}", reason)
        return

    # ── Bước 3: Validate action ────────────────────────────────────────────
    if action not in VALID_ACTIONS:
        print(f"[{DEVICE_ID}] Lỗi: action không hợp lệ '{action}' — bỏ qua")
        _publish_error(client, f"invalid_action:{action}", reason)
        return

    # ── Bước 4: Safety check — xử lý tại thiết bị, không chờ gateway ──────
    #
    # QUY TẮC: Không được bật compactor khi buzzer đang bật.
    #
    # Lý do vật lý: Khi có fire_risk (temperature > 60°C, buzzer=on),
    # bộ nén rác tạo thêm nhiệt và ma sát → tăng nguy cơ phát nổ.
    # Gateway đã gửi buzzer=on trước, nhưng sau đó có thể gửi nhầm
    # compactor=on (bug, hoặc lệnh manual từ REST API không kiểm tra).
    #
    # Actuator từ chối tại đây thay vì để gateway xử lý vì:
    # 1. Giảm độ trễ: không cần round-trip lên gateway/cloud
    # 2. Hoạt động offline: nếu mạng chậm, lệnh vẫn bị chặn
    # 3. Defense in depth: ngay cả khi gateway có bug, thiết bị an toàn
    if target == "compactor" and action == "on" and state["buzzer"] == "on":
        print(f"[{DEVICE_ID}] SAFETY BLOCK: từ chối bật compactor khi buzzer đang on (fire_risk)")
        _publish_error(client, "safety_block:compactor_during_fire_risk", reason)
        return

    # ── Bước 5: Áp dụng lệnh và publish status ────────────────────────────
    old_value = state[target]           # lưu giá trị cũ để log
    state[target] = action              # cập nhật trạng thái nội bộ
    last_command_reason = reason        # ghi nhớ lý do cho lần publish sau

    print(
        f"[{DEVICE_ID}] CMD ✓  {target}: {old_value} → {action}  "
        f"(reason={reason})"
    )

    # Publish toàn bộ snapshot trạng thái — gateway/ThingsBoard nhận được
    # để cập nhật dashboard và ghi vào InfluxDB (measurement: actuator_status)
    status_payload = build_status_payload()
    client.publish(TOPIC_STATUS, json.dumps(status_payload), qos=1)
    print(f"[{DEVICE_ID}] STATUS → {TOPIC_STATUS}")


def _publish_error(client, error: str, reason: str):
    """
    Publish error response lên topic status khi lệnh bị reject.

    Tại sao publish error thay vì im lặng?
    - Gateway (SV2) subscribe TOPIC_STATUS để biết lệnh có được
      thực thi không. Nếu không có response, gateway không biết
      lệnh bị mất hay bị từ chối.
    - Giúp phát hiện bug trong gateway rule engine khi dev.
    - Trong log phân tán (InfluxDB/Grafana), error response là
      dấu hiệu để alert team vận hành.

    Error response dùng cùng topic TOPIC_STATUS (không tạo topic mới)
    để gateway chỉ cần một subscription để nhận cả success lẫn error.
    """
    err_payload = {
        "device_id": DEVICE_ID,
        "bin_id":    BIN_ID,
        "error":     error,    # mô tả lý do reject, ví dụ: "invalid_target:motor"
        "reason":    reason,   # lý do trong lệnh gốc
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    client.publish(TOPIC_STATUS, json.dumps(err_payload), qos=1)


# ══════════════════════════════════════════════════════════════════════════════
# MQTT CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════

def on_connect(client, _userdata, _flags, rc, _properties=None):
    """
    Callback khi kết nối MQTT broker thành công hoặc thất bại.

    Subscribe TRONG on_connect (không phải trước connect()) vì:
    Khi broker restart và client reconnect tự động, subscription
    sẽ được tái lập. Nếu subscribe ở nơi khác, sau reconnect
    actuator sẽ mất subscription và không nhận được lệnh nữa.

    Publish initial status sau khi kết nối để gateway biết actuator
    đã online và trạng thái ban đầu của tất cả thiết bị (tất cả "off").
    Điều này quan trọng nếu gateway restart — nó cần snapshot hiện tại.
    """
    if rc == 0:
        print(f"[{DEVICE_ID}] Kết nối MQTT broker thành công")

        # QoS 1 cho subscription: đảm bảo broker giao lại message
        # nếu actuator mất kết nối tạm thời trong lúc gateway gửi lệnh.
        client.subscribe(TOPIC_COMMAND, qos=1)
        print(f"[{DEVICE_ID}] Subscribe: {TOPIC_COMMAND}")

        # Broadcast trạng thái ban đầu (tất cả thiết bị đang "off")
        # giúp gateway/ThingsBoard hiển thị đúng ngay khi actuator lên line.
        client.publish(TOPIC_STATUS, json.dumps(build_status_payload()), qos=1)
    else:
        print(f"[{DEVICE_ID}] Kết nối thất bại, rc={rc}")


def on_message(client, _userdata, msg):
    """
    Callback khi nhận message trên topic TOPIC_COMMAND.

    Bắt lỗi ở 2 tầng:
    1. json.JSONDecodeError: gateway gửi sai format (bug trong SV2)
       → log riêng để dễ phân biệt với lỗi logic bên dưới.
    2. Exception chung: lỗi không mong đợi trong apply_command
       → không để crash callback, MQTT loop vẫn tiếp tục chạy.

    Không raise exception trong callback vì paho-mqtt sẽ bắt và
    bỏ qua silently — actuator sẽ tiếp tục nhận message kế tiếp.
    """
    try:
        payload = json.loads(msg.payload.decode())
        print(f"[{DEVICE_ID}] CMD nhận: {payload}")
        apply_command(client, payload)
    except json.JSONDecodeError as e:
        # Ghi rõ raw payload để debug gateway (SV2)
        print(f"[{DEVICE_ID}] Lỗi JSON: {e} — payload raw: {msg.payload}")
    except Exception as e:
        print(f"[{DEVICE_ID}] Lỗi xử lý command: {e}")


def on_disconnect(_client, _userdata, rc, _properties=None):
    """
    Callback khi mất kết nối MQTT broker.

    rc=0: disconnect chủ động (gọi client.disconnect()) — bình thường.
    rc≠0: mất kết nối đột ngột — paho với loop_forever() tự reconnect.

    Actuator không cần lưu state xuống disk vì:
    - State hiện tại đã được publish lên TOPIC_STATUS rồi (broker lưu
      retained message nếu cần — có thể bật sau).
    - Sau reconnect, gateway sẽ gửi lại lệnh cần thiết nếu hệ thống
      detect actuator offline qua heartbeat timeout.
    """
    print(f"[{DEVICE_ID}] Mất kết nối MQTT (rc={rc}), đang thử kết nối lại...")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    """
    Khởi tạo actuator và chờ lệnh vô hạn (event-driven).

    Kiến trúc threading:
      Actuator KHÔNG cần vòng lặp publish chủ động — nó chỉ phản ứng
      với message đến. Do đó dùng loop_forever() thay vì loop_start():
        - loop_forever(): block main thread, tối ưu cho subscriber thuần túy
        - loop_start(): tạo thread nền, cần thiết khi main thread còn việc khác

      Actuator không có "việc khác" → loop_forever() là lựa chọn đúng.
    """
    client = mqtt.Client(
        client_id=DEVICE_ID,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        # Không cần userdata vì actuator dùng biến module-level (state, last_command_reason)
        # thay vì class instance như sensor.
    )
    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect

    # Retry loop: chờ mosquitto sẵn sàng (Docker startup race condition)
    print(f"[{DEVICE_ID}] Khởi động — kết nối {MQTT_BROKER}:{MQTT_PORT}")
    while True:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            break
        except Exception as e:
            print(f"[{DEVICE_ID}] Chưa kết nối được: {e} — thử lại sau 5s")
            time.sleep(5)

    print(f"[{DEVICE_ID}] Đang chờ lệnh trên {TOPIC_COMMAND} ...")
    try:
        # loop_forever() block tại đây, xử lý message qua on_message callback.
        # Tự động reconnect khi mất kết nối (built-in của paho).
        client.loop_forever()
    except KeyboardInterrupt:
        print(f"[{DEVICE_ID}] Dừng.")
    finally:
        # Đảm bảo DISCONNECT packet được gửi — broker biết client offline
        # ngay lập tức thay vì chờ keepalive timeout (60s).
        client.disconnect()


if __name__ == "__main__":
    main()
