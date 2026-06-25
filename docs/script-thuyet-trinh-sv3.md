# Script thuyết trình phần SV3 — Trương Tuấn Nghĩa

Dựa theo các slide 17, 18, 19, 20, 21 và phụ lục 27 trong `BaoCao_WasteGateway_HUST.pptx`. Phần của bạn nói về ba việc: trực quan hóa dữ liệu (Grafana và ThingsBoard), REST API, và triển khai bằng Docker Compose. Mỗi slide dưới đây có ba phần: nội dung trên slide, mô tả ảnh để biết chỉ tay vào đâu, và lời thuyết trình gợi ý có thể đọc gần như nguyên văn.

Câu chuyển tiếp khi nhận lời từ người nói trước (phần Rule Engine của SV2): "Em xin tiếp tục phần của em, nói về cách dữ liệu sau khi qua rule engine được hiển thị ra ngoài và cách hệ thống được đóng gói để chạy."

---

## Slide 17 — Trực quan hóa và tích hợp đám mây (slide mở đầu phần)

**Nội dung trên slide.** Hai bullet chính: Grafana có sáu bảng hiển thị mức đầy, nhiệt độ, methane, trạng thái, sự kiện, cấu hình tự động; ThingsBoard đồng bộ hai chiều qua Gateway MQTT API, ba thiết bị con tự xuất hiện, chiều xuống có RPC chuyển thành command và Alarm Rule ở hai mức Critical và Warning.

**Không có ảnh, đây là slide giới thiệu, chỉ có chữ.**

**Lời thuyết trình.**

"Sau khi gateway xử lý xong rule engine, dữ liệu cần được nhìn thấy được. Nhóm em dùng hai công cụ song song cho hai mục đích khác nhau. Grafana đọc trực tiếp từ InfluxDB, phục vụ giám sát tại biên, không cần Internet. ThingsBoard Cloud thì đồng bộ hai chiều: chiều lên là telemetry, chiều xuống là lệnh điều khiển RPC. Ba thiết bị con bin-01, bin-02, bin-03 không cần tạo tay trên ThingsBoard, chúng tự xuất hiện khi gateway gọi đúng API kết nối thiết bị. Slide sau em sẽ cho thầy cô xem cụ thể từng bên."

---

## Slide 18 — Grafana

**Nội dung trên slide.** Một ảnh chụp toàn màn hình dashboard Grafana, chia thành tám ô nhỏ.

**Mô tả ảnh, đọc từ trái sang phải, trên xuống dưới.**

1. Góc trên trái: biểu đồ đường "Mức đầy theo thùng (fill_level %)", ba đường màu khác nhau cho ba thùng, có một đường ngang màu đỏ là ngưỡng tám mươi lăm phần trăm. Có thể thấy các đường tăng dần rồi rơi thẳng xuống gần không, đó là lúc xe mô phỏng đến thu gom.
2. Góc trên phải: biểu đồ tương tự nhưng là "Khối lượng theo thùng (weight_kg)", hình dạng giống biểu đồ mức đầy vì khối lượng tỉ lệ thuận với mức đầy.
3. Giữa trái: "Nhiệt độ theo thùng (°C)", đường gần như bằng phẳng quanh ba mươi độ, có hai điểm nhô vọt lên gần sáu mươi, đó là lúc rule engine phát hiện fire_risk.
4. Giữa phải: "Methane theo thùng (ppm)", nhiều điểm tăng vọt hẳn lên tám trăm đến một nghìn ppm, vượt xa đường ngưỡng đỏ năm trăm, mô phỏng các lần túi khí bị vỡ.
5. Dưới trái: bảng "Trạng thái thiết bị theo thùng", các cột buzzer, compactor, dispatch, lock theo từng bin_id, giá trị on hoặc off.
6. Dưới phải: biểu đồ cột "Số event theo thời gian", mỗi màu một loại sự kiện, có chú giải bin_full, bin_tilted, gas_alert, overweight ở dưới.
7. Cuối trái: bảng "Thùng cần thu gom hiện tại (fill_level > 85)", chỉ hiện đúng những thùng đang vượt ngưỡng, ví dụ bin-02 ở tám mươi lăm phần trăm.
8. Cuối phải: bảng "Event gần nhất", liệt kê thời gian, bin_id, loại sự kiện, mức độ nghiêm trọng và ngưỡng tương ứng.

