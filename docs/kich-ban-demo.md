# Kịch bản trình bày Demo — Đề tài 2.5: Virtual Smart Waste Management Gateway

Tài liệu này hướng dẫn trình bày demo trước giảng viên theo đúng tám điểm mà đề bài yêu cầu phải chứng minh (mục 6, Khung yêu cầu chung). Mỗi phần gồm bốn mục: mục đích, lệnh cần chạy, lời thuyết trình gợi ý, và kết quả mong đợi. Tổng thời gian demo nên gói trong khoảng mười lăm phút, chưa kể phần hỏi đáp.

## Chuẩn bị trước buổi demo

1. Mở Docker Desktop, kiểm tra daemon đang chạy bằng lệnh `docker info`.
2. Đảm bảo file `.env` đã tồn tại trong thư mục gốc (`cp .env.example .env` nếu chưa có), và `TB_GATEWAY_TOKEN` đã được điền nếu muốn demo phần ThingsBoard.
3. Mở sẵn ba tab terminal: một để chạy lệnh chính, một để xem log (`docker compose logs -f`), một để chạy `mosquitto_sub` hoặc `curl`.
4. Mở sẵn ba tab trình duyệt: Grafana (`http://localhost:3000`), Swagger UI của REST API (`http://localhost:8001/docs`), và ThingsBoard Cloud (`https://thingsboard.cloud`, đã đăng nhập từ trước).
5. Cân nhắc hạ tạm một vài ngưỡng rule engine trong `.env` (ví dụ `METHANE_ALERT_THRESHOLD` hoặc `TEMP_FIRE_THRESHOLD`) để sự kiện bất thường xuất hiện nhanh hơn trong lúc demo, tránh phải chờ lâu trước mặt giảng viên.

## Điểm 1: Chạy toàn bộ stack bằng Docker Compose

**Mục đích.** Chứng minh hệ thống khởi động hoàn toàn bằng một lệnh, không cần thao tác thủ công vào từng container.

**Lệnh:**
```bash
docker compose up -d --build
docker compose ps
```

**Lời thuyết trình.** "Toàn bộ hệ thống của nhóm em được mô tả trong một file `docker-compose.yml` duy nhất. Em chỉ cần một lệnh để dựng cả mười một container: broker MQTT, ba sensor, ba actuator, gateway xử lý rule engine, InfluxDB, Grafana và REST API."

**Kết quả mong đợi.** Cột `STATUS` của tất cả container hiển thị `Up`, mosquitto và influxdb thêm chữ `(healthy)` nhờ healthcheck.

## Điểm 2: Nhiều virtual sensor đang publish

**Mục đích.** Chứng minh từng thùng rác có một sensor độc lập, sinh dữ liệu liên tục và có xu hướng (không random rời rạc).

**Lệnh:**
```bash
docker compose logs -f sensor-bin-01 sensor-bin-02 sensor-bin-03
```

**Lời thuyết trình.** "Mỗi container sensor đại diện cho một thùng rác thật. Mức đầy tăng dần qua từng chu kỳ năm đến bảy giây, đúng theo yêu cầu mô phỏng có xu hướng của đề bài, không phải số random độc lập."

**Kết quả mong đợi.** Ba luồng log xen kẽ, giá trị `fill` của mỗi thùng tăng dần qua các dòng liên tiếp.

## Điểm 3: Gateway nhận telemetry, normalize và phát hiện event bất thường

**Mục đích.** Chứng minh gateway xử lý dữ liệu thô và rule engine hoạt động đúng.

**Lệnh:**
```bash
docker compose logs -f waste-gateway
```

Nếu muốn ép một sự kiện xuất hiện ngay để không phải chờ, gửi lệnh hạ ngưỡng qua REST API ở một terminal khác:
```bash
curl -X POST http://localhost:8001/config -H "Content-Type: application/json" \
  -d '{"temp_fire": 35}'
```

**Lời thuyết trình.** "Gateway subscribe toàn bộ telemetry bằng một wildcard, kiểm tra hợp lệ rồi chuẩn hóa dữ liệu. Em vừa hạ tạm ngưỡng nhiệt độ cháy qua REST API để minh họa nhanh, các thầy cô sẽ thấy log hiện dòng EVENT ngay sau đó."

**Kết quả mong đợi.** Log hiện dòng dạng `[waste-gateway] EVENT bin-01: fire_risk (critical) value=... thr=...`, kèm dòng `CMD bin-01: buzzer=on`.

## Điểm 4: Gateway gửi command tự động, actuator phản hồi status

**Mục đích.** Chứng minh chuỗi điều khiển tự động khép kín, có an toàn nhiều lớp.

**Lệnh:**
```bash
docker compose logs -f actuator-bin-01
```

Thử gửi thêm một lệnh nguy hiểm để minh họa safety block:
```bash
curl -X POST http://localhost:8001/bins/bin-01/command -H "Content-Type: application/json" \
  -d '{"target":"compactor","action":"on","reason":"demo"}'
```

**Lời thuyết trình.** "Actuator nhận lệnh `buzzer=on` từ gateway và phản hồi trạng thái ngay. Bây giờ em thử gửi thủ công lệnh bật bộ nén rác trong lúc đang báo cháy, hệ thống phải từ chối vì actuator có lớp an toàn riêng, không phụ thuộc hoàn toàn vào gateway."

**Kết quả mong đợi.** Log actuator hiện `CMD ✓ buzzer: off → on`, sau đó khi gửi lệnh compactor sẽ thấy `SAFETY BLOCK: từ chối bật compactor khi buzzer đang on`.

