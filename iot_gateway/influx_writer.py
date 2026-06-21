"""
InfluxDB Writer — Edge Waste Gateway (Đề tài 5, IT6130)
=========================================================
Vai trò trong hệ thống (SV2):
  Ghi 3 loại dữ liệu vào InfluxDB time-series (§5.11) để Grafana (SV3) vẽ:
    - bin_telemetry   tag: area_id, bin_id ; field: fill_level, weight_kg,
                                                     methane_ppm, temperature
    - gateway_events  tag: bin_id, event_type, severity ; field: value, threshold
    - actuator_status tag: bin_id ; field: lock, compactor, buzzer, dispatch (0/1)

Nguyên tắc thiết kế:
  Lỗi ghi InfluxDB KHÔNG ĐƯỢC làm sập gateway. Vòng đời edge (rule engine,
  điều khiển actuator) phải tiếp tục ngay cả khi DB tạm chết. Vì vậy:
    - Khởi tạo client trong try/except → nếu InfluxDB chưa sẵn sàng, gateway
      vẫn chạy ở chế độ "không ghi" và log cảnh báo.
    - Mỗi lần ghi bọc try/except riêng, chỉ log lỗi rồi đi tiếp.
"""


def _bin01(value) -> int:
    """Mã hóa trạng thái actuator 'on'/'off' → 1/0 để lưu dạng số (Grafana vẽ được)."""
    return 1 if value == "on" else 0


class InfluxWriter:
    def __init__(self, url: str, token: str, org: str, bucket: str):
        self.enabled = False
        self._bucket = bucket
        self._org = org
        try:
            from influxdb_client import InfluxDBClient, Point
            from influxdb_client.client.write_api import SYNCHRONOUS

            self._Point = Point
            self._client = InfluxDBClient(url=url, token=token, org=org)
            self._write_api = self._client.write_api(write_options=SYNCHRONOUS)
            self.enabled = True
            print(f"[gateway] InfluxDB kết nối: {url} (org={org}, bucket={bucket})")
        except Exception as e:
            print(f"[gateway] InfluxDB chưa sẵn sàng ({e}) — chạy chế độ KHÔNG GHI, "
                  f"gateway vẫn hoạt động bình thường")

    def _safe_write(self, point):
        if not self.enabled:
            return
        try:
            self._write_api.write(bucket=self._bucket, org=self._org, record=point)
        except Exception as e:
            print(f"[gateway] Lỗi ghi InfluxDB (bỏ qua): {e}")

    def write_telemetry(self, t: dict):
        """measurement bin_telemetry — số liệu thô đã normalize của thùng."""
        if not self.enabled:
            return
        p = (
            self._Point("bin_telemetry")
            .tag("area_id", t.get("area_id", "unknown"))
            .tag("bin_id", t.get("bin_id", "unknown"))
            .field("fill_level", float(t.get("fill_level", 0.0)))
            .field("weight_kg", float(t.get("weight_kg", 0.0)))
            .field("methane_ppm", float(t.get("methane_ppm", 0.0)))
            .field("temperature", float(t.get("temperature", 0.0)))
        )
        self._safe_write(p)

    def write_event(self, bin_id: str, event_type: str, ev: dict):
        """measurement gateway_events — mỗi sự kiện rule engine phát ra (cạnh lên)."""
        if not self.enabled:
            return
        p = (
            self._Point("gateway_events")
            .tag("bin_id", bin_id)
            .tag("event_type", event_type)
            .tag("severity", ev.get("severity", "info"))
            .field("value", float(ev.get("value", 0)))
            .field("threshold", float(ev.get("threshold", 0)))
        )
        self._safe_write(p)

    def write_actuator_status(self, bin_id: str, status: dict):
        """measurement actuator_status — trạng thái 4 thiết bị (mã hóa 0/1)."""
        if not self.enabled:
            return
        # Bỏ qua payload error (không đủ 4 field trạng thái).
        if not all(k in status for k in ("lock", "compactor", "buzzer", "dispatch")):
            return
        p = (
            self._Point("actuator_status")
            .tag("bin_id", bin_id)
            .field("lock", _bin01(status["lock"]))
            .field("compactor", _bin01(status["compactor"]))
            .field("buzzer", _bin01(status["buzzer"]))
            .field("dispatch", _bin01(status["dispatch"]))
        )
        self._safe_write(p)

    def close(self):
        if self.enabled:
            try:
                self._client.close()
            except Exception:
                pass
