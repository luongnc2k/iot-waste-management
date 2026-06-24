# Hướng dẫn dựng Dashboard trên ThingsBoard Cloud

> Đề tài 5 — Virtual Smart Waste Management Gateway (IT6130).
> Dashboard ThingsBoard dựng trên web UI `https://thingsboard.cloud`. Tài liệu này
> hướng dẫn từng bước, dùng **đúng tên telemetry/RPC** mà `tb_gateway.py` gửi lên.

## 0. Dữ liệu sẵn có trên ThingsBoard

Sau khi `tb-gateway` chạy (`docker compose --profile thingsboard up -d --build tb-gateway`),
trên ThingsBoard sẽ có:

- **3 device con**: `bin-01`, `bin-02`, `bin-03` (tự xuất hiện dưới gateway `waste-gateway`).
- **Telemetry mỗi bin**: `fill_level` (%), `weight_kg`, `methane_ppm`, `temperature` (°C), `fill_status` (low/medium/high).
- **Trạng thái actuator**: `lock`, `compactor`, `buzzer`, `dispatch` (giá trị `on`/`off`) — tb-gateway đẩy lên khi actuator phản hồi status.
- **Telemetry alarm (khi có event)**: `alarm_type`, `alarm_severity`, `alarm_value`, `alarm_threshold`, `alarm_action`.
- **RPC điều khiển** (server→device): `setLock`, `setCompactor`, `setBuzzer`, `setDispatch` — params `true`=on / `false`=off.
- **Shared attributes** chỉnh ngưỡng: `fill_dispatch`, `fill_critical`, `temp_fire`, `methane_alert`, `weight_lock`.

**Kiểm tra trước khi vẽ:** Devices → mở `bin-01` → tab **Latest telemetry** phải thấy `fill_level`... cập nhật.

---

## 0b. CÁCH NHANH — Import dashboard có sẵn (khuyến nghị)

Repo đã kèm file dashboard dựng sẵn: **`thingsboard/waste-management-dashboard.json`**
(4 widget: 2 biểu đồ time-series + bảng trạng thái + bảng cảnh báo, dùng đúng FQN của
ThingsBoard hiện hành).

1. **Dashboards** → **＋** → **Import dashboard**.
2. Chọn file `thingsboard/waste-management-dashboard.json`.
3. ThingsBoard hỏi map **entity alias** `Bins` → alias đã cấu hình lọc *Device name starts with*
   `bin-` nên thường tự khớp; nếu được hỏi, xác nhận/chọn `bin-01, bin-02, bin-03`.
4. **Import** → mở dashboard. Đặt khung thời gian (góc phải) = *Last 30 minutes*.

Sau khi import, bạn có ngay: biểu đồ mức đầy theo từng thùng, biểu đồ nhiệt độ & methane,
bảng trạng thái (fill/khối lượng/lock/compactor/buzzer/dispatch), và bảng cảnh báo.
Muốn thêm **bản đồ màu theo mức đầy** và **nút RPC** thì làm thêm theo mục 2.5–2.6 dưới đây.

> Nếu import báo "widget type not found" (do lệch phiên bản TB), dựng tay theo mục 1–2.

## 1. Tạo Dashboard và Entity Alias (dựng tay — nếu không import)

1. **Dashboards** → **+** → **Create new dashboard** → Title: `Smart Waste Management`.
2. Mở dashboard → vào **Edit** (bút chì góc phải).
3. Tạo alias gom 3 thùng: **Entity aliases** (biểu tượng) → **Add alias**:
   - Name: `Bins`
   - Filter type: **Device**
   - Type: **Device name starts with** → giá trị `bin-`
   - (Tùy chọn) tick *Resolve as multiple entities* để 1 widget hiển thị cả 3 thùng.
4. **Add alias** thứ hai cho điều khiển từng thùng (RPC cần 1 device cụ thể):
   - Name: `Bin selected` · Filter: **Device** · *Device list* → chọn `bin-01` (đổi khi demo thùng khác),
     hoặc dùng **Entity from dashboard state** nếu muốn chọn động.

---

## 2. Các widget (bám yêu cầu mục 5.10 & 5.12 đề bài)

> Với mỗi widget: **Add widget** → chọn bundle → cấu hình **Datasource** = alias `Bins`, chọn key tương ứng.

### 2.1. Mức đầy theo thời gian — *Time series Chart*
- Bundle **Charts** → **Time series Chart**.
- Datasource: alias `Bins`, keys: `fill_level`.
- Settings → **Thresholds**: thêm đường ngang `value = 85` (màu đỏ) — ngưỡng điều xe.
- (Lặp lại tạo thêm chart cho `weight_kg`, `temperature`, `methane_ppm` nếu muốn đủ 4 chỉ số.)

