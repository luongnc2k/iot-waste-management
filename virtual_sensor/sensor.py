"""
Virtual Sensor — Thùng rác thông minh (Đề tài 5, IT6130)
=========================================================
Vai trò trong hệ thống:
  Mỗi container chạy file này đại diện cho MỘT thùng rác vật lý.
  Sensor liên tục đo và publish dữ liệu thô lên MQTT broker.
  Gateway (SV2) sẽ subscribe topic này để xử lý — sensor không
  biết gateway tồn tại, chỉ biết MQTT broker.

Luồng dữ liệu:
  sensor.py  ──publish──▶  waste/{bin_id}/sensor/telemetry
  sensor.py  ◀──subscribe── waste/{bin_id}/sensor/reset   (từ gateway)

Nguyên tắc mô phỏng quan trọng:
  fill_level PHẢI tăng dần có xu hướng (không random độc lập).
  Lý do: thùng rác thực tế chỉ tăng khi rác bỏ vào, giảm khi
  xe thu gom — không thể tự vơi rồi đầy ngẫu nhiên.
"""
import os
import json
import time
import random
from datetime import datetime, timezone

import paho.mqtt.client as mqtt  # thư viện MQTT client cho Python


# ══════════════════════════════════════════════════════════════════════════════
# CẤU HÌNH — đọc từ biến môi trường (environment variables)
# Không hard-code để mỗi container có thể cấu hình khác nhau
# mà không cần sửa code (tuân thủ 12-factor app).
# ══════════════════════════════════════════════════════════════════════════════

AREA_ID = os.environ.get("AREA_ID", "district-1")
# Khu vực địa lý chứa thùng, dùng để nhóm nhiều thùng trong cùng quận/phường.
# Ví dụ: "district-1", "ward-5", "campus-hust"

BIN_ID = os.environ.get("BIN_ID", "bin-01")
# ID duy nhất của thùng rác này. Được nhúng vào MQTT topic để
# gateway phân biệt dữ liệu từng thùng khi dùng wildcard subscribe.
# Ví dụ: "bin-01", "bin-02", "bin-03"

DEVICE_ID = os.environ.get("DEVICE_ID", f"sensor-{BIN_ID}")
# ID của thiết bị cảm biến (client_id trong MQTT).
# Broker yêu cầu mỗi client có client_id duy nhất —
# nếu hai client cùng ID kết nối, broker sẽ đá client cũ ra.

MQTT_BROKER = os.environ.get("MQTT_BROKER", "mosquitto")
# Hostname của MQTT broker trong Docker network.
# Dùng tên service ("mosquitto"), KHÔNG dùng "localhost" —
# trong Docker Compose, mỗi container có IP riêng, "localhost"
# trỏ về chính container đó chứ không phải container mosquitto.

MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
# Port mặc định của MQTT (unencrypted). Port 8883 dùng cho MQTT/TLS.

PUBLISH_INTERVAL = float(os.environ.get("PUBLISH_INTERVAL", "5"))
# Chu kỳ publish dữ liệu (giây). Giá trị nhỏ → dữ liệu dày đặc hơn
# nhưng tốn băng thông. Trong lab dùng 2–5s để thấy kết quả nhanh.

# Topic publish telemetry — cố định sau khi đọc BIN_ID từ env.
# Pattern: waste/{bin_id}/sensor/telemetry
# Gateway subscribe: waste/+/sensor/telemetry  (+ là wildcard 1 cấp)
TOPIC_TELEMETRY = f"waste/{BIN_ID}/sensor/telemetry"


# ══════════════════════════════════════════════════════════════════════════════
# LỚP TRẠNG THÁI THÙNG RÁC
# Tách riêng logic mô phỏng vào class để dễ test độc lập (không cần MQTT).
# ══════════════════════════════════════════════════════════════════════════════

