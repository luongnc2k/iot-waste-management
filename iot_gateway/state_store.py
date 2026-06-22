"""
State Store — Edge Waste Gateway (Đề tài 5, IT6130)
=====================================================
Vai trò trong hệ thống (SV2):
  Lưu trạng thái MỚI NHẤT của từng thùng tại gateway và duy trì DANH SÁCH
  THÙNG CẦN THU GOM (yêu cầu riêng của Đề tài 5, §5.9).

Vì sao cần một state store riêng?
  - paho-mqtt gọi callback trên thread mạng, còn vòng lặp bảo trì (offline
    check, mô phỏng thu gom) chạy trên main thread → truy cập state đồng thời.
    Mọi thao tác đọc/ghi state bọc trong một Lock để thread-safe.
  - Tách logic "nhớ trạng thái + debounce + edge-detect" khỏi gateway.py giúp
    code rõ ràng và test được độc lập.

Bốn nhóm dữ liệu được giữ:
  1. telemetry/actuator mới nhất từng thùng  → REST API (SV3) đọc /bins/{id}/state
  2. debounce lệnh (last_cmd)                 → chỉ gửi command khi trạng thái đổi
  3. edge-detect sự kiện (active_events)      → chỉ phát event tại cạnh lên
  4. danh sách thu gom (collection)           → /collection/route + mô phỏng xe
"""
import threading
import time

# Trạng thái mặc định của actuator khi gateway lần đầu thấy một thùng.
# Khớp với trạng thái khởi tạo của virtual_actuator (tất cả "off") → tránh
# gửi một loạt lệnh "off" thừa ngay khi thùng xuất hiện.
_DEFAULT_CMD = {"lock": "off", "compactor": "off", "buzzer": "off", "dispatch": "off"}


class StateStore:
    def __init__(self):
        self._lock = threading.Lock()
        self.telemetry: dict = {}      # bin_id -> telemetry đã normalize gần nhất
        self.actuator: dict = {}       # bin_id -> status actuator gần nhất
        self._last_seen: dict = {}     # bin_id -> time.monotonic() lần nhận telemetry cuối
        self._last_cmd: dict = {}      # bin_id -> {target: action} lệnh đã gửi gần nhất
        self._active_events: dict = {} # bin_id -> set(event_type) đang active
        self._collection: dict = {}    # bin_id -> time.monotonic() lúc bắt đầu cần thu gom
        self._offline: set = set()     # bin_id đang bị đánh dấu offline

    # ── Cập nhật từ message MQTT ──────────────────────────────────────────

    def update_telemetry(self, bin_id: str, normalized: dict):
        """Lưu telemetry mới nhất + đánh dấu thùng còn sống (cho offline-detect)."""
        with self._lock:
            self.telemetry[bin_id] = normalized
            self._last_seen[bin_id] = time.monotonic()
            self._last_cmd.setdefault(bin_id, dict(_DEFAULT_CMD))

    def update_actuator(self, bin_id: str, status: dict):
        """Lưu status actuator mới nhất (để REST/InfluxDB phản ánh đúng thực tế)."""
        with self._lock:
            self.actuator[bin_id] = status

    # ── Debounce lệnh: chỉ gửi khi trạng thái mong muốn THAY ĐỔI ───────────

    def command_changed(self, bin_id: str, target: str, action: str) -> bool:
        """
        Trả về True nếu (target → action) khác lệnh đã gửi gần nhất, đồng thời
        ghi nhận lệnh mới. Dùng để tránh gửi lặp command mỗi chu kỳ telemetry.
        """
        with self._lock:
            sent = self._last_cmd.setdefault(bin_id, dict(_DEFAULT_CMD))
            if sent.get(target) == action:
                return False
            sent[target] = action
            return True

    # ── Edge-detect sự kiện: chỉ phát tại CẠNH LÊN ────────────────────────

    def newly_fired_events(self, bin_id: str, current: set) -> set:
        """
        So sánh tập event_type đang đúng với tập đang active trước đó.
        Trả về các event VỪA chuyển sang active (cạnh lên) để phát/ghi một lần,
        đồng thời cập nhật tập active (event hết điều kiện sẽ tự được gỡ).
        """
        with self._lock:
            prev = self._active_events.get(bin_id, set())
            fired = current - prev
            self._active_events[bin_id] = set(current)
            return fired

    # ── Danh sách thùng cần thu gom (§5.9) ────────────────────────────────

    def mark_for_collection(self, bin_id: str):
        """Thêm thùng vào danh sách thu gom (idempotent — không reset đồng hồ)."""
        with self._lock:
            self._collection.setdefault(bin_id, time.monotonic())

    def collection_route(self) -> list:
        """
        Danh sách thùng cần thu gom, sắp theo thời gian chờ lâu nhất trước
        (gợi ý thứ tự thu gom — yêu cầu nâng cao §5.17.1). REST /collection/route
        của SV3 trả về danh sách này.
        """
        with self._lock:
            ordered = sorted(self._collection.items(), key=lambda kv: kv[1])
            return [bin_id for bin_id, _ in ordered]

    def due_for_collection(self, delay: float) -> list:
        """Các thùng đã nằm trong danh sách quá `delay` giây (xe mô phỏng tới nơi)."""
        now = time.monotonic()
        with self._lock:
            return [b for b, ts in self._collection.items() if now - ts >= delay]

    def clear_collection(self, bin_id: str):
        """Gỡ thùng khỏi danh sách sau khi đã thu gom (publish reset xong)."""
        with self._lock:
            self._collection.pop(bin_id, None)

    # ── Phát hiện sensor offline (rule 5 mở rộng / §5.17.4) ───────────────

    def offline_transitions(self, timeout: float):
        """
        Quét toàn bộ thùng đã biết, trả về (newly_offline, recovered):
          - newly_offline: thùng vừa quá `timeout` giây không gửi telemetry
          - recovered:     thùng trước offline nay đã gửi lại
        Gateway phát event sensor_offline cho newly_offline.
        """
        now = time.monotonic()
        newly_offline, recovered = [], []
        with self._lock:
            for bin_id, seen in self._last_seen.items():
                is_offline = (now - seen) > timeout
                was_offline = bin_id in self._offline
                if is_offline and not was_offline:
                    self._offline.add(bin_id)
                    newly_offline.append(bin_id)
                elif not is_offline and was_offline:
                    self._offline.discard(bin_id)
                    recovered.append(bin_id)
        return newly_offline, recovered

    # ── Cho REST API (SV3) đọc ────────────────────────────────────────────

    def snapshot(self, bin_id: str) -> dict:
        """Snapshot trạng thái một thùng (telemetry + actuator + cờ offline)."""
        with self._lock:
            return {
                "bin_id":   bin_id,
                "telemetry": self.telemetry.get(bin_id),
                "actuator":  self.actuator.get(bin_id),
                "offline":   bin_id in self._offline,
                "needs_collection": bin_id in self._collection,
            }

    def known_bins(self) -> list:
        with self._lock:
            return sorted(set(self.telemetry) | set(self.actuator))
