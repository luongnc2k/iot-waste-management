# Virtual Smart Waste Management Gateway

**Đề tài 2.5** — IT6130 Lập trình và Ảo hóa cho IoT (HUST)

Hệ thống IoT gateway ảo giám sát thùng rác thông minh: theo dõi mức đầy, nhiệt độ, khí methane, phát hiện bất thường bằng rule engine, điều khiển actuator tự động, đồng bộ hai chiều với ThingsBoard Cloud.

## Kiến trúc hệ thống

```
┌─────────────────┐         ┌───────────────┐         ┌──────────────┐
│ Virtual Sensors │─publish─▶│  Mosquitto    │◀─sub────│  Edge IoT    │
│ bin-01/02/03    │         │  MQTT Broker  │─forward─▶│  Gateway     │
└─────────────────┘         │  (:1883)      │         │  (SV2)       │
                            └───────────────┘         └──────┬───────┘
┌─────────────────┐              ▲                           │
│ Virtual Actuators│◀─command────┘         ┌─────────────────┼─────────────┐
│ lock/compactor/ │                        │                 │             │
│ buzzer/dispatch │                        ▼                 ▼             ▼
└─────────────────┘               ┌──────────────┐  ┌────────────┐  ┌──────────┐
                                  │  InfluxDB    │  │  Grafana   │  │ThingsBoard│
┌─────────────────┐               │  (:8086)     │  │  (:3000)   │  │  Cloud   │
│ FastAPI REST API│◀─query────────┤              │  └────────────┘  └──────────┘
│ (:8000)         │               └──────────────┘
└─────────────────┘
```

## Luồng dữ liệu

1. **Sensor** publish telemetry (fill_level, weight, methane, temperature) mỗi 5s
2. **MQTT Broker** forward đến Gateway
3. **Gateway** validate → normalize → rule engine → ghi InfluxDB → gửi command
4. **Actuator** nhận command → cập nhật state → publish status
5. **Grafana** query InfluxDB → hiển thị dashboard
6. **ThingsBoard** nhận telemetry từ gateway bridge → dashboard cloud + RPC

## Phân công nhóm

| SV | Nhiệm vụ | Thư mục |
|---|---|---|
| SV1 | Virtual sensor, virtual actuator, thiết kế topic/message | `virtual_sensor/`, `virtual_actuator/`, `docs/` |
| SV2 | Edge gateway, rule engine, ghi InfluxDB | `iot_gateway/gateway.py`, `rule_engine.py`, `influx_writer.py` |
| SV3 | ThingsBoard integration, REST API, Docker Compose, Grafana, README | `gateway_api/`, `iot_gateway/tb_gateway.py`, `grafana/`, `docker-compose.yml` |

## Cài đặt và chạy

### Yêu cầu
- Docker Desktop (có Docker Compose)
- (Tùy chọn) Tài khoản ThingsBoard Cloud

### Khởi động nhanh

```bash
# Clone repo
git clone https://github.com/luongnc2k/iot-waste-management.git
cd iot-waste-management

# Tạo file .env từ mẫu
cp .env.example .env

# Build và chạy toàn bộ stack
docker compose up -d --build

# Kiểm tra trạng thái
docker compose ps
```

### Truy cập

| Service | URL | Tài khoản |
|---|---|---|
| REST API (Swagger) | http://localhost:8001/docs | — |
| Grafana | http://localhost:3000 | admin / admin |
| InfluxDB | http://localhost:8086 | admin / admin12345 |

### Xem log

```bash
docker compose logs -f waste-gateway    # Gateway rule engine
docker compose logs -f sensor-bin-01    # Sensor data
docker compose logs -f gateway-api      # REST API
```

### Kiểm tra MQTT

```bash
# Subscribe tất cả telemetry
docker exec mosquitto mosquitto_sub -t "waste/+/sensor/telemetry" -v

# Subscribe events
docker exec mosquitto mosquitto_sub -t "waste/+/gateway/event" -v

# Gửi lệnh thủ công
docker exec mosquitto mosquitto_pub \
  -t waste/bin-01/actuator/command \
  -m '{"bin_id":"bin-01","target":"dispatch","action":"on","reason":"manual"}'
```

### REST API