class BinState:
    """
    Mô phỏng trạng thái vật lý của một thùng rác thông minh.

    Mỗi lần gọi tick() sẽ cập nhật tất cả các trường theo mô hình
    vật lý đơn giản và trả về dict payload sẵn sàng để publish.
    """

    def __init__(self):
        # ── Giá trị ban đầu ────────────────────────────────────────────────
        # Khởi tạo ngẫu nhiên để 3 bin không đồng bộ với nhau —
        # nếu cả 3 bắt đầu ở 0%, chúng sẽ đạt ngưỡng cùng lúc,
        # gây "giả" tắc nghẽn dispatch cùng một thời điểm.
        self.fill_level = random.uniform(5.0, 20.0)     # % mức đầy (0–100)
        self.weight_kg  = self.fill_level * 0.5         # kg; tỉ lệ ~0.5 kg/1% fill
        self.methane_ppm = random.uniform(50.0, 150.0)  # ppm; nền tối thiểu ~50 ppm
        self.temperature = random.uniform(28.0, 35.0)   # °C; nhiệt độ môi trường
        self.lid_status  = "closed"                     # "open" | "closed"
        self.tilt        = False                        # True khi thùng bị đổ nghiêng

        # ── Tham số nội bộ (không publish ra ngoài) ────────────────────────
        # Tốc độ tăng mức đầy: mô phỏng lượng rác bỏ vào mỗi chu kỳ.
        # Khởi tạo khác nhau để các thùng đầy ở thời điểm khác nhau.
        self._fill_rate = random.uniform(0.3, 0.8)      # %/publish_interval

        self._tick = 0          # đếm số lần tick, dùng để debug
        self._collected = False  # cờ đánh dấu vừa được thu gom (dùng nội bộ)

    # ── Các phương thức mô phỏng (private) ────────────────────────────────

    def _simulate_fill(self):
        """
        Cập nhật mức đầy theo xu hướng tăng dần.

        Công thức: fill += fill_rate + nhiễu_Gauss
        - fill_rate: hằng số dương → đảm bảo fill luôn tăng về dài hạn
        - nhiễu Gauss(0, 0.1): dao động nhỏ quanh trend, mô phỏng lượng
          rác bỏ vào không đều từng chu kỳ (đôi khi nhiều, đôi khi ít)

        Tại sao KHÔNG dùng random.uniform() độc lập:
          Nếu fill ở tick 1 = 45%, tick 2 = 12% — thùng rác không thể
          tự vơi mà không có xe thu gom. Dữ liệu như vậy sẽ bị gateway
          reject hoặc sinh false alarm "xe vừa thu gom mà sao lại đầy?"
        """
        noise = random.gauss(0, 0.1)                    # nhiễu nhỏ: ±0.1% trung bình
        self.fill_level += self._fill_rate + noise
        self.fill_level = max(0.0, min(100.0, self.fill_level))  # clamp về [0, 100]

        # Cân nặng tỉ lệ thuận với mức đầy + nhiễu nhỏ (sai số cân)
        self.weight_kg = self.fill_level * 0.5 + random.gauss(0, 0.3)
        self.weight_kg = max(0.0, self.weight_kg)       # cân nặng không âm

    def _simulate_methane(self):
        """
        Mô phỏng nồng độ khí methane trong thùng.

        Methane sinh ra từ quá trình phân hủy rác hữu cơ — vì vậy
        tương quan dương với mức đầy: thùng đầy hơn → nhiều rác phân hủy
        → nhiều methane.

        Công thức baseline: methane = 50 + fill_level × 5 (ppm)
        - fill=0%   → ~50 ppm (nền không khí bình thường)
        - fill=50%  → ~300 ppm (nồng độ trung bình)
        - fill=100% → ~550 ppm (gần ngưỡng nguy hiểm 500 ppm)

        Spike (~3% xác suất/tick): mô phỏng túi khí tích tụ bị vỡ
        đột ngột, tạo nồng độ methane rất cao trong thời gian ngắn.
        Gateway rule engine phải phát hiện được spike này.
        """
        # Giá trị nền tăng tuyến tính theo mức đầy
        base = 50.0 + self.fill_level * 5.0

        # Nhiễu Gauss mô phỏng biến động nồng độ bình thường
        self.methane_ppm = base + random.gauss(0, 20)

        # Spike đột biến: 3% xác suất mỗi tick
        # Tăng 300–500 ppm → vượt ngưỡng gas_alert (500 ppm) của rule engine
        if random.random() < 0.03:
            self.methane_ppm += random.uniform(300, 500)

        self.methane_ppm = max(0.0, self.methane_ppm)   # không âm

    def _simulate_temperature(self):
        """
        Mô phỏng nhiệt độ bên trong thùng rác.

        Nhiệt độ chịu ảnh hưởng của 2 yếu tố:
        1. Nhiệt môi trường theo giờ: 10h–15h nắng gắt làm tăng ~3°C.
           (Dùng UTC hour — trong môi trường thực cần điều chỉnh timezone)

        2. Nguy cơ cháy từ methane cao: Nếu methane > 400 ppm, có 2%
           xác suất mỗi tick nhiệt độ tăng vọt 20–35°C — mô phỏng ủ
           nhiệt khi rác bắt đầu cháy âm ỉ bên trong thùng kín.
           Khi temperature > 60°C, rule engine sẽ kích hoạt fire_risk.

        Hai ngưỡng quan trọng cho rule engine (SV2):
          > 38.5°C → fever event (ít dùng với rác)
          > 60.0°C → fire_risk → buzzer=on, cấm compactor
        """
        hour = datetime.now(timezone.utc).hour

        # Hệ số nhiệt môi trường: ban ngày nắng làm thùng nóng hơn
        heat_factor = 3.0 if 10 <= hour <= 15 else 0.0
        base = 30.0 + heat_factor

        # Nhiễu nhiệt bình thường (±1.5°C std)
        self.temperature = base + random.gauss(0, 1.5)

        # Đường dẫn đến fire_risk: methane cao → có thể bốc cháy
        # Điều kiện: methane đã > 400 ppm VÀ xác suất 2%/tick
        if self.methane_ppm > 400 and random.random() < 0.02:
            self.temperature += random.uniform(20, 35)  # leo thang nhiệt đột ngột

        # Nhiệt độ tối thiểu: môi trường không thể âm trong bối cảnh này
        self.temperature = max(20.0, self.temperature)

    def _simulate_tilt(self):
        """
        Mô phỏng trạng thái đổ nghiêng của thùng rác.

        Thùng dễ đổ hơn khi quá nặng (rác đầy chèn không đều).
        - Điều kiện xảy ra: weight > 55 kg VÀ xác suất 2%/tick
        - Tự phục hồi (bảo trì xử lý): 30% xác suất/tick khi đang tilt

        Khi tilt=True → gateway sinh event bin_tilted, severity info
        → đưa vào lịch bảo trì (không khẩn cấp như fire_risk).
        """
        if self.weight_kg > 55 and random.random() < 0.02:
            self.tilt = True
        elif self.tilt and random.random() < 0.3:
            # Giả định đội bảo trì đã dựng lại thùng
            self.tilt = False

    def _simulate_lid(self):
        """
        Mô phỏng trạng thái nắp thùng.

        15% xác suất mỗi tick nắp đang mở — mô phỏng người dùng
        vừa bỏ rác vào. Nắp mở không trực tiếp kích hoạt rule nào
        nhưng cung cấp context (sensor có người dùng tương tác).

        Giả định đơn giản: nắp luôn đóng lại sau mỗi tick (người
        dùng không bỏ rác liên tục nhiều chu kỳ).
        """
        if random.random() < 0.15:
            self.lid_status = "open"
        else:
            self.lid_status = "closed"

    # ── Phương thức public ────────────────────────────────────────────────

    def reset_after_collection(self):
        """
        Reset trạng thái thùng sau khi xe thu gom đã lấy rác.

        Được gọi khi nhận lệnh reset từ gateway qua topic:
          waste/{bin_id}/sensor/reset

        Tại sao reset về 0.5–3.0% thay vì 0%?
        Vì thùng rác thực tế không bao giờ hoàn toàn trống —
        luôn còn cặn, mùi, và một ít rác vương lại sau khi đổ.
        Giá trị 0% tuyệt đối sẽ trông không tự nhiên.
        """
        self.fill_level  = random.uniform(0.5, 3.0)
        self.weight_kg   = self.fill_level * 0.5
        self.methane_ppm = random.uniform(50, 100)      # về gần nền không khí
        self.temperature = random.uniform(28, 33)       # nhiệt độ bình thường
        self.tilt        = False                        # thùng được đặt lại thẳng
        self._collected  = False
        print(f"[{DEVICE_ID}] Thùng đã được thu gom — reset về {self.fill_level:.1f}%")

    def tick(self) -> dict:
        """
        Thực hiện một chu kỳ mô phỏng và trả về payload JSON.

        Thứ tự gọi các hàm có ý nghĩa:
          1. fill trước → methane dùng fill mới nhất
          2. methane trước → temperature dùng methane mới nhất
             (quan trọng cho kịch bản fire_risk: methane spike → temp spike
              có thể xảy ra trong cùng một tick)
          3. tilt và lid độc lập — không phụ thuộc thứ tự

        Trả về dict sẵn sàng json.dumps() và publish lên MQTT.
        Tất cả số thực được làm tròn 2 chữ số thập phân để
        tránh trailing zeros gây payload to không cần thiết.
        """
        self._tick += 1
        self._simulate_fill()        # phải gọi trước methane
        self._simulate_methane()     # phải gọi trước temperature
        self._simulate_temperature() # dùng methane_ppm vừa cập nhật
        self._simulate_tilt()
        self._simulate_lid()

        return {
            "device_id":   DEVICE_ID,
            "area_id":     AREA_ID,
            "bin_id":      BIN_ID,
            "fill_level":  round(self.fill_level, 2),   # % [0, 100]
            "weight_kg":   round(self.weight_kg, 2),    # kg
            "methane_ppm": round(self.methane_ppm, 2),  # ppm; ngưỡng alert: 500
            "temperature": round(self.temperature, 2),  # °C; ngưỡng fire: 60
            "lid_status":  self.lid_status,              # "open" | "closed"
            "tilt":        self.tilt,                    # bool
            "timestamp":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            # ISO 8601 UTC — gateway dùng timestamp này để phát hiện sensor offline
            # (nếu timestamp quá cũ → sensor_offline event)
        }


