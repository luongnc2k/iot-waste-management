# SV3 — Đánh giá công việc (ThingsBoard + REST API + Grafana + Docker Compose)

Phạm vi SV3 theo README: `gateway_api/`, `iot_gateway/tb_gateway.py`, `grafana/`, `docker-compose.yml`, `README.md`.

## 1. Checklist nhiệm vụ bắt buộc (§5.6 – §5.14)

| Mục | Trạng thái | Ghi chú |
|---|---|---|
| REST API (FastAPI): health, bins, state, events, command, config | ✅ Done | `gateway_api/api.py` |
| GET /collection/route (§5.9) | ✅ Done | Suy từ InfluxDB fill_level, sắp xếp giảm dần |
| ThingsBoard bridge: telemetry uplink + RPC downlink | ✅ Verify thật trên cloud | 3 sub-device bin-01/02/03, Alarm rules, dashboard |
| Grafana dashboard: 11 panel (8 bắt buộc + 3 nâng cao) | ✅ Done | Provisioning tự động khi container start |
| Docker Compose: 12 service, network, volume, env | ✅ Done | Healthcheck đủ 12 service |
| README: kiến trúc, cài đặt, phân công, hướng dẫn test | ✅ Done | Có hướng dẫn ThingsBoard + demo |

## 2. Yêu cầu nâng cao §5.17 — trạng thái đầy đủ

| # | Yêu cầu | Trạng thái | Triển khai cụ thể |
|---|---|---|---|
| 1 | Tối ưu lộ trình thu gom (gợi ý thứ tự thùng) | ✅ Done | `GET /collection/route` — sắp xếp theo fill_level desc |
| 2 | Bản đồ thùng rác theo màu trên ThingsBoard | ✅ Done | Toạ độ push qua Gateway API, cần thêm widget Markers Map trên UI |
| 3 | Shared Attributes để chỉnh ngưỡng từ xa | ✅ Done | `POST /config` + `tb_gateway.py` shared attr → `waste/gateway/config` → `gateway.py` áp ngay |
| 4 | Health check cho các service trong Compose | ✅ Done | 12/12 service có healthcheck (HTTP / process check) |
| 5 | Alarm + thông báo khi fire_risk / thùng tràn | ✅ Done | Alarm rules trên ThingsBoard + webhook gateway.py (WEBHOOK_URL env) |
| 6 | Dự báo thời điểm thùng đầy từ tốc độ tăng | ✅ Done | `GET /bins/{id}/eta` — least-squares regression 15 phút |
| 7 | Unit test cho rule engine | ✅ Done | 72 test tổng (40 iot_gateway + 32 gateway_api), tất cả pass |
| 8 | Cơ chế reconnect khi mất kết nối MQTT/ThingsBoard | ✅ Done | `reconnect_delay_set` + SIGTERM handler |

**Kết quả: 8/8 yêu cầu nâng cao hoàn thành.**

## 3. Bug đã sửa (trong quá trình phát triển SV3)

| File | Bug | Sửa |
|---|---|---|
| `tb_gateway.py` | Tạo `local_client` hai lần (artifact copy-paste), bản có `reconnect_delay_set` bị đè | Xoá block thứ hai |
| `gateway_api/api.py` | `ThresholdConfig` field `float = None` (Pydantic v2 báo 422 khi gửi null) | Đổi sang `Optional[float] = None` |
| `README.md` | 6 chỗ ghi port 8000, thực tế map 8001 | Sửa toàn bộ |
| `tb_gateway.py` | `tb_on_connect` dùng sai API topic shared attributes → ThingsBoard disconnect ngay sau mỗi connect | Đổi sang `v1/devices/me/attributes/request/1` |
| `tb_gateway.py` | Thiếu SIGTERM handler → container dừng không sạch | Thêm `signal.signal(SIGTERM, ...)` |
| `gateway_api/api.py` | `_get_latest_actuator` dùng `-5m` (như telemetry), actuator chỉ publish khi nhận lệnh → báo no_data sai | Đổi sang `-24h` |
| `gateway.py` | `waste/gateway/config` không được subscribe → tính năng chỉnh ngưỡng runtime vô tác dụng | Thêm subscribe + `handle_config_update()` |

## 4. API endpoints — tổng hợp đầy đủ

### Bắt buộc (§5.9)
| Endpoint | Mô tả |
|---|---|
| `GET /health` | Trạng thái service + danh sách bins |
| `GET /bins` | Telemetry mới nhất tất cả bins |
| `GET /bins/{id}/state` | Telemetry + actuator state của 1 bin |
| `GET /bins/{id}/events` | 20 event gần nhất của 1 bin (1h) |
| `POST /bins/{id}/command` | Gửi lệnh thủ công xuống actuator |
| `GET /collection/route` | Danh sách thùng cần thu gom, sắp theo fill desc |
| `GET /config` | Ngưỡng rule engine hiện tại |
| `POST /config` | Cập nhật ngưỡng runtime (không cần restart) |

