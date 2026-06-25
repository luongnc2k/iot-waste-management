# Hướng dẫn setup tất cả các loại demo

Tài liệu này tổng hợp cách dựng từng phần của hệ thống để demo, từ stack Docker cơ bản đến tích hợp ThingsBoard Cloud. Mỗi mục độc lập, có thể đọc riêng theo nhu cầu mà không cần đọc hết toàn bộ tài liệu.

## 1. Demo stack chính bằng Docker Compose

Đây là phần bắt buộc, chạy được trên máy cá nhân, không cần tài khoản cloud nào.

```bash
git clone https://github.com/luongnc2k/iot-waste-management.git
cd iot-waste-management
cp .env.example .env
docker compose up -d --build
docker compose ps
```

Mười một container sẽ khởi động: `mosquitto`, `influxdb`, `waste-gateway`, ba cặp `sensor-bin-01/02/03` và `actuator-bin-01/02/03`, `grafana`, `gateway-api`. Riêng `tb-gateway` không nằm trong nhóm này, xem mục 3.

Xem log để xác nhận dữ liệu đang chạy:
```bash
docker compose logs -f waste-gateway
docker compose logs -f sensor-bin-01
```

Dừng và dọn dẹp khi xong:
```bash
docker compose down        # giữ lại volume dữ liệu
docker compose down -v     # xóa cả volume, dùng khi muốn làm lại từ đầu
```

## 2. Demo REST API (FastAPI)

Không cần thêm cấu hình, chạy ngay khi stack chính đã lên.

* Giao diện Swagger tự sinh: mở `http://localhost:8001/docs`, có thể thử trực tiếp từng endpoint trên trình duyệt.
* Gọi bằng `curl` nếu muốn demo qua terminal:

```bash
curl http://localhost:8001/health
curl http://localhost:8001/bins
curl http://localhost:8001/bins/bin-01/state
curl http://localhost:8001/bins/bin-01/events
curl http://localhost:8001/collection/route
curl -X POST http://localhost:8001/bins/bin-01/command \
  -H "Content-Type: application/json" \
  -d '{"target":"dispatch","action":"on","reason":"manual"}'
curl http://localhost:8001/config
curl -X POST http://localhost:8001/config \
  -H "Content-Type: application/json" \
  -d '{"temp_fire": 50}'
```

## 3. Demo tích hợp ThingsBoard Cloud

Phần này cần một tài khoản ThingsBoard Cloud miễn phí và một Gateway Device riêng. Mỗi người dùng nên tạo token riêng, không dùng chung token đã commit trong lịch sử trò chuyện hoặc tài liệu nội bộ.

### 3.1. Tạo Gateway Device

1. Đăng nhập `https://thingsboard.cloud`.
2. Vào **Devices**, bấm nút **+**, chọn **Add new device**.
3. Đặt tên, ví dụ `waste-gateway`, bật công tắc **Is gateway**, bấm **Add**.
4. Mở thiết bị vừa tạo, vào tab **Credentials**, copy **Access token**.

### 3.2. Cấu hình và khởi động bridge

```bash
echo "TB_GATEWAY_TOKEN=<token_vừa_copy>" >> .env
docker compose --profile thingsboard up -d --build tb-gateway
docker logs -f tb-gateway
```

Log đúng phải hiện `Connected to ThingsBoard Cloud` và `Connected device: bin-01/02/03`, không lặp lại dòng `TB disconnected` liên tục. Nếu thấy vòng lặp connect rồi disconnect mỗi một đến hai giây, đó là dấu hiệu code cũ chưa vá bug định dạng API shared attributes; đảm bảo đang dùng phiên bản mới nhất của nhánh `feat/dashboard-thingsboard-report` hoặc `main`.

### 3.3. Kiểm tra trên ThingsBoard

* Vào **Devices**, xác nhận `bin-01`, `bin-02`, `bin-03` tự xuất hiện dưới gateway.
* Mở một thiết bị con, tab **Latest telemetry**, xác nhận `fill_level`, `temperature`, `methane_ppm` cập nhật định kỳ.
* Tab **Alarms** hiện cảnh báo khi có sự kiện `fire_risk` hoặc `bin_full` xảy ra, nếu Alarm Rule đã được cấu hình (xem mục 3.4).

### 3.4. Tạo Alarm Rule (tùy chọn, cộng điểm nâng cao)

ThingsBoard Cloud có trợ lý AI tích hợp (biểu tượng nổi ở góc dưới phải màn hình) có thể tạo Alarm Rule trực tiếp nếu được mô tả rõ yêu cầu. Mô tả gợi ý:

