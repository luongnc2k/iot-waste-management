# SV3 — Đánh giá công việc (ThingsBoard + REST API + Grafana + Docker Compose)

Phạm vi SV3 theo README: `gateway_api/`, `iot_gateway/tb_gateway.py`, `grafana/`, `docker-compose.yml`, `README.md`.
Tài liệu này đánh giá những gì đã làm, những gì vừa được vá/test thêm, và việc còn mở.

## 1. Checklist nhiệm vụ bắt buộc

| Mục | Trạng thái | Ghi chú |
|---|---|---|
| REST API (FastAPI): health, bins, state, events, command | ✅ Done | `gateway_api/api.py` |
| ThingsBoard Gateway bridge (telemetry uplink + RPC downlink) | ✅ Done, verify thật trên cloud | token `g0gmqki3xip1aaryq93t`, 3 sub-device bin-01/02/03 lên ThingsBoard |
| Grafana provisioning (datasource + dashboard) | ✅ Done | 6 panel: fill level, temperature, methane, actuator status, events by severity, recent events |
| Docker Compose: gateway-api, grafana, tb-gateway (profile) | ✅ Done | đã chạy 11 container, verify end-to-end |
| README (kiến trúc, cài đặt, phân công) | ✅ Done, đã sửa thêm lần này | xem mục 3 |
| Branch `sv3-thingsboard-restapi` đã push lên GitHub | ✅ Đã xác nhận | `origin/sv3-thingsboard-restapi` == local, commit `36cc654`. **`origin/main` chưa có commit này** — chưa merge/PR. |

## 2. Nâng cao đã làm trước đó nhưng CHƯA hoàn thiện (phát hiện khi đọc sâu code)

Phiên làm việc trước đã thêm 2 endpoint nâng cao (`GET/POST /config`) và xử lý Shared
Attributes từ ThingsBoard (`tb_gateway.py::_handle_shared_attributes`) — đúng theo gợi ý
"Endpoint API đổi ngưỡng rule engine khi đang chạy" đã liệt ở lần trước. Nhưng khi đọc kỹ:

- Cả hai đường (`POST /config` và shared attributes) đều **chỉ publish** lên topic
  retained `waste/gateway/config`.
- `iot_gateway/gateway.py` (SV2) **không hề subscribe** topic này → message rơi vào
  hư không. Tính năng "chỉnh ngưỡng runtime không cần restart" tồn tại trên giấy nhưng
  KHÔNG có tác dụng thật trước khi sửa.

**Đã vá trong phiên này** (`iot_gateway/gateway.py`):
- Thêm `TOPIC_CONFIG = "waste/gateway/config"`, subscribe trong `on_connect`.
- Thêm `handle_config_update(payload)` — áp `payload["thresholds"]` vào `THRESHOLDS`
  (dataclass mutable dùng chung với `rule_engine.evaluate()`), bỏ qua key lạ/giá trị
  không hợp lệ, không crash gateway.
- Test `test_update_takes_effect_immediately_in_rule_engine` chứng minh: hạ
  `TEMP_FIRE_THRESHOLD` runtime → cùng một bản telemetry chuyển từ "an toàn" sang
  "fire_risk" ngay lập tức, không cần restart container.

⚠️ File này thuộc phần SV2 — đã sửa để hoàn thiện tính năng SV3 thiết kế, **nên báo cho
bạn cùng nhóm làm SV2 biết** trước khi merge, tránh xung đột với thay đổi khác của họ.

## 3. Bug đã sửa

| File | Bug | Sửa |
|---|---|---|
| `iot_gateway/tb_gateway.py` | `main()` tạo `local_client = mqtt.Client(...)` **hai lần liên tiếp** — bản đầu (có `reconnect_delay_set(min_delay=1, max_delay=10)`) bị đè mất, dùng bản hai (reconnect delay mặc định của paho). Rõ ràng là artifact copy-paste. | Xoá block tạo lại, giữ bản có cấu hình reconnect. |
| `gateway_api/api.py` | `ThresholdConfig` khai báo `fill_dispatch: float = None` (không phải `Optional[float]`). Với Pydantic v2, client gửi tường minh `{"fill_critical": null}` → **422 validation error** thay vì được coi là "không cung cấp" (khác hành vi Pydantic v1 mà code có vẻ được viết theo). | Đổi cả 5 field sang `Optional[float] = None`. |
| `README.md` | 6 chỗ ghi `http://localhost:8000/...` cho REST API, nhưng `docker-compose.yml` map `"8001:8000"` (do bạn đổi port lúc port 8000 bị Docker khác chiếm trên máy). Ai theo README sẽ gọi nhầm port. | Đổi toàn bộ thành `localhost:8001`. |

