# Nội dung báo cáo Mini Project — 15 slide chính + slide dự phòng

> **Đề tài 2.5 — Virtual Smart Waste Management Gateway**
> Học phần IT6130 — Lập trình và Ảo hóa cho IoT (HUST)
>
> Quy ước trình bày: phần **Tiêu đề** hiển thị trên slide; phần **Nội dung** là các ý
> trình chiếu; phần *Thuyết minh* là gợi ý lời nói, không đưa lên slide.
> Phân bổ thời gian đề xuất (15 phút): Mở đầu 2′ — Thiết kế 3′ — **Demo 5–6′** —
> Điểm nhấn kỹ thuật 2′ — Khó khăn & Kết luận 2′.

---

## Slide 1 — Trang bìa

**Tiêu đề:** Virtual Smart Waste Management Gateway
*Hệ thống IoT Gateway ảo giám sát thùng rác thông minh*

- Học phần: IT6130 — Lập trình và Ảo hóa cho IoT
- Đề tài 2.5
- Nhóm thực hiện: ba thành viên (SV1 – SV2 – SV3)
- Giảng viên hướng dẫn / Lớp / Ngày báo cáo

*Thuyết minh:* Giới thiệu ngắn gọn tên đề tài, thành viên và vai trò trong khoảng 15 giây.

---

## Slide 2 — Bài toán và động lực

**Tiêu đề:** Đặt vấn đề

- Thu gom rác theo lịch cố định dẫn đến hai cực: thùng tràn hoặc xe vận hành rỗng, gây lãng phí nguồn lực.
- Thùng kín tích tụ khí methane, tiềm ẩn nguy cơ cháy nổ mà không được giám sát.
- Tình trạng quá tải khối lượng và đổ nghiêng làm hư hỏng thiết bị, mất vệ sinh đô thị.
- Yêu cầu đặt ra: một hệ thống **giám sát thời gian thực** kết hợp **ra quyết định tự động tại biên**.

*Thuyết minh:* Nêu bối cảnh thực tiễn để hội đồng thấy rõ giá trị của bài toán.

---

## Slide 3 — Mục tiêu và phạm vi

**Tiêu đề:** Mục tiêu của đề tài

- Ảo hóa toàn bộ chuỗi IoT đầu cuối: **cảm biến → broker → gateway → cơ cấu chấp hành**.
- Xây dựng gateway biên tự động phát hiện bất thường bằng **rule engine** và điều khiển thiết bị.
- Lưu trữ chuỗi thời gian và trực quan hóa dữ liệu bằng **InfluxDB và Grafana**.
- Tích hợp nền tảng đám mây: đồng bộ **hai chiều với ThingsBoard** và cung cấp **REST API**.
- Triển khai toàn bộ hệ thống bằng **Docker Compose**, bảo đảm khả năng tái lập.

*Thuyết minh:* Nhấn mạnh trọng tâm "ảo hóa" — toàn bộ thiết bị được mô phỏng bằng container, không cần phần cứng vật lý.

---

## Slide 4 — Kiến trúc tổng thể

**Tiêu đề:** Kiến trúc hệ thống

```
Cảm biến ảo ──publish──▶ Mosquitto MQTT ──forward──▶ Edge Gateway
(bin-01/02/03)             Broker (:1883)            (rule engine)
                                ▲                          │
Cơ cấu chấp hành ◀──command─────┘          ┌───────────────┼────────────┐
(lock/compactor/                           ▼               ▼            ▼
 buzzer/dispatch)                      InfluxDB        Grafana     ThingsBoard
                                       (:8086)         (:3000)        Cloud
REST API (FastAPI) ◀──query── InfluxDB                            (telemetry + RPC)
(:8001)
```

- Hệ thống mô phỏng ba thùng rác, mỗi thùng gồm một cảm biến và một cơ cấu chấp hành.
- Một gateway duy nhất phục vụ tất cả các thùng thông qua cơ chế subscribe wildcard.

*Thuyết minh:* Trình bày theo chiều dữ liệu: đầu vào bên trái, xử lý ở giữa, đầu ra bên phải. Đây là sơ đồ định hướng cho toàn bộ phần báo cáo.

---

## Slide 5 — Thiết kế Topic và Message (SV1)

**Tiêu đề:** Giao thức trao đổi trên MQTT

```
waste/{bin_id}/
├── sensor/    telemetry · reset
├── actuator/  command · status
└── gateway/   normalized · event
```