# ══════════════════════════════════════════════════════════════════════════════
# MQTT CALLBACKS
# paho-mqtt gọi các hàm này từ thread network (loop_start tạo thread riêng).
# Tham số có tiền tố _ là unused (pylance convention).
# ══════════════════════════════════════════════════════════════════════════════

def on_connect(client, _userdata, _flags, rc, _properties=None):
    """
    Callback khi kết nối MQTT broker thành công hoặc thất bại.

    Tham số rc (return code):
      0 = kết nối thành công
      1 = phiên bản protocol sai
      2 = client ID không hợp lệ
      3 = server không sẵn sàng
      4 = sai username/password
      5 = không có quyền truy cập

    Việc subscribe PHẢI làm trong on_connect, không phải trước connect(),
    vì nếu broker restart và client reconnect tự động, subscription
    sẽ được tái lập — nếu subscribe ở nơi khác sẽ bị mất sau reconnect.
    """
    if rc == 0:
        print(f"[{DEVICE_ID}] Kết nối MQTT broker thành công")
        # Subscribe topic reset để nhận thông báo từ gateway
        # khi xe thu gom đã lấy rác khỏi thùng này.
        reset_topic = f"waste/{BIN_ID}/sensor/reset"
        client.subscribe(reset_topic)
        print(f"[{DEVICE_ID}] Subscribe: {reset_topic}")
    else:
        print(f"[{DEVICE_ID}] Kết nối thất bại, rc={rc}")