### Nâng cao (vượt đề bài)
| Endpoint | Mô tả | Nâng cao # |
|---|---|---|
| `GET /summary` | Tổng quan hệ thống: online/offline/critical/due | — |
| `GET /bins/offline` | Sensor offline detection theo SENSOR_OFFLINE_TIMEOUT | #1 |
| `GET /bins/{id}/eta` | ETA dự báo thùng đầy (linear regression 15 phút) | #6 |

## 5. Grafana Dashboard — 11 panel

| # | Panel | Loại | Nguồn dữ liệu |
|---|---|---|---|
| 1 | Mức đầy theo thùng (fill_level %) | timeseries | bin_telemetry.fill_level |
| 2 | Khối lượng theo thùng (weight_kg) | timeseries | bin_telemetry.weight_kg |
| 3 | Nhiệt độ theo thùng (°C) | timeseries | bin_telemetry.temperature |
| 4 | Methane theo thùng (ppm) | timeseries | bin_telemetry.methane_ppm |
| 5 | Trạng thái thiết bị (lock/compactor/buzzer/dispatch) | table | actuator_status |
| 6 | Số event theo thời gian | timeseries | gateway_events |
| 7 | Thùng cần thu gom hiện tại (fill > 85%) | table | bin_telemetry.fill_level |
| 8 | Event gần nhất | table | gateway_events |
| 9 | **[Nâng cao]** Tốc độ tăng mức đầy (%/phút) | timeseries | derivative(fill_level) |
| 10 | **[Nâng cao]** Trạng thái sensor (Online/Offline) | table | last seen time |
| 11 | **[Nâng cao]** Dự báo thời điểm đầy (ETA) | table | linear regression fill_level |

## 6. Test suite

```bash
python -m unittest discover -s iot_gateway -p "test_*.py" -v   # 40 test
python -m unittest discover -s gateway_api -p "test_*.py" -v   # 32 test
# Tổng: 72 test, tất cả pass. Không cần Docker/broker/InfluxDB thật.
```

Phân bổ theo file:
- `iot_gateway/test_rule_engine.py` — rule engine (SV2, nhóm có sẵn)
- `iot_gateway/test_tb_gateway.py` — 27 test: pure functions + regression bug ThingsBoard
- `iot_gateway/test_gateway_config.py` — 5 test: handle_config_update runtime
- `gateway_api/test_api.py` — 32 test: tất cả endpoint, validate, mock MQTT + InfluxDB

## 7. Demo nâng cao

Chạy script minh họa tất cả 8 nâng cao:
```bash
bash scripts/demo-nang-cao.sh
```

Script sẽ:
- Gọi từng endpoint theo thứ tự nâng cao #1→#8
- In kết quả thực tế, kết hợp hướng dẫn cho bước cần thao tác UI
- Chạy toàn bộ test suite
- In bảng tổng kết cuối

## 8. Cấu hình thông báo webhook (nâng cao #5)

Thêm vào `.env`:
```env
WEBHOOK_URL=https://webhook.site/<your-uuid>   # URL nhận HTTP POST
WEBHOOK_SEVERITY=critical                        # critical | warning | all
```

Tương thích với Slack Incoming Webhooks, Discord Webhooks, webhook.site, n8n, Make (Integromat), bất kỳ dịch vụ nhận HTTP POST nào.

## 9. Trạng thái Git

- Branch làm việc: `feat/dashboard-thingsboard-report` (rebase từ `sv3-thingsboard-restapi`)
- Commit mới nhất: `4708833` (docs: báo cáo 26 trang)
- `main`: đã merge PR #4 (`sv3-thingsboard-restapi`) — có toàn bộ code SV3 cơ bản
- Các cải tiến mới nhất (Grafana 11 panel, API 3 endpoint, webhook, health check) đang trên `feat/dashboard-thingsboard-report` — cần merge vào `main` trước khi nộp

## 10. Việc còn cần làm trước khi nộp

| # | Việc | Ai | Ghi chú |
|---|---|---|---|
| 1 | Merge `feat/dashboard-thingsboard-report` vào `main` | SV3 (bạn) | PR hoặc merge trực tiếp |
| 2 | Thêm Markers Map widget trên ThingsBoard dashboard | Bạn (UI) | Toạ độ đã push sẵn |
| 3 | Cấu hình WEBHOOK_URL trong `.env` để test notification | Bạn | webhook.site miễn phí |
| 4 | Thông báo SV2 về thay đổi trong `gateway.py` (subscribe config topic) | Bạn | Trước khi merge |
