# Thiết kế Topic & Message — Virtual Smart Waste Management Gateway
# Phần SV1: Virtual Sensor · Virtual Actuator · Topic/Message

---

## Phần 1 — Tổng quan luồng hoạt động của SV1

SV1 chịu trách nhiệm hai thành phần đầu cuối của hệ thống:

```text
[Virtual Sensor] ──publish──▶ MQTT Broker ──subscribe──▶ [Gateway (SV2)]
[Virtual Sensor] ◀──subscribe── MQTT Broker ◀──publish── [Gateway (SV2)]

[Gateway (SV2)] ──publish──▶ MQTT Broker ──subscribe──▶ [Virtual Actuator]
[Virtual Actuator] ──publish──▶ MQTT Broker (phản hồi trạng thái)
```

Nói đơn giản:

- **Virtual Sensor** = thiết bị giả lập thùng rác, liên tục gửi số liệu lên MQTT
- **Virtual Actuator** = thiết bị giả lập motor/còi/khóa, nhận lệnh từ gateway và phản hồi lại
- **MQTT Broker (Mosquitto)** = trung tâm phân phát tin nhắn, không xử lý logic

---

## Phần 2 — Thiết kế Topic Hierarchy

Mỗi thùng rác có một "kênh" riêng trên MQTT, gồm 5 topic con:

```text
waste/
└── {bin_id}/                        ← thay bin-01, bin-02, bin-03
    ├── sensor/
    │   ├── telemetry                ← Sensor GỬI dữ liệu thô lên đây
    │   └── reset                    ← Gateway BÁO thùng vừa được thu gom
    ├── actuator/
    │   ├── command                  ← Gateway GỬI lệnh điều khiển xuống đây
    │   └── status                   ← Actuator PHẢN HỒI trạng thái lên đây
    └── gateway/
        ├── normalized               ← Gateway publish dữ liệu đã chuẩn hóa (SV2)
        └── event                    ← Gateway publish sự kiện phát hiện (SV2)
```

**Tại sao lại thiết kế như vậy?**

- Tách riêng `sensor/` và `actuator/` để dễ debug: chỉ cần subscribe một topic là xem
  được toàn bộ dữ liệu của một loại thiết bị.
- Dùng `bin_id` trong topic thay vì trong payload để gateway có thể dùng wildcard
  (`waste/+/sensor/telemetry`) subscribe tất cả thùng chỉ bằng một lệnh.
- Topic `gateway/` dành cho SV2 ghi kết quả xử lý — SV1 không cần đụng vào.

---

## Phần 3 — Virtual Sensor hoạt động như thế nào?

### 3.1 Vòng đời của một Sensor

```text
Khởi động
    │
    ▼
Đọc cấu hình từ biến môi trường
(AREA_ID, BIN_ID, MQTT_BROKER, PUBLISH_INTERVAL ...)
    │
    ▼
Kết nối MQTT Broker (thử lại nếu thất bại)
    │
    ▼
Subscribe topic "waste/bin-01/sensor/reset"   ← lắng nghe lệnh reset từ gateway
    │
    ▼
┌──────────────────────────────────────────┐
│  Lặp vô hạn mỗi PUBLISH_INTERVAL giây   │
│                                          │
│  1. Cập nhật trạng thái thùng (tick)     │
│  2. Đóng gói JSON                        │
│  3. Publish lên sensor/telemetry         │
│  4. In log ra màn hình                   │
└──────────────────────────────────────────┘
    │
    ▼ (song song, bất kỳ lúc nào)
Nhận reset → đặt fill_level về ~0
```

### 3.2 Tại sao fill_level phải có xu hướng?

Đề bài yêu cầu: *"lưu giá trị trước và thay đổi có xu hướng, không random độc lập"*.

**Sai (random độc lập):**

```text
Tick 1: fill=45.2%   ← random
Tick 2: fill=12.8%   ← random → giảm đột ngột, không thực tế
Tick 3: fill=78.1%   ← random → tăng vọt, không thực tế
```

**Đúng (có xu hướng — cách đã implement):**

```text
fill_level ở tick này = fill_level tick trước + fill_rate + nhiễu nhỏ

Tick 1: fill=12.0%   ← khởi tạo ngẫu nhiên
Tick 2: fill=12.6%   ← tăng thêm ~0.5% + nhiễu ±0.1
Tick 3: fill=13.2%   ← tiếp tục tăng dần
Tick 4: fill=13.9%
...
Tick N: fill=88.0%   → gateway phát hiện → dispatch=on → xe thu gom
Sau thu gom: fill= 1.5%  ← reset
```

### 3.3 Các tình huống đặc biệt sensor có thể sinh