- Đưa `bin_id` vào cấu trúc topic cho phép gateway subscribe wildcard `waste/+/sensor/telemetry`.
- Bản tin định dạng **JSON**, mọi message đều mang `bin_id` và `timestamp`.
- Telemetry gồm: `fill_level` (%), `weight_kg`, `methane_ppm`, `temperature` (°C), `tilt`.

*Thuyết minh:* Thiết kế topic là nền tảng của hệ thống; nhờ wildcard, gateway chỉ cần một lệnh subscribe để giám sát mọi thùng.

---

## Slide 6 — Thiết bị ảo: Cảm biến và Cơ cấu chấp hành (SV1)

**Tiêu đề:** Mô phỏng thiết bị đầu cuối

**Cảm biến (Sensor):**
- Mỗi container đại diện một thùng rác, publish dữ liệu mỗi 5–7 giây.
- `fill_level` biến thiên **có xu hướng** (giá trị mới dựa trên giá trị cũ), phản ánh đúng quy luật vật lý.
- Sinh các tình huống bất thường: tăng đột biến methane, nguy cơ cháy, đổ nghiêng.

**Cơ cấu chấp hành (Actuator):**
- Bốn thiết bị: `lock`, `compactor`, `buzzer`, `dispatch`.
- Cơ chế **an toàn nhiều lớp**: từ chối nén rác (`compactor`) khi đang báo cháy (`buzzer=on`).

*Thuyết minh:* Việc mô phỏng dữ liệu "có xu hướng" và cơ chế an toàn tại thiết bị thể hiện tính thực tiễn của mô hình.

---

## Slide 7 — Edge Gateway và xử lý dữ liệu (SV2)

**Tiêu đề:** Gateway biên — trung tâm xử lý

- Một gateway phục vụ mọi thùng, thực thi pipeline: **validate → normalize → rule engine → command/event → ghi InfluxDB**.
- Khâu validate: kiểm tra đủ trường, đối chiếu `bin_id` với topic (chống giả mạo), kiểm tra định dạng timestamp.
- Lý do xử lý tại biên thay vì gửi thẳng đám mây:
  - **Độ trễ thấp** cho các quyết định an toàn.
  - **Khả năng chịu lỗi** khi mất kết nối Internet.
  - **Gộp kết nối** nhiều thùng qua một điểm lên đám mây.

*Thuyết minh:* Đây là khái niệm cốt lõi của học phần — điện toán biên (edge computing).

---

## Slide 8 — Rule Engine và triết lý thiết kế (SV2)

**Tiêu đề:** Tập luật phát hiện bất thường

| Điều kiện | Sự kiện | Hành động |
|---|---|---|
| `fill > 85` (>95: critical) | `bin_full` | điều xe thu gom |
| `temperature > 60` | `fire_risk` | bật còi, cấm nén rác |
| `methane > 500` | `gas_alert` | cảnh báo thông khí |
| `weight > 60` | `overweight` | khóa nắp |
| `tilt = true` | `bin_tilted` | lên lịch bảo trì |

- **Hàm thuần (pure function):** không phụ thuộc trạng thái → kiểm thử độc lập, không cần broker.
- **Tách biệt edge / level-based:** sự kiện chỉ phát ở cạnh lên; lệnh điều khiển có debounce, chỉ gửi khi trạng thái thay đổi.

*Thuyết minh:* Đây là điểm nhấn kỹ thuật; giải thích vì sao việc tách hai khái niệm giúp hệ thống ổn định và dễ kiểm thử.

---

## Slide 9 — Trực quan hóa và Tích hợp đám mây (SV3)

**Tiêu đề:** Grafana và ThingsBoard Cloud

**Grafana** (cấu hình tự động qua provisioning):
- Sáu bảng theo dõi: mức đầy, nhiệt độ, methane, trạng thái thiết bị, sự kiện theo mức độ.

**ThingsBoard Cloud** (đồng bộ hai chiều qua Gateway MQTT API):
- Chiều lên: đẩy telemetry, ba thiết bị con tự xuất hiện trên nền tảng.
- Chiều xuống: lệnh RPC từ đám mây chuyển thành command điều khiển thiết bị.
- **Alarm Rule** (Critical/Warning) đã được kiểm chứng hoạt động.