### 2.2. Gauge mức đầy hiện tại — *Radial gauge*
- Bundle **Gauges** → **Radial gauge**.
- Datasource: alias `Bins`, key `fill_level`. Min 0, Max 100, unit `%`.
- Color ranges: 0–50 xanh, 50–85 vàng, 85–100 đỏ.
- Vì alias resolve nhiều thùng → đặt 3 gauge (mỗi gauge 1 device) hoặc dùng *Latest values → Value card* cho gọn.

### 2.3. Bảng thùng cần thu gom — *Entities table*
- Bundle **Cards** → **Entities table**.
- Datasource: alias `Bins`, columns: `fill_level`, `fill_status`, `weight_kg`.
- **Filter** (phễu): thêm điều kiện `fill_level >= 85` → bảng chỉ hiện thùng cần thu gom.
- Sort theo `fill_level` desc.

### 2.4. Trạng thái thiết bị — *Entities table* hoặc *Value cards*
- Bundle **Cards** → **Entities table** (hoặc **Value card** cho từng thùng).
- Datasource: alias `Bins`, keys: `lock`, `compactor`, `buzzer`, `dispatch` (giá trị `on`/`off`).
- (tb-gateway đã đẩy 4 trường này lên TB — xem mục 5.)

### 2.5. Bản đồ thùng rác (màu theo mức đầy) — *Map*
- Bundle **Maps** → **OpenStreetMap** (hoặc Image Map nếu không có tọa độ thật).
- **Cần tọa độ:** telemetry không có lat/long → thêm **server attribute** cho mỗi bin:
  Devices → `bin-01` → **Attributes** → Server attributes → Add:
  `latitude = 21.0045`, `longitude = 105.8430` (đổi cho mỗi bin lệch nhau một chút).
- Map widget: Datasource alias `Bins`; Latitude key `latitude`, Longitude key `longitude`.
- **Color function** theo mức đầy (Settings → Markers → Color):
  ```javascript
  if (data.fill_level > 85) return 'red';
  if (data.fill_level >= 50) return 'orange';
  return 'green';
  ```

### 2.6. Nút điều khiển RPC — *Control widgets*
- Bundle **Control widgets** → **Switch control** (hoặc **Round switch**).
- Target device: alias `Bin selected`.
- Settings:
  - **RPC set value method**: `setCompactor` (tạo các switch khác cho `setLock`, `setBuzzer`, `setDispatch`).
  - **Value to data**: on → `true`, off → `false` (khớp `rpc_action_from_params`: bool → on/off).
- Bấm switch → TB gửi RPC → `tb-gateway` chuyển thành command MQTT → actuator thực thi.
- Kiểm chứng: `docker logs -f tb-gateway` thấy `RPC setCompactor(True) → bin-01/compactor=on`.

### 2.7. Bảng cảnh báo — *Alarms table*
- Bundle **Alarm widgets** → **Alarms table**.
- Datasource: alias `Bins`. Hiển thị các alarm `bin_full` / `fire_risk` sinh bởi Rule Chain (mục 3).

---

## 3. Cấu hình Alarm (bin_full / fire_risk)

Repo đã có sẵn cấu hình node alarm trong `thingsboard/`:
- `critical_device_alarm.json`, `warning_device_alarm.json` — node *Create Alarm* đọc `alarm_severity`, `alarm_type`, `alarm_value` (telemetry mà tb-gateway đẩy lên khi có event).
- `default.json` — device profile mặc định.

> **Quan trọng:** widget Alarms để trống ("No alarms found") nếu CHƯA có luật tạo alarm.
> Gateway chỉ đẩy *telemetry* `alarm_severity/alarm_type/...`; phải có **Alarm rule** đọc các
> telemetry đó và *tạo* alarm thì bảng mới có dữ liệu.
>
> ⚠️ Lưu ý: KHÔNG import được alarm bằng "Import device profile" — bản hiện tại của
> thingsboard.cloud **bỏ phần alarm rules** khi import profile JSON. Dùng một trong hai cách dưới.

### Cách A (ĐÃ KIỂM CHỨNG) — dùng node Create Alarm có sẵn trong repo
Repo có `thingsboard/critical_device_alarm.json` & `warning_device_alarm.json` — cấu hình node
*Create Alarm* (đọc `alarm_severity`/`alarm_type`/`alarm_value`), đã chạy thật (alarm type hiện
ra: **"Warning Device Alarm" / "Critical Device Alarm"**).

- **Rule chains** → Root rule chain → thêm node **Create Alarm** → dán nội dung file tương ứng
  (Severity/Type/Value tham chiếu `alarm_severity`/`alarm_type`/`alarm_value`) → nối từ nhánh
  *Post telemetry* → **Save**.
- (Tùy chọn §5.17.5: nối thêm node **Send Email/Telegram** sau Create Alarm để thông báo.)