| Tình huống | Xác suất | Biểu hiện trong dữ liệu |
|---|---|---|
| Spike khí methane | ~3%/tick | `methane_ppm` tăng đột biến +300–500 ppm |
| Nguy cơ cháy | ~2%/tick khi methane cao | `temperature` tăng thêm 20–35°C |
| Mở nắp | ~15%/tick | `lid_status = "open"` |
| Đổ nghiêng | ~2% khi weight > 55kg | `tilt = true` |
| Reset sau thu gom | Khi nhận lệnh reset | `fill_level` về gần 0 |

### 3.4 Message format sensor gửi lên

**Topic:** `waste/bin-01/sensor/telemetry`

```json
{
  "device_id":   "sensor-bin-01",
  "area_id":     "district-1",
  "bin_id":      "bin-01",
  "fill_level":  88.0,
  "weight_kg":   42.5,
  "methane_ppm": 350.0,
  "temperature": 33.0,
  "lid_status":  "closed",
  "tilt":        false,
  "timestamp":   "2026-06-10T10:00:00Z"
}
```

| Field | Kiểu | Đơn vị | Ý nghĩa |
|---|---|---|---|
| `fill_level` | float | % | Mức đầy (0–100), tăng dần theo thời gian |
| `weight_kg` | float | kg | Khối lượng rác (~0.5 kg mỗi % mức đầy) |
| `methane_ppm` | float | ppm | Nồng độ khí methane (nguy hiểm nếu > 500) |
| `temperature` | float | °C | Nhiệt độ trong thùng (nguy hiểm nếu > 60) |
| `lid_status` | string | — | `"open"` hoặc `"closed"` |
| `tilt` | bool | — | `true` khi thùng bị đổ nghiêng |

---

## Phần 4 — Virtual Actuator hoạt động như thế nào?

### 4.1 Vòng đời của một Actuator

```text
Khởi động
    │
    ▼
Đọc cấu hình từ biến môi trường
    │
    ▼
Kết nối MQTT Broker
    │
    ▼
Subscribe topic "waste/bin-01/actuator/command"
    │
    ▼
Publish trạng thái ban đầu lên actuator/status  ← báo hiệu "tôi đã online"
    │
    ▼
┌──────────────────────────────────────────────┐
│  Chờ lệnh command (loop_forever)             │
│                                              │
│  Khi có lệnh đến:                            │
│  1. Parse JSON                               │
│  2. Validate (target hợp lệ? action hợp lệ?)│
│  3. Safety check (cháy → không nén rác)      │
│  4. Cập nhật trạng thái nội bộ              │
│  5. Publish status phản hồi                  │
│  6. In log                                   │
└──────────────────────────────────────────────┘
```

### 4.2 Bốn thiết bị actuator điều khiển được

```text
lock      = khóa nắp thùng        → dùng khi thùng quá nặng (overweight)
compactor = bộ nén rác            → giúp thùng chứa được nhiều hơn
buzzer    = còi báo động          → dùng khi có nguy cơ cháy/nổ
dispatch  = tín hiệu điều xe      → dùng khi thùng đầy, cần thu gom
```

### 4.3 Luồng xử lý một lệnh command

```text
Gateway gửi command:
{
  "bin_id":  "bin-01",
  "target":  "dispatch",       ← thiết bị nào?
  "action":  "on",             ← bật hay tắt?
  "reason":  "bin_full"        ← lý do gì?
}
        │
        ▼
[Bước 1] bin_id có phải thùng này không?
        │ Không → bỏ qua (lệnh cho thùng khác)
        │ Có ↓
[Bước 2] target có thuộc {lock, compactor, buzzer, dispatch}?
        │ Không → báo lỗi invalid_target + publish error status
        │ Có ↓
[Bước 3] action có thuộc {on, off}?
        │ Không → báo lỗi invalid_action + publish error status
        │ Có ↓
[Bước 4] Safety check đặc biệt:
         Nếu target=compactor, action=on, MÀ buzzer đang on
         → TỪ CHỐI (đang có nguy cơ cháy, không được nén rác)
        │ Bị block → publish error status
        │ Qua ↓
[Bước 5] Cập nhật trạng thái: state["dispatch"] = "on"
        │
        ▼
[Bước 6] Publish status phản hồi lên actuator/status:
{
  "device_id":           "actuator-bin-01",
  "bin_id":              "bin-01",
  "lock":                "off",
  "compactor":           "off",
  "buzzer":              "off",
  "dispatch":            "on",       ← đã bật
  "last_command_reason": "bin_full",
  "timestamp":           "2026-06-10T10:00:06Z"
}
```

### 4.4 Tại sao cần Safety Check?

Khi thùng rác bốc cháy (`temperature > 60`, `buzzer=on`), nếu vẫn cho phép bật
`compactor` thì bộ nén sẽ tạo thêm nhiệt và ma sát → **nguy cơ nổ cao hơn**.
Do đó actuator tự từ chối lệnh này ngay tại thiết bị, không cần chờ gateway xử lý.