## 4. Test đã viết (mới — chưa từng có cho phần SV3)

Trước phiên này, **chỉ SV2 có unit test** (`iot_gateway/test_rule_engine.py`). Phần SV3
(REST API, ThingsBoard bridge) chưa có test nào. Đã bổ sung 3 file, theo đúng triết lý
"pure function, không cần broker/DB thật" mà `rule_engine.py` đã đặt ra:

- **`iot_gateway/test_tb_gateway.py`** (23 test) — tách 5 hàm thuần khỏi `tb_gateway.py`
  (`rpc_to_command`, `rpc_action_from_params`, `build_telemetry_values`,
  `build_alarm_values`, `build_gateway_telemetry`, `map_shared_attributes`) để test logic
  chuyển đổi RPC/telemetry/shared-attributes độc lập với MQTT/ThingsBoard thật.
- **`iot_gateway/test_gateway_config.py`** (5 test) — test `handle_config_update()` mới
  thêm, gồm 1 test "đóng vòng lặp" chứng minh update runtime ảnh hưởng `evaluate()` ngay.
- **`gateway_api/test_api.py`** (17 test) — dùng `fastapi.testclient.TestClient`, mock
  `mqtt_client.publish` và `query_api.query` (patch `mqtt.Client.connect`/`loop_start`
  TRƯỚC khi import `api.py`, vì module này tự nối MQTT — retry vô hạn — ngay khi import,
  nếu không patch thì việc import sẽ treo vô thời hạn khi không có broker thật).
  Phủ: validate input (target/action sai → 400), 404 cho bin không tồn tại, đúng
  topic/payload publish khi gửi command, fallback khi InfluxDB lỗi/không có data,
  endpoint `/config` (GET trả default, POST validate + publish retained).

**Tổng: 50 test, tất cả pass** (33 trong `iot_gateway`, 17 trong `gateway_api`).

Chạy:
```bash
python -m unittest discover -s iot_gateway -p "test_*.py" -v
python -m unittest discover -s gateway_api -p "test_*.py" -v
```
(Cần `fastapi`, `pydantic`, `influxdb-client`, `paho-mqtt` đã cài — xem `requirements.txt`
từng thư mục. Không cần Docker/broker/InfluxDB thật để chạy test.)

## 5. Việc còn mở / khuyến nghị

| # | Việc | Độ khó | Ai nên làm |
|---|---|---|---|
| 1 | Merge `sv3-thingsboard-restapi` vào `main` (qua PR, theo đúng quy trình repo đã dùng cho SV1/SV2 — xem `git log --all` có PR #1, #2) | Thấp | Bạn (SV3), nhưng nên có teammate review vì đụng `gateway.py` |
| 2 | Thông báo cho SV2 về thay đổi trong `gateway.py` (subscribe topic config mới) | Thấp | Bạn |
| 3 | Health check cho `gateway-api`/`grafana`/`tb-gateway` trong `docker-compose.yml` (mosquitto, influxdb đã có) | Thấp | SV3 |
| 4 | Đồng bộ version pin `paho-mqtt` — `gateway_api` ghim `==1.6.1`, `iot_gateway` ghim `>=2.0.0`. Không phải bug (cả hai đều chạy được do code không dùng API mới ở `api.py`), nhưng nên thống nhất 1 version cho dễ bảo trì | Thấp | Tuỳ chọn |
| 5 | Tạo Alarm rule + Dashboard trên ThingsBoard Cloud UI, chụp ảnh cho báo cáo | Thấp, không cần code | Bạn |
| 6 | Test tích hợp thật (docker compose up + curl + mosquitto_pub/sub) — hiện chỉ có unit test mock | Trung bình | Tuỳ chọn, nếu muốn chắc chắn hơn trước khi nộp |

## 6. Trạng thái Git

- Branch làm việc: `sv3-thingsboard-restapi`, đã có commit `36cc654` **đã push lên
  `origin/sv3-thingsboard-restapi`** từ phiên trước.
- Phiên này có thêm các thay đổi (bug fix + tests + README) **chưa commit** tại thời điểm
  viết file này — xem `git status` / `git diff` để commit trước khi push tiếp.
- `origin/main` vẫn ở `dd2489f` (trước SV3) — cần tạo Pull Request `sv3-thingsboard-restapi → main`
  để teammate review, khớp quy trình đã dùng cho SV1 (`PR #1`) và SV2 (`PR #2`).