**Lời thuyết trình.**

"Đây là dashboard Grafana của nhóm em, được cấu hình tự động ngay khi container khởi động, không cần ai vào tạo tay. Em xin chỉ vào panel mức đầy trước, thầy cô có thể thấy ba đường tăng dần đúng theo công thức có xu hướng đã trình bày ở phần sensor, và khi vượt đường đỏ tám mươi lăm phần trăm thì rơi thẳng về không, đó là lúc gateway cho rằng xe đã đến thu gom. Hai panel nhiệt độ và methane cho thấy rõ những lần tăng đột biến, methane có thể vọt lên gần một nghìn ppm, vượt xa ngưỡng cảnh báo. Quan trọng nhất với đề bài là hai bảng dưới cùng bên trái: bảng thùng cần thu gom hiện tại, suy trực tiếp từ mức đầy mới nhất, và bảng event gần nhất để biết chuyện gì vừa xảy ra."

---

## Slide 19 — ThingsBoard

**Nội dung trên slide.** Hai ảnh chụp đặt cạnh nhau.

**Mô tả ảnh bên trái.** Trang chi tiết thiết bị bin-01 trên ThingsBoard, tab Latest telemetry đang mở, một danh sách khóa và giá trị: alarm_action là "ventilation_suggested", alarm_severity là "warning", alarm_threshold, alarm_type là "gas_alert", alarm_value, buzzer là "on", compactor là "off", dispatch là "off", cùng cột thời gian cập nhật cuối ở bên trái mỗi dòng.

**Mô tả ảnh bên phải.** Dashboard ThingsBoard tên "Smart Waste Management" với bốn khối: hai biểu đồ đường ở trên cùng tên "Mức đầy theo thùng" và "Nhiệt độ và Methane theo thùng", một bảng "Entities" liệt kê ba dòng bin-01, bin-02, bin-03 với các cột fill phần trăm, status (low, medium), kg, lock, compactor, buzzer, dispatch, và cuối cùng là bảng "Alarms" với các dòng cảnh báo loại "Warning Device Alarm", cột Status hiện "Active Unacknowledged" hoặc "Cleared Unacknowledged".

**Lời thuyết trình.**

"Bên trái là góc nhìn từng thiết bị, đây là trang chi tiết của bin-01. Mọi khóa telemetry mà gateway đẩy lên đều xuất hiện ở đây theo thời gian thực, bao gồm cả các khóa alarm_type, alarm_severity mà em sẽ giải thích kỹ hơn ở phần sau. Bên phải là dashboard tổng hợp cho cả ba thùng cùng lúc, có bảng Entities để so sánh nhanh trạng thái giữa các thùng, và quan trọng nhất là bảng Alarms ở dưới, đây là cảnh báo thật của ThingsBoard, được tạo ra từ Alarm Rule mà nhóm em cấu hình trên Device Profile, không phải chỉ hiển thị log thông thường. Khi một cảnh báo không còn đúng điều kiện nữa, nó tự chuyển sang trạng thái Cleared như thầy cô thấy ở dòng thứ hai."

---

## Slide 20 — REST API và cấu hình thời gian thực

**Nội dung trên slide.** Ba bullet và một sơ đồ nhỏ phía dưới: hộp "REST POST /config" và hộp "ThingsBoard Shared Attributes" cùng mũi tên chỉ vào một hộp "topic waste/gateway/config", từ đó mũi tên tiếp tục chỉ sang hộp "Gateway cập nhật ngưỡng" với chú thích nhỏ "hiệu lực tức thì".

**Không có ảnh chụp màn hình, đây là sơ đồ vẽ tay bằng công cụ trình chiếu.**

**Lời thuyết trình.**