```bash
# Health check
curl http://localhost:8001/health

# Xem trạng thái tất cả bins
curl http://localhost:8001/bins

# Xem state chi tiết
curl http://localhost:8001/bins/bin-01/state

# Xem events gần đây
curl http://localhost:8001/bins/bin-01/events

# Gửi lệnh
curl -X POST http://localhost:8001/bins/bin-01/command \
  -H "Content-Type: application/json" \
  -d '{"target":"dispatch","action":"on","reason":"manual"}'
```

### ThingsBoard (tùy chọn)

`.env.example` không chứa token thật (mỗi người tự tạo gateway device riêng trên
ThingsBoard Cloud của mình). Làm theo 5 bước sau:

1. Đăng ký/đăng nhập https://thingsboard.cloud (free tier đủ dùng cho lab).
2. **Devices** → nút **+** → **Add new device** → Name: `waste-gateway` → bật
   toggle **Is gateway** → **Add**.
3. Mở device `waste-gateway` vừa tạo → tab **Credentials** → **Copy access token**.
4. Dán token vào `.env`:
   ```bash
   echo "TB_GATEWAY_TOKEN=<token_vừa_copy>" >> .env
   ```
5. Đảm bảo stack chính đang chạy (`docker compose up -d`), rồi chạy `tb-gateway`:
   ```bash
   docker compose --profile thingsboard up -d --build tb-gateway
   docker logs -f tb-gateway   # phải thấy "Connected to ThingsBoard Cloud"
                               # và "Connected device: bin-01/02/03", KHÔNG có
                               # dòng "TB disconnected" lặp lại liên tục
   ```

**Kiểm tra kết quả:** vào lại ThingsBoard Cloud → **Devices** → 3 device con
`bin-01`, `bin-02`, `bin-03` phải tự xuất hiện dưới `waste-gateway` (vài giây sau
khi sensor publish lần đầu). Click vào một bin → tab **Latest telemetry** → thấy
`fill_level`, `weight_kg`, `methane_ppm`, `temperature` cập nhật mỗi ~10s.

Nếu log lặp `Connected` → `TB disconnected (rc=7)` liên tục mỗi 1-2s: đó là dấu
hiệu phiên bản cũ chưa vá bug "shared attributes request sai format" — đảm bảo
đang chạy code mới nhất của nhánh `sv3-thingsboard-restapi` (xem `SV3-Tasks.md`).

## Dọn dẹp

```bash
docker compose down        # Dừng containers
docker compose down -v     # Dừng + xóa volumes (mất dữ liệu)
```

## Kiểm thử

Unit test chạy bằng `unittest` (stdlib), không cần Docker/broker/InfluxDB thật:

```bash
# Rule engine + state store (SV2)
python -m unittest discover -s iot_gateway -p "test_*.py" -v

# REST API (SV3) — cần fastapi, pydantic, influxdb-client, paho-mqtt đã cài
python -m unittest discover -s gateway_api -p "test_*.py" -v
```

| File | Phạm vi |
|---|---|
| `iot_gateway/test_rule_engine.py` | Rule engine (5 luật) + state store (debounce, edge-detect, collection) |
| `iot_gateway/test_tb_gateway.py` | RPC ThingsBoard → command, telemetry/alarm payload, shared attributes → threshold |
| `iot_gateway/test_gateway_config.py` | `waste/gateway/config` → cập nhật `THRESHOLDS` runtime |
| `gateway_api/test_api.py` | REST API: validate input, 404, publish command, đọc InfluxDB (mock) |

## Cấu trúc thư mục

```
iot-waste-management/
├── docker-compose.yml
├── .env.example
├── mosquitto/config/mosquitto.conf
├── virtual_sensor/          (SV1)
├── virtual_actuator/        (SV1)
├── iot_gateway/             (SV2 + SV3)
│   ├── gateway.py
│   ├── rule_engine.py
│   ├── influx_writer.py
│   ├── state_store.py
│   └── tb_gateway.py       (SV3)
├── gateway_api/             (SV3)
│   ├── api.py
│   ├── Dockerfile
│   └── requirements.txt
├── grafana/                 (SV3)
│   ├── provisioning/
│   └── dashboards/
├── docs/
│   └── topic-design.md     (SV1)
└── README.md                (SV3)
```