### Cách B — Tạo tay Device Profile Alarm rules (thay thế, schema do UI tự sinh nên luôn đúng)

1. **Profiles → Device profiles** → mở profile của bin (`default` hoặc `waste-bin`) → **Edit** (bút) → tab **Alarm rules** → **＋**.
2. **Rule 1 — Warning:** *Alarm type* `Waste Warning` → severity **Warning** → **Add key filter**:
   - *Key type* **Time series** · *Key* `alarm_severity` · *Value type* **String** · *Operation* **Equal** · *Value* `warning`
3. **＋** lần nữa — **Rule 2 — Critical:** *Alarm type* `Waste Critical` → severity **Critical** → `alarm_severity` **Equal** `critical`.
4. **Save** profile.

Sau đó: khi gateway phát event **warning** (`bin_full`/`overweight`/`gas_alert`) hoặc **critical**
(`fire_risk`) → tb-gateway đẩy `alarm_severity` → TB tạo alarm → hiện ở widget **Alarms**.
Warning xuất hiện nhanh (các event này thường xuyên); critical cần `fire_risk` hoặc `fill>95`.

<!-- Bản chi tiết Rule Chain (type alarm động bin_full/fire_risk) — dùng cấu hình trong -->
<!-- Cách C — Rule Chain (giữ tham khảo) -->
### Cách C — Rule Chain (tham khảo)
Rule chains → Root rule chain → thêm node **Create Alarm** dùng cấu hình trong
`thingsboard/critical_device_alarm.json` & `warning_device_alarm.json` (Severity/Type/Value
tham chiếu `alarm_severity`/`alarm_type`/`alarm_value`). Cho phép alarm mang đúng tên loại
event. (Tùy chọn §5.17.5: nối thêm node **Send Email/Telegram** để thông báo.)

---

## 4. Hoàn tất

1. **Save** dashboard (đĩa mềm).
2. (Tùy chọn) Devices → `waste-gateway` → gán dashboard này làm **default dashboard**.
3. Chụp màn hình cho báo cáo: dashboard tổng, map màu theo mức đầy, alarm table, và log `tb-gateway` khi bấm RPC.

---

## 5. Trạng thái actuator trên TB (đã bổ sung)

`tb_gateway.py` đã được mở rộng: `local_on_connect` subscribe thêm `waste/+/actuator/status`,
và `local_on_message` dùng hàm thuần `build_actuator_values(status)` đẩy `lock/compactor/
buzzer/dispatch` (on/off) lên `v1/gateway/telemetry`. Payload error của actuator (thiếu 4
trường) bị bỏ qua. Đã có unit test `TestBuildActuatorValues` trong `test_tb_gateway.py`.

→ Trên TB, 4 trường này xuất hiện trong **Latest telemetry** của mỗi bin và dùng được cho
widget ở mục 2.4. (Cần chạy lại `tb-gateway` với token thật để thấy dữ liệu.)

---

## 5b. Xuất / Nhập dashboard dưới dạng JSON (để tái sử dụng & nộp kèm)

ThingsBoard **không** dựng dashboard từ file provisioning như Grafana; dashboard tạo trên
UI và lưu trong tài khoản. Để có một file JSON tái dùng được (import lại hoặc nộp kèm
báo cáo), cách **đáng tin cậy nhất** là:

1. Dựng dashboard một lần theo mục 1–4 ở trên.
2. **Dashboards** → mở dashboard → menu **⋮** → **Export dashboard** → lưu file
   `smart-waste-management.json` (đề xuất đặt vào `thingsboard/` cùng các file alarm).
3. Khi cần dùng lại (máy khác / tài khoản khác): **Dashboards** → **+** → **Import dashboard**
   → chọn file → TB sẽ hỏi **map entity alias** `Bins` sang device thật (chọn lọc theo tên
   `bin-` như mục 1).

> Lưu ý: file JSON export gắn với **phiên bản ThingsBoard** lúc export và chứa id widget
> nội bộ; vì vậy nên export từ chính tài khoản dùng để demo. Một file JSON viết tay sẵn
> thường import lỗi/không khớp widget giữa các phiên bản — nên không đính kèm sẵn ở đây.

## 6. Bản đồ widget ↔ yêu cầu đề bài (mục 5.10)

| Yêu cầu | Widget | Mục |
|---|---|---|
| Bản đồ thùng rác màu theo mức đầy | Map + color function | 2.5 |
| Gauge mức đầy | Radial gauge | 2.2 |
| Bảng thùng cần thu gom | Entities table + filter `fill_level≥85` | 2.3 |
| Nút RPC setCompactor/setLock/setBuzzer/setDispatch | Switch control | 2.6 |
| Alarm bin_full/fire_risk (Rule Chain) | Alarms table + alarm rule | 2.7, 3 |
| (Bổ sung) biểu đồ telemetry theo thời gian | Time series Chart | 2.1 |