"Phần REST API của nhóm em dùng FastAPI, tự sinh tài liệu Swagger ngay tại địa chỉ cổng tám nghìn không trăm lẻ một, đường dẫn slash docs, nên ai cũng thử được từng endpoint trực tiếp trên trình duyệt mà không cần viết code gọi thử. Điểm em muốn nhấn mạnh nhất ở slide này là khả năng chỉnh ngưỡng rule engine ngay khi hệ thống đang chạy, không cần khởi động lại container. Như sơ đồ thầy cô thấy, có hai nguồn cùng đổ vào một topic duy nhất tên waste/gateway/config: một là lệnh POST tới REST API, hai là Shared Attributes từ ThingsBoard. Cả hai đều đi qua đúng một đường, gateway subscribe topic đó và áp giá trị mới vào ngay, có hiệu lực từ bản telemetry kế tiếp. Đây là yêu cầu nâng cao của đề bài và nhóm em đã viết unit test chứng minh nó có tác dụng thật, không chỉ tồn tại trên giấy."

---

## Slide 21 — Triển khai và ảo hóa

**Nội dung trên slide.** Bốn bullet, không có ảnh: một lệnh docker compose up khởi tạo trên mười một container; toàn bộ tham số qua biến môi trường và file .env theo chuẩn mười hai yếu tố; có healthcheck, chính sách restart, mạng riêng, volume lưu dữ liệu; thành phần ThingsBoard tách riêng theo profile, chỉ kích hoạt khi cần.

**Lời thuyết trình.**

"Phần cuối của em là cách đóng gói toàn bộ hệ thống. Chỉ một lệnh docker compose up dash d build build sẽ khởi tạo đủ mười một container, từ broker, sáu container sensor và actuator, gateway, InfluxDB, Grafana đến REST API. Mọi tham số, từ địa chỉ broker đến các ngưỡng rule engine, đều đọc từ file .env, không hard-code trong mã nguồn, đúng nguyên tắc mười hai yếu tố mà các hệ thống production thường dùng. Mosquitto và InfluxDB có healthcheck riêng để Docker biết khi nào dịch vụ thật sự sẵn sàng, không chỉ là container đã khởi động. Một quyết định thiết kế đáng nói là thành phần kết nối ThingsBoard được tách riêng theo một profile tên thingsboard, chỉ bật khi người dùng đã có token hợp lệ, để tránh báo lỗi không cần thiết cho người chỉ muốn chạy phần lõi tại biên."

---

## Phụ lục, slide 27 — REST API endpoints

**Nội dung trên slide.** Một bảng ba cột Method, Endpoint, Chức năng, liệt kê GET /health, GET /bins và /bins/{id}/state, GET /bins/{id}/events, POST /bins/{id}/command, GET/POST /config.

**Lời thuyết trình, kèm một cập nhật nhỏ nên nói thêm bằng miệng vì bảng trên slide chưa kịp cập nhật.**

"Đây là toàn bộ endpoint của REST API. Slash health để kiểm tra tình trạng dịch vụ, slash bins và slash bins kèm id slash state để xem trạng thái từng thùng, slash bins kèm id slash events để xem sự kiện gần đây, post lên slash bins kèm id slash command để gửi lệnh điều khiển thủ công, và get hoặc post slash config để xem và chỉnh ngưỡng rule engine như em vừa trình bày. Sau buổi báo cáo này nhóm em có bổ sung thêm một endpoint nữa là get slash collection slash route, trả về danh sách thùng đang cần thu gom ngay tại thời điểm gọi, đúng theo yêu cầu của đề bài mà bảng trên slide chưa kịp cập nhật, em xin bổ sung bằng lời ở đây."

---

## Câu chuyển tiếp sang phần demo hoặc phần SV2 tổng kết

"Đến đây là hết phần trình bày kỹ thuật của em. Nếu được, em xin chuyển sang phần demo trực tiếp để thầy cô thấy toàn bộ những gì em vừa nói đang chạy thật, không chỉ là ảnh chụp." (Xem chi tiết các bước demo tại `docs/kich-ban-demo.md`.)
