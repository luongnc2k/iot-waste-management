# Virtual Smart Waste Management Gateway

**Đề tài 2.5** — IT6130 Lập trình và Ảo hóa cho IoT, HUST

Hệ thống IoT gateway ảo giám sát ba thùng rác thông minh theo thời gian thực: theo dõi mức đầy, nhiệt độ, khí methane, phát hiện bất thường bằng rule engine tại biên, điều khiển actuator tự động, lưu trữ chuỗi thời gian vào InfluxDB, trực quan hóa qua Grafana và đồng bộ hai chiều với ThingsBoard Cloud.

## Nhóm thực hiện

| Sinh viên | MSSV | Phụ trách |
|---|---|---|
| Ngô Văn Quang | 20251243M | Virtual sensor, virtual actuator, thiết kế MQTT topic |
| Nguyễn Cao Lương | 20251244M | Edge gateway, rule engine, InfluxDB, unit test |
| TRƯƠNG Tuấn Nghĩa | 20251196M | REST API, ThingsBoard, Grafana, Docker Compose |

## Kiến trúc hệ thống

```
Sensor bin-01/02/03          MQTT Broker           Edge Gateway
  (fill, weight,      ──pub──► Mosquitto  ──sub──►  validate → normalize
   methane, temp)             :1883                  rule engine → event
         ▲                       ▲                   write InfluxDB
         │                       │                        │
       reset                  command                     │ command
         │                       │                        │
Actuator bin-01/02/03 ──status──►│◄──────────────────────┘
  lock / compactor /
  buzzer / dispatch                    InfluxDB ◄─── REST API (FastAPI)
                                         :8086           :8001
                                           │
                                        Grafana       ThingsBoard Cloud
                                         :3000     (tb-gateway bridge)
```

## Yêu cầu

- Docker Desktop ≥ 4.x với Docker Compose v2
- Python ≥ 3.10 (chỉ cần cho unit test, không cần cho chạy Docker)
- (Tùy chọn) Tài khoản ThingsBoard Cloud để demo tích hợp đám mây

## Khởi động

### 1. Khởi động nhanh — toàn bộ stack

```bash
git clone https://github.com/luongnc2k/iot-waste-management.git
cd iot-waste-management

cp .env.example .env      # sao chép cấu hình mặc định
docker compose up -d --build
```

### 2. Kiểm tra stack đã sẵn sàng

```bash
docker compose ps
```

Tất cả container phải ở trạng thái `Up` hoặc `Up (healthy)`. Các container có healthcheck (`mosquitto`, `influxdb`, `grafana`, `gateway-api`) cần thêm 10–15 giây để chuyển sang `(healthy)`.

```bash
# Xác nhận REST API phản hồi
curl -s http://localhost:8001/health | python3 -m json.tool
```

### 3. Khởi động từng phần (debug)

```bash
# Chỉ khởi động broker và sensor
docker compose up -d mosquitto sensor-bin-01 sensor-bin-02 sensor-bin-03

# Sau khi sensor đã chạy, khởi động gateway
docker compose up -d waste-gateway

# Thêm InfluxDB và API
docker compose up -d influxdb gateway-api

# Thêm Grafana
docker compose up -d grafana
```

### 4. Khởi động với ThingsBoard Cloud

```bash
# Bước 1: Tạo gateway device trên ThingsBoard Cloud
# - Đăng nhập https://thingsboard.cloud (free tier)
# - Devices → + → Add new device → Name: waste-gateway → bật "Is gateway" → Add
# - Mở device vừa tạo → tab Credentials → Copy access token

# Bước 2: Điền token vào .env
# Mở .env và sửa dòng:  TB_GATEWAY_TOKEN=<token_vừa_copy>

# Bước 3: Khởi động tb-gateway (profile riêng)
docker compose --profile thingsboard up -d --build tb-gateway
docker compose logs -f tb-gateway
# Phải thấy: "Connected to ThingsBoard Cloud" và "Connected device: bin-01"
```

## Truy cập giao diện

| Service | URL | Tài khoản mặc định |
|---|---|---|
| REST API + Swagger UI | http://localhost:8001/docs | — |
| Grafana Dashboard | http://localhost:3000 | admin / admin |
| InfluxDB UI | http://localhost:8086 | admin / admin12345 |
| ThingsBoard Cloud | https://thingsboard.cloud | tài khoản cá nhân |

## Theo dõi log

```bash
# Gateway: rule engine, event, command gửi đi
docker compose logs -f waste-gateway

# Sensor: giá trị fill tăng dần
docker compose logs -f sensor-bin-01

# REST API: request/response
docker compose logs -f gateway-api

# ThingsBoard bridge
docker compose logs -f tb-gateway

# Xem log nhiều service cùng lúc
docker compose logs -f waste-gateway sensor-bin-01 actuator-bin-01
```