*Thuyết minh:* Chèn ảnh chụp dashboard và trang thiết bị ThingsBoard làm bằng chứng kết quả.

---

## Slide 10 — REST API và cấu hình thời gian thực (SV3)

**Tiêu đề:** Giao diện điều khiển qua HTTP

- API xây dựng bằng **FastAPI**, tài liệu tự sinh tại `:8001/docs`.
- Cho phép: truy vấn trạng thái thùng, đọc sự kiện, gửi lệnh điều khiển, xem và điều chỉnh ngưỡng.
- **Điều chỉnh ngưỡng rule engine khi đang vận hành** (không cần khởi động lại):

```
REST POST /config  ─┐
                    ├─▶ topic waste/gateway/config ─▶ Gateway cập nhật ngưỡng
ThingsBoard Shared ─┘                                  (hiệu lực ngay tức thì)
Attributes
```

*Thuyết minh:* Cùng một cơ chế config phục vụ cả REST API và ThingsBoard — thể hiện tính thống nhất trong thiết kế.

---

## Slide 11 — Triển khai và ảo hóa (SV3)

**Tiêu đề:** Đóng gói bằng Docker Compose

- Một lệnh `docker compose up -d --build` khởi tạo trên 11 container.
- Cấu hình theo nguyên tắc **12-factor**: toàn bộ tham số qua biến môi trường và tệp `.env`.
- Trang bị healthcheck, chính sách `restart`, mạng riêng và volume lưu trữ dữ liệu.
- Thành phần ThingsBoard tách riêng theo **profile**, kích hoạt khi cần.

*Thuyết minh:* Đúng tinh thần "ảo hóa": hạ tầng tái lập được, triển khai chỉ bằng một câu lệnh.

---

## Slide 12 — DEMO TRỰC TIẾP

**Tiêu đề:** Trình diễn hệ thống

**Kịch bản 1 — Vòng đời đầy → thu gom → reset:**
1. Quan sát log gateway: `fill_level` tăng dần.
2. Vượt ngưỡng 85 → phát sự kiện `bin_full`, gửi lệnh `dispatch=on`.
3. Sau thời gian thu gom mô phỏng → gateway publish `reset`, `fill_level` về 0.

**Kịch bản 2 — Nguy cơ cháy:**
4. Nhiệt độ vượt 60 → sự kiện `fire_risk`, bật `buzzer`, tắt `compactor`.
5. Gửi lệnh bật `compactor` thủ công → bị **safety block** từ chối.

**Quan sát song song:** Grafana cập nhật thời gian thực · ThingsBoard hiển thị telemetry và cảnh báo · REST API trả về trạng thái.

*Thuyết minh:* Đây là phần trọng tâm. Chuẩn bị sẵn hệ thống đang chạy trước khi báo cáo; vừa thao tác vừa giải thích.

---

## Slide 13 — Khó khăn và bài học

**Tiêu đề:** Thách thức trong quá trình thực hiện

- **Kết nối ThingsBoard chập chờn (rc=7):** sai định dạng API yêu cầu shared attributes khiến nền tảng ngắt kết nối mỗi 1–2 giây. → *Bài học: phải nắm chính xác đặc tả giao thức.*
- **Lỗi chỉ bộc lộ khi chạy thật:** unit test với mock không phát hiện được; chỉ khi triển khai trên Docker mới lộ ra. → *Bài học: kiểm thử tích hợp không thể thay thế bằng kiểm thử đơn vị.*
- **Tính năng "tồn tại trên giấy":** topic config được publish nhưng không thành phần nào subscribe. → *Bài học: tích hợp đầu-cuối quan trọng hơn từng mô-đun riêng lẻ.*
- **Dừng tiến trình an toàn:** `docker stop` gửi SIGTERM (không phải SIGINT) → bổ sung xử lý tín hiệu để ngắt kết nối sạch.

*Thuyết minh:* Đây là phần thể hiện chiều sâu; trình bày trung thực các lỗi đã gặp và cách khắc phục.

---

## Slide 14 — Kiểm thử

**Tiêu đề:** Bảo đảm chất lượng

- Kiểm thử bằng `unittest`, không phụ thuộc Docker, broker hay cơ sở dữ liệu thật.
- Phạm vi bao phủ:
  - Rule engine và state store: debounce, phát hiện cạnh, vòng đời thu gom.
  - ThingsBoard bridge: chuyển đổi RPC, dựng payload telemetry/alarm, shared attributes.
  - REST API: kiểm tra đầu vào, xử lý lỗi 404/422, gửi lệnh, đọc InfluxDB (mock).
