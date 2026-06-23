# Virtual Smart Waste Management Gateway

**Đề tài 5** — IT6130 Lập trình và Ảo hóa cho IoT (HUST)

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
| REST API (Swagger) | http://localhost:8000/docs | — |
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
curl http://localhost:8000/health

# Xem trạng thái tất cả bins
curl http://localhost:8000/bins

# Xem state chi tiết
curl http://localhost:8000/bins/bin-01/state

# Xem events gần đây
curl http://localhost:8000/bins/bin-01/events

# Gửi lệnh
curl -X POST http://localhost:8000/bins/bin-01/command \
  -H "Content-Type: application/json" \
  -d '{"target":"dispatch","action":"on","reason":"manual"}'
```

### ThingsBoard (tùy chọn)

```bash
# Thêm token vào .env
echo "TB_GATEWAY_TOKEN=your_token_here" >> .env

# Chạy với profile thingsboard
docker compose --profile thingsboard up -d --build tb-gateway
```

## Dọn dẹp

```bash
docker compose down        # Dừng containers
docker compose down -v     # Dừng + xóa volumes (mất dữ liệu)
```

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