---

## Phần 5 — Toàn bộ luồng dữ liệu SV1 trong hệ thống

Đây là luồng đầy đủ từ khi thùng bắt đầu đầy đến khi xe thu gom xong:

```text
BƯỚC 1: Sensor liên tục gửi dữ liệu
─────────────────────────────────────
sensor-bin-01  ──publish──▶  waste/bin-01/sensor/telemetry
  {"fill_level": 45.2, "temperature": 31.0, ...}  (mỗi 5 giây)


BƯỚC 2: Gateway (SV2) xử lý và phát hiện thùng gần đầy
─────────────────────────────────────────────────────────
MQTT Broker  ──forward──▶  waste-gateway (SV2)
Gateway normalize → tính toán → chạy rule engine
  Rule: fill_level > 85 → cần thu gom!


BƯỚC 3: Gateway gửi lệnh dispatch xuống actuator
─────────────────────────────────────────────────
waste-gateway  ──publish──▶  waste/bin-01/actuator/command
  {"bin_id":"bin-01","target":"dispatch","action":"on","reason":"bin_full"}


BƯỚC 4: Actuator nhận lệnh, validate, bật tín hiệu
────────────────────────────────────────────────────
actuator-bin-01 nhận lệnh
  → validate OK → safety check OK
  → state["dispatch"] = "on"
  → publish phản hồi


BƯỚC 5: Actuator báo cáo trạng thái
──────────────────────────────────────
actuator-bin-01  ──publish──▶  waste/bin-01/actuator/status
  {"dispatch":"on","lock":"off","compactor":"off","buzzer":"off",...}


BƯỚC 6: Sau khi xe thu gom xong, gateway báo reset sensor
────────────────────────────────────────────────────────────
waste-gateway  ──publish──▶  waste/bin-01/sensor/reset
  {"action":"reset"}

sensor-bin-01 nhận reset → fill_level về ~1.5%  → bắt đầu chu kỳ mới
```

---

## Phần 6 — Ví dụ tình huống nguy cơ cháy

```text
BƯỚC 1: Sensor phát hiện bất thường
─────────────────────────────────────
sensor-bin-01 publish:
  {"fill_level": 60.0, "temperature": 72.5, "methane_ppm": 520.0, ...}
  (nhiệt độ leo thang vì methane cao)


BƯỚC 2: Gateway phát hiện fire_risk
──────────────────────────────────────
Gateway chạy rule: temperature > 60 → fire_risk critical!

Gateway gửi 2 lệnh liên tiếp:
  Lệnh 1: {"target":"buzzer", "action":"on", "reason":"fire_risk"}
  Lệnh 2: {"target":"compactor", "action":"off", "reason":"fire_risk"}


BƯỚC 3: Actuator xử lý
────────────────────────
Lệnh 1 (buzzer=on) → OK → state["buzzer"] = "on"
Lệnh 2 (compactor=off) → OK → state["compactor"] = "off"


BƯỚC 4: Nếu sau đó ai đó gửi lệnh bật compactor nhầm
───────────────────────────────────────────────────────
Gateway hoặc REST API gửi: {"target":"compactor","action":"on","reason":"manual"}

Actuator kiểm tra: buzzer đang on? → CÓ
→ SAFETY BLOCK: từ chối lệnh, publish error status
  {"error":"safety_block:compactor_during_fire_risk", ...}
```

---

## Phần 7 — Cách chạy và kiểm tra SV1

### Chạy toàn bộ stack

```bash
cd project-05
docker compose up -d --build
```

### Xem log sensor

```bash
docker compose logs -f sensor-bin-01
```

Kết quả mong đợi:

```text
[sensor-bin-01] fill= 12.0%  weight=  6.0kg  methane=134.9ppm  temp=29.6°C  tilt=False
[sensor-bin-01] fill= 12.6%  weight=  6.3kg  methane=152.1ppm  temp=31.2°C  tilt=False
[sensor-bin-01] fill= 13.3%  weight=  6.7kg  methane=141.8ppm  temp=30.5°C  tilt=False
```

### Gửi lệnh thủ công vào actuator (dùng mosquitto_pub)

```bash
# Bật dispatch (điều xe thu gom)
docker exec mosquitto mosquitto_pub \
  -t waste/bin-01/actuator/command \
  -m '{"bin_id":"bin-01","target":"dispatch","action":"on","reason":"manual"}'

# Xem phản hồi
docker exec mosquitto mosquitto_sub -t "waste/+/actuator/status" -v
```

### Subscribe toàn bộ telemetry của mọi thùng

```bash
docker exec mosquitto mosquitto_sub -t "waste/+/sensor/telemetry" -v
```