- Triết lý kiểm thử: tách logic thuần khỏi I/O để kiểm thử độc lập.

*Thuyết minh:* Nhấn mạnh hệ thống có hơn 50 unit test, bảo đảm độ tin cậy.

---

## Slide 15 — Kết luận và hướng phát triển

**Tiêu đề:** Kết luận

**Kết quả đạt được:**
- Hoàn thiện chuỗi IoT ảo hóa đầu-cuối, vận hành tự động và đồng bộ đám mây hai chiều.
- Có kiểm thử tự động, tài liệu và khả năng triển khai bằng một câu lệnh.

**Hướng phát triển:**
- Bảo mật truyền thông MQTT bằng TLS và xác thực thiết bị.
- Ứng dụng học máy dự báo mức đầy và tối ưu lộ trình xe thu gom.
- Mở rộng quy mô đa khu vực, đa gateway.

*Thuyết minh:* Tổng kết ngắn gọn, khẳng định mức độ hoàn thành. Kết thúc bằng lời cảm ơn và mời đặt câu hỏi.

---
---

# SLIDE DỰ PHÒNG (Backup — chỉ trình chiếu khi được hỏi)

## B1 — Chi tiết Validate và Normalize

- **Trường bắt buộc:** `bin_id`, `fill_level`, `weight_kg`, `methane_ppm`, `temperature`, `timestamp`.
- **Validate:** đối chiếu `bin_id` payload với topic; kiểm tra timestamp theo ISO-8601.
- **Normalize:** ép kiểu số, làm tròn, clamp `fill_level` về [0,100], phân loại `fill_status` (low/medium/high), gắn `gateway_received_at`.
- Message không hợp lệ bị loại bỏ, không làm gián đoạn vòng lặp MQTT.

## B2 — Vòng đời thu gom và phát hiện offline

- Thùng vượt ngưỡng được đưa vào danh sách thu gom; sau `COLLECTION_DELAY` gateway publish `reset`.
- Quá `SENSOR_OFFLINE_TIMEOUT` không nhận telemetry → phát sự kiện `sensor_offline`.
- Kiến trúc đa luồng: thread mạng xử lý message, thread chính chạy vòng lặp bảo trì.

## B3 — Danh sách endpoint REST API đầy đủ

| Method | Endpoint | Chức năng |
|---|---|---|
| GET | `/health` | kiểm tra tình trạng |
| GET | `/bins` , `/bins/{id}/state` | trạng thái thùng |
| GET | `/bins/{id}/events` | sự kiện gần đây |
| POST | `/bins/{id}/command` | gửi lệnh điều khiển |
| GET / POST | `/config` | xem / điều chỉnh ngưỡng |

## B4 — Định dạng bản tin chi tiết

**Telemetry:**
```json
{"bin_id":"bin-01","fill_level":88.0,"weight_kg":70.4,"methane_ppm":350.0,
 "temperature":33.0,"lid_status":"closed","tilt":false,
 "timestamp":"2026-06-10T10:00:00Z"}
```
**Command:**
```json
{"bin_id":"bin-01","target":"dispatch","action":"on","reason":"bin_full"}
```

## B5 — Kịch bản nguy cơ cháy (sequence)

```
Sensor: temperature=72.5, methane=520  ──▶ Gateway phát hiện fire_risk
Gateway ──▶ command buzzer=on (reason=fire_risk)
Gateway ──▶ command compactor=off (reason=fire_risk)
[Nếu gửi nhầm compactor=on] ──▶ Actuator SAFETY BLOCK → từ chối
```

## B6 — Danh mục công nghệ và phân công

| Thành phần | Công nghệ |
|---|---|
| Ngôn ngữ | Python 3 (paho-mqtt, FastAPI, Pydantic) |
| Broker / DB / Dashboard | Mosquitto · InfluxDB 2.7 · Grafana 10.4 |
| Đám mây / Triển khai | ThingsBoard Cloud · Docker Compose |

| SV | Nhiệm vụ |
|---|---|
| SV1 | Cảm biến, cơ cấu chấp hành, thiết kế topic/message |
| SV2 | Edge gateway, rule engine, InfluxDB |
| SV3 | ThingsBoard, REST API, Grafana, Docker Compose |