```text
Tạo Alarm Rule trên Device Profile "default": mức CRITICAL khi telemetry
alarm_severity bằng "critical", mức WARNING khi alarm_severity bằng "warning".
Mỗi mức có Clear Rule tương ứng khi alarm_severity khác giá trị đó.
```

File JSON tham khảo của hai rule đã tạo thành công nằm tại `thingsboard/` (nếu có trong nhánh đang dùng), dùng để đối chiếu cấu trúc nếu cần tạo lại thủ công.

### 3.5. Gửi RPC điều khiển từ ThingsBoard

Trên trang chi tiết thiết bị con, ví dụ `bin-01`, dùng chức năng gửi RPC hoặc gọi REST API của ThingsBoard với payload:
```json
{"method": "setBuzzer", "params": false}
```
Bốn phương thức được hỗ trợ: `setLock`, `setCompactor`, `setBuzzer`, `setDispatch`.

## 4. Demo Grafana

Không cần thêm cấu hình thủ công, datasource và dashboard được provisioning tự động khi container `grafana` khởi động.

1. Mở `http://localhost:3000`.
2. Đăng nhập bằng `GRAFANA_USER` và `GRAFANA_PASSWORD` trong `.env`, mặc định `admin` / `admin`.
3. Dashboard có sẵn xuất hiện ngay ở trang chủ, gồm tám panel mô tả ở Mục 12 của báo cáo.

Nếu muốn kiểm tra datasource đã trỏ đúng nơi: vào **Connections → Data sources → InfluxDB**, xác nhận URL là `http://influxdb:8086` (dùng tên service, không phải `localhost`), tổ chức `hust`, bucket `iot`.

## 5. Demo kiểm thử MQTT thủ công

Hữu ích khi muốn cho thấy giao thức MQTT thật, không qua lớp trừu tượng nào.

```bash
# Theo dõi toàn bộ telemetry của mọi thùng
docker exec mosquitto mosquitto_sub -t "waste/+/sensor/telemetry" -v

# Theo dõi sự kiện gateway phát ra
docker exec mosquitto mosquitto_sub -t "waste/+/gateway/event" -v

# Gửi lệnh thủ công thẳng vào actuator, không qua REST API
docker exec mosquitto mosquitto_pub \
  -t waste/bin-01/actuator/command \
  -m '{"bin_id":"bin-01","target":"dispatch","action":"on","reason":"manual"}'
```

## 6. Demo bộ unit test

Chạy được trên máy host, không cần Docker, không cần broker hay database thật.

```bash
python -m venv .venv-test
source .venv-test/bin/activate
pip install -r gateway_api/requirements.txt -r iot_gateway/requirements.txt
python -m unittest discover -s iot_gateway -p "test_*.py" -v
python -m unittest discover -s gateway_api -p "test_*.py" -v
python -m unittest discover -s virtual_sensor -p "test_*.py" -v
```

Tổng số hơn sáu mươi test phải hiện `OK` ở cuối mỗi lần chạy.

## 7. Bảng tra cứu nhanh cổng dịch vụ

| Dịch vụ | Địa chỉ | Tài khoản mặc định |
|---|---|---|
| REST API (Swagger) | `http://localhost:8001/docs` | không cần đăng nhập |
| Grafana | `http://localhost:3000` | `admin` / `admin` |
| InfluxDB | `http://localhost:8086` | `admin` / `admin12345` |
| MQTT Broker | `localhost:1883` | `iot_user` / `iot_pass_2026` |
| ThingsBoard Cloud | `https://thingsboard.cloud` | tài khoản cá nhân từng người |

## 8. Khắc phục sự cố thường gặp

* **Container báo lỗi liên tục khi khởi động.** Kiểm tra `docker compose logs <tên service>`, phần lớn do `.env` thiếu biến hoặc Docker Desktop chưa chạy.
* **`tb-gateway` không kết nối được.** Kiểm tra `TB_GATEWAY_TOKEN` đã đúng chưa, và `TB_HOST` nên là `mqtt.thingsboard.cloud` hoặc `thingsboard.cloud` tùy theo gợi ý hiện tại của ThingsBoard.
* **Grafana không có dữ liệu.** Xác nhận `waste-gateway` đang chạy và đã ghi được vào InfluxDB; kiểm tra bằng Data Explorer ở mục InfluxDB trước khi nghi ngờ Grafana.
* **Port bị chiếm.** Nếu máy đã có service khác dùng cùng cổng, đổi cổng host trong `docker-compose.yml` (giữ nguyên cổng container), ví dụ đổi `"8001:8000"` thành `"8002:8000"`.