## Kiểm tra MQTT thủ công

```bash
# Subscribe toàn bộ telemetry (3 thùng)
docker exec mosquitto mosquitto_sub -t "waste/+/sensor/telemetry" -v

# Subscribe event từ gateway (fire_risk, bin_full, gas_alert, ...)
docker exec mosquitto mosquitto_sub -t "waste/+/gateway/event" -v

# Subscribe phản hồi actuator
docker exec mosquitto mosquitto_sub -t "waste/+/actuator/status" -v

# Gửi lệnh thủ công xuống actuator
docker exec mosquitto mosquitto_pub \
  -t waste/bin-01/actuator/command \
  -m '{"bin_id":"bin-01","target":"dispatch","action":"on","reason":"manual"}'

# Ép fire_risk bằng cách hạ ngưỡng nhiệt độ (không cần restart)
curl -X POST http://localhost:8001/config \
  -H "Content-Type: application/json" \
  -d '{"temp_fire": 25}'
# Sau vài giây: docker compose logs waste-gateway | grep fire_risk
# Khôi phục:
curl -X POST http://localhost:8001/config \
  -H "Content-Type: application/json" \
  -d '{"temp_fire": 60}'
```

## REST API — danh sách endpoint

```bash
# Trạng thái tổng quát hệ thống
curl -s http://localhost:8001/health | python3 -m json.tool
curl -s http://localhost:8001/summary | python3 -m json.tool

# Telemetry mới nhất tất cả bins
curl -s http://localhost:8001/bins | python3 -m json.tool

# Trạng thái chi tiết một bin (telemetry + actuator)
curl -s http://localhost:8001/bins/bin-01/state | python3 -m json.tool

# 20 event gần nhất trong 1 giờ qua
curl -s http://localhost:8001/bins/bin-01/events | python3 -m json.tool

# Danh sách thùng cần thu gom, sắp xếp theo fill giảm dần
curl -s http://localhost:8001/collection/route | python3 -m json.tool

# Sensor offline detection
curl -s http://localhost:8001/bins/offline | python3 -m json.tool

# Dự báo thời điểm thùng đầy (OLS linear regression 15 phút)
curl -s http://localhost:8001/bins/bin-01/eta | python3 -m json.tool

# Xem ngưỡng rule engine hiện tại
curl -s http://localhost:8001/config | python3 -m json.tool

# Cập nhật ngưỡng không cần restart
curl -X POST http://localhost:8001/config \
  -H "Content-Type: application/json" \
  -d '{"fill_dispatch": 80, "temp_fire": 55}'

# Gửi lệnh điều khiển thủ công
curl -X POST http://localhost:8001/bins/bin-01/command \
  -H "Content-Type: application/json" \
  -d '{"target":"buzzer","action":"off","reason":"manual_reset"}'
```

Xem toàn bộ endpoint với ví dụ request/response tại http://localhost:8001/docs.

## Kiểm tra InfluxDB

```bash
# Mở Data Explorer tại http://localhost:8086
# Login: admin / admin12345, org: hust, bucket: iot

# Hoặc query qua CLI:
docker exec influxdb influx query \
  --org hust --token dev-token-change-me \
  'from(bucket:"iot") |> range(start:-10m) |> filter(fn:(r) => r._measurement == "bin_telemetry") |> limit(n:5)'

# Kiểm tra event gần nhất
docker exec influxdb influx query \
  --org hust --token dev-token-change-me \
  'from(bucket:"iot") |> range(start:-30m) |> filter(fn:(r) => r._measurement == "gateway_events") |> last()'
```

## Chạy unit test

Unit test không cần Docker, broker hay InfluxDB thật. Chạy trực tiếp trên máy:

```bash
# Cài dependencies (một lần)
pip install fastapi "httpx[http2]" paho-mqtt influxdb-client pydantic

# Chạy tất cả test (92 test)
python -m unittest discover -s iot_gateway -p "test_*.py" -v
python -m unittest discover -s gateway_api -p "test_*.py" -v

# Chạy từng nhóm
python -m unittest iot_gateway.test_rule_engine -v       # rule engine 5 luật
python -m unittest iot_gateway.test_tb_gateway -v        # ThingsBoard RPC/payload
python -m unittest iot_gateway.test_gateway_config -v    # config runtime
python -m unittest gateway_api.test_api -v               # REST API endpoints
python -m unittest gateway_api.test_eta_predictor -v     # OLS ETA algorithm
```