## Điểm 5: Dữ liệu được ghi vào InfluxDB

**Mục đích.** Chứng minh telemetry, event và actuator status đều được lưu trữ dạng time-series.

**Thực hiện.** Mở `http://localhost:8086`, đăng nhập bằng thông tin trong `.env` (`admin` / `admin12345` theo mặc định), vào Data Explorer, chạy truy vấn:
```text
from(bucket: "iot")
  |> range(start: -10m)
  |> filter(fn: (r) => r._measurement == "gateway_events")
```

**Lời thuyết trình.** "Mọi sự kiện gateway phát ra đều được ghi vào measurement `gateway_events`, kèm tag bin_id, loại sự kiện và mức độ nghiêm trọng. Cùng cách này, telemetry thô nằm ở `bin_telemetry` và trạng thái actuator nằm ở `actuator_status`."

**Kết quả mong đợi.** Bảng kết quả hiện đúng sự kiện `fire_risk` vừa kích hoạt ở điểm 3, với giá trị và ngưỡng khớp với log gateway.

## Điểm 6: Grafana hiển thị telemetry, event và status

**Mục đích.** Chứng minh dữ liệu được trực quan hóa đầy đủ theo yêu cầu sáu nhóm panel của đề bài.

**Thực hiện.** Mở `http://localhost:3000` (đăng nhập theo `GRAFANA_USER`/`GRAFANA_PASSWORD` trong `.env`, mặc định `admin`/`admin`), mở dashboard có sẵn (provisioning tự động nạp khi container khởi động).

**Lời thuyết trình.** "Dashboard có tám panel, đủ sáu nhóm yêu cầu: mức đầy, khối lượng, nhiệt độ và methane theo từng thùng, trạng thái bốn thiết bị chấp hành, số lượng sự kiện theo thời gian, danh sách thùng cần thu gom ngay bây giờ, và bảng sự kiện gần nhất."

**Kết quả mong đợi.** Panel "Nhiệt độ theo thùng" cho thấy đường của bin-01 vượt ngưỡng đúng thời điểm vừa demo ở điểm 3; panel "Trạng thái thiết bị" cho thấy buzzer của bin-01 đang ở mức on.

## Điểm 7: Telemetry xuất hiện trên ThingsBoard

**Mục đích.** Chứng minh tích hợp cloud hai chiều, vai trò ThingsBoard Gateway của hệ thống.

**Lệnh (nếu `tb-gateway` chưa chạy):**
```bash
docker compose --profile thingsboard up -d --build tb-gateway
docker logs -f tb-gateway
```

**Lời thuyết trình.** "Container `tb-gateway` đóng vai trò cầu nối, đăng ký từng thùng như một sub-device trên ThingsBoard Cloud, rồi đẩy dữ liệu đã chuẩn hóa lên đó. Các thầy cô có thể thấy ba thiết bị bin-01, bin-02, bin-03 tự xuất hiện dưới gateway."

**Kết quả mong đợi.** Trên ThingsBoard, vào Devices, thấy `bin-01`, `bin-02`, `bin-03` dưới gateway `waste-gateway`; mở tab Latest telemetry của `bin-01`, thấy `fill_level`, `temperature` và các trường khác cập nhật liên tục; tab Alarms hiện một Alarm mức Critical tương ứng sự kiện `fire_risk`.

## Điểm 8: Gửi RPC hoặc lệnh REST API xuống actuator thành công

**Mục đích.** Chứng minh điều khiển từ xa hai chiều, hoàn thiện vòng lặp uplink và downlink.

**Cách A, qua ThingsBoard RPC.** Trên trang chi tiết thiết bị `bin-01`, tìm chức năng gửi lệnh RPC (hoặc dùng API ThingsBoard), gửi:
```json
{"method": "setBuzzer", "params": false}
```

**Cách B, qua REST API (đơn giản hơn để demo nhanh):**
```bash
curl -X POST http://localhost:8001/bins/bin-01/command -H "Content-Type: application/json" \
  -d '{"target":"buzzer","action":"off","reason":"demo_tat_coi"}'
```

**Lời thuyết trình.** "Đây là chiều downlink, lệnh điều khiển đi từ trung tâm xuống thiết bị. Em gửi lệnh tắt còi cho bin-01, các thầy cô sẽ thấy actuator phản hồi ngay trong log và trạng thái trên Grafana cũng đổi theo."

**Kết quả mong đợi.** Log actuator hiện `CMD ✓ buzzer: on → off`; panel "Trạng thái thiết bị" trên Grafana đổi giá trị buzzer của bin-01 về off trong vòng vài giây.

## Kịch bản phụ, nếu còn thời gian

* **Vòng đời thu gom đầy đủ.** Theo dõi một thùng từ lúc `fill_level` vượt 85, sự kiện `bin_full` xuất hiện, `dispatch` chuyển on, đến khi gateway tự publish lệnh reset sau `COLLECTION_DELAY` giây và sensor quay về mức đầy gần 0.
* **Chạy unit test trực tiếp.** `python -m unittest discover -s iot_gateway -p "test_*.py" -v` để cho thấy hơn sáu mươi test đều pass, minh họa phần kiểm thử đã thực hiện.
* **GET /collection/route.** `curl http://localhost:8001/collection/route` để cho thấy endpoint vừa hoàn thiện, trả về danh sách thùng cần thu gom sắp theo mức đầy giảm dần.

## Dọn dẹp sau demo

```bash
docker compose down
```