def on_message(_client, userdata, msg):
    """
    Callback khi nhận message trên topic đã subscribe.

    Hiện tại sensor chỉ subscribe topic reset, nên mọi message
    nhận được đều là lệnh reset từ gateway.

    userdata["state"] chứa BinState instance — được truyền vào
    khi tạo mqtt.Client, cho phép callback truy cập state
    mà không cần biến global (thread-safe hơn).

    Format lệnh reset mong đợi:
      {"action": "reset"}
    Nếu action khác → bỏ qua (forward-compatible với các lệnh mới sau này).
    """
    try:
        payload = json.loads(msg.payload.decode())
        if payload.get("action") == "reset":
            userdata["state"].reset_after_collection()
    except Exception as e:
        # Không raise exception trong callback — paho sẽ bỏ qua silently
        # nhưng log để debug khi gateway gửi sai format.
        print(f"[{DEVICE_ID}] Lỗi parse message reset: {e}")


def on_disconnect(_client, _userdata, rc, _properties=None):
    """
    Callback khi mất kết nối MQTT broker.

    rc=0: disconnect chủ động (client.disconnect() được gọi) — bình thường.
    rc≠0: mất kết nối đột ngột (network error, broker restart) — sẽ tự reconnect.

    paho-mqtt với loop_start() tự động reconnect sau on_disconnect.
    Callback này chỉ để log, không cần xử lý thêm.
    """
    print(f"[{DEVICE_ID}] Mất kết nối MQTT (rc={rc}), đang thử kết nối lại...")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    """
    Khởi tạo sensor và chạy vòng lặp publish vô hạn.

    Kiến trúc threading:
      - loop_start(): tạo thread riêng xử lý MQTT network I/O
        (nhận message, gửi PINGREQ keepalive, xử lý reconnect)
      - Main thread: vòng lặp while True publish telemetry + sleep
      - Hai thread này chạy song song — không bị block nhau

    Tại sao dùng loop_start() thay vì loop_forever()?
      loop_forever() block main thread → không publish được.
      Sensor cần ĐỒNG THỜI: publish định kỳ VÀ nhận message reset.
      loop_start() giải quyết bằng cách tách I/O ra thread riêng.

    userdata pattern:
      Truyền state vào client.userdata để on_message có thể truy cập
      mà không cần biến global — clean hơn và dễ test hơn.
    """
    state = BinState()

    # Tạo MQTT client với API version 2 (paho >= 2.0.0)
    # VERSION2 bắt buộc để on_connect nhận đủ 5 tham số
    client = mqtt.Client(
        client_id=DEVICE_ID,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        userdata={"state": state},  # truyền state để on_message truy cập
    )
    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect

    # Retry loop: thử kết nối broker cho đến khi thành công.
    # Cần thiết vì Docker Compose khởi động các container gần như đồng thời —
    # sensor có thể start trước khi mosquitto sẵn sàng nhận kết nối.
    print(f"[{DEVICE_ID}] Khởi động — kết nối {MQTT_BROKER}:{MQTT_PORT}")
    while True:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            # keepalive=60: broker và client trao đổi PINGREQ/PINGRESP mỗi 60s
            # để phát hiện kết nối chết (zombie connection)
            break
        except Exception as e:
            print(f"[{DEVICE_ID}] Chưa kết nối được: {e} — thử lại sau 5s")
            time.sleep(5)

    # Khởi động MQTT network thread (xử lý I/O nền)
    client.loop_start()

    print(f"[{DEVICE_ID}] Bắt đầu publish lên {TOPIC_TELEMETRY} mỗi {PUBLISH_INTERVAL}s")
    try:
        while True:
            # Cập nhật trạng thái và lấy payload
            payload = state.tick()

            # Publish với QoS 1: broker xác nhận đã nhận (at-least-once delivery)
            # QoS 0 = fire-and-forget (có thể mất), QoS 2 = exactly-once (chậm hơn)
            client.publish(TOPIC_TELEMETRY, json.dumps(payload), qos=1)

            # Log một dòng để theo dõi qua `docker compose logs -f sensor-bin-01`
            print(
                f"[{DEVICE_ID}] fill={payload['fill_level']:5.1f}% "
                f"weight={payload['weight_kg']:5.1f}kg "
                f"methane={payload['methane_ppm']:6.1f}ppm "
                f"temp={payload['temperature']:5.1f}°C "
                f"tilt={payload['tilt']}"
            )
            time.sleep(PUBLISH_INTERVAL)

    except KeyboardInterrupt:
        # Ctrl+C khi chạy local (không xảy ra trong Docker thường)
        print(f"[{DEVICE_ID}] Dừng.")
    finally:
        # Dọn dẹp: dừng network thread trước khi disconnect
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