| File | Số test | Phạm vi |
|---|---|---|
| `iot_gateway/test_rule_engine.py` | ~8 | 5 luật rule engine + debounce + edge-detect |
| `iot_gateway/test_tb_gateway.py` | 27 | RPC → command, telemetry payload, shared attributes |
| `iot_gateway/test_gateway_config.py` | 5 | Cập nhật ngưỡng runtime, validation |
| `gateway_api/test_api.py` | 32 | Tất cả endpoint: validate, 404, 400, mock InfluxDB |
| `gateway_api/test_eta_predictor.py` | 20 | OLS slope, biên chuỗi rỗng/1 điểm/âm/đầy |
| **Tổng** | **92** | **Tất cả pass, không cần môi trường Docker** |

## Demo nâng cao §5.17 (8/8)

```bash
# Chạy script demo tất cả 8 yêu cầu nâng cao
bash scripts/demo-nang-cao.sh

# Hoặc demo từng điểm:

# #1 Lộ trình thu gom
curl -s http://localhost:8001/collection/route | python3 -m json.tool

# #4 Healthcheck 12 service
docker compose ps --format "table {{.Name}}\t{{.Status}}"

# #6 Dự báo ETA
curl -s http://localhost:8001/bins/bin-01/eta | python3 -m json.tool
curl -s http://localhost:8001/bins/bin-02/eta | python3 -m json.tool
curl -s http://localhost:8001/bins/bin-03/eta | python3 -m json.tool

# #7 Unit test
python -m unittest discover -s iot_gateway -p "test_*.py" 2>&1 | tail -2
python -m unittest discover -s gateway_api -p "test_*.py" 2>&1 | tail -2

# #8 Test reconnect MQTT
docker compose stop mosquitto
sleep 5
docker compose start mosquitto
docker compose logs waste-gateway | grep -i "connect"
```

## Dọn dẹp

```bash
# Dừng tất cả container
docker compose down

# Dừng và xóa volumes (mất toàn bộ dữ liệu InfluxDB + Grafana)
docker compose down -v

# Xóa image đã build
docker compose down --rmi local
```

## Cấu trúc thư mục

```
iot-waste-management/
├── docker-compose.yml          # Định nghĩa 12 service
├── .env.example                # Mẫu cấu hình (25 biến môi trường)
├── mosquitto/
│   └── config/mosquitto.conf
├── virtual_sensor/             # SV1: cảm biến ảo
│   ├── sensor.py
│   ├── Dockerfile
│   └── requirements.txt
├── virtual_actuator/           # SV1: thiết bị chấp hành ảo
│   ├── actuator.py
│   ├── Dockerfile
│   └── requirements.txt
├── iot_gateway/                # SV2 + SV3
│   ├── gateway.py              # Edge gateway chính
│   ├── rule_engine.py          # 5 luật phát hiện bất thường
│   ├── state_store.py          # Quản lý trạng thái, debounce
│   ├── influx_writer.py        # Ghi InfluxDB
│   ├── tb_gateway.py           # ThingsBoard bridge (SV3)
│   ├── test_rule_engine.py
│   ├── test_tb_gateway.py
│   └── test_gateway_config.py
├── gateway_api/                # SV3: REST API
│   ├── api.py                  # FastAPI — 11 endpoint
│   ├── eta_predictor.py        # OLS linear regression module
│   ├── test_api.py
│   ├── test_eta_predictor.py
│   ├── Dockerfile
│   └── requirements.txt
├── grafana/                    # SV3: 11 panel, provisioning tự động
│   ├── provisioning/
│   └── dashboards/
├── thingsboard/                # SV3: Alarm Rules JSON export
├── scripts/
│   └── demo-nang-cao.sh        # Demo 8 yêu cầu nâng cao
└── docs/
    ├── kich-ban-demo.md
    └── script-thuyet-trinh-sv3.md
```

## Biến môi trường quan trọng

| Biến | Mặc định | Mô tả |
|---|---|---|
| `MQTT_BROKER` | `mosquitto` | Hostname broker trong Docker network |
| `TB_GATEWAY_TOKEN` | *(bắt buộc điền)* | Token gateway ThingsBoard Cloud |
| `FILL_DISPATCH_THRESHOLD` | `85` | Ngưỡng fill (%) kích hoạt điều xe |
| `TEMP_FIRE_THRESHOLD` | `60` | Nhiệt độ (°C) coi là fire_risk |
| `METHANE_ALERT_THRESHOLD` | `500` | Methane (ppm) phát cảnh báo |
| `SENSOR_OFFLINE_TIMEOUT` | `30` | Giây không nhận telemetry → offline |
| `WEBHOOK_URL` | *(trống)* | URL nhận HTTP POST khi có event critical |
| `WEBHOOK_SEVERITY` | `critical` | `critical` / `warning` / `all` |

Xem đầy đủ 25 biến tại [`.env.example`](.env.example).
