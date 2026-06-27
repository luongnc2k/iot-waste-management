import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from influxdb_client import InfluxDBClient

MQTT_BROKER = os.getenv("MQTT_BROKER", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")

INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://influxdb:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "dev-token-change-me")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "hust")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "iot")

BINS = os.getenv("BIN_IDS", "bin-01,bin-02,bin-03").split(",")


def _connect_mqtt() -> mqtt.Client:
    """Retry loop khi kết nối broker — cùng pattern với sensor/actuator/gateway,
    tránh container crash-loop nếu mosquitto chưa sẵn sàng khi gateway-api start."""
    client = mqtt.Client(client_id="gateway-api")
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    while True:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            return client
        except Exception as e:
            print(f"[gateway-api] Chưa kết nối được MQTT broker: {e} — thử lại sau 5s")
            time.sleep(5)


app = FastAPI(title="Waste Management Gateway API", version="1.0.0")

mqtt_client = _connect_mqtt()
mqtt_client.loop_start()

influx_client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
query_api = influx_client.query_api()


class CommandRequest(BaseModel):
    target: str
    action: str
    reason: str = "manual_api"


class ThresholdConfig(BaseModel):
    # Optional[float] (không phải "float = None") — Pydantic v2 coi "float = None"
    # là field bắt buộc kiểu float với default None, nên client gửi tường minh
    # {"fill_critical": null} sẽ bị 422 validation error thay vì được bỏ qua.
    fill_dispatch: Optional[float] = None
    fill_critical: Optional[float] = None
    temp_fire: Optional[float] = None
    methane_alert: Optional[float] = None
    weight_lock: Optional[float] = None


@app.get("/health")
def health():
    return {"status": "ok", "service": "gateway-api", "bins": BINS}


@app.get("/collection/route")
def collection_route():
    """
    Danh sách thùng cần thu gom ngay bây giờ (đề bài §5.9 REST API).

    gateway.py (SV2) đã có sẵn collection_route() trong state_store.py, nhưng
    gateway-api chạy ở container riêng, không chia sẻ bộ nhớ với gateway nên
    không gọi trực tiếp được. Endpoint này suy ra danh sách từ fill_level mới
    nhất trên InfluxDB (đúng cách Grafana panel "Thùng cần thu gom hiện tại"
    đã làm) — không cần thêm cơ chế chia sẻ trạng thái mới giữa hai container.
    Sắp xếp giảm dần theo fill_level để gợi ý thứ tự thu gom (đầy nhất trước).
    """
    threshold = float(os.getenv("FILL_DISPATCH_THRESHOLD", "85"))
    due = []
    for bin_id in BINS:
        telemetry = _get_latest_telemetry(bin_id)
        fill_level = telemetry.get("fill_level")
        if fill_level is not None and fill_level > threshold:
            due.append({"bin_id": bin_id, "fill_level": fill_level})
    due.sort(key=lambda b: b["fill_level"], reverse=True)
    return {"threshold": threshold, "bins_due_for_collection": due}


@app.get("/bins")
def list_bins():
    results = []
    for bin_id in BINS:
        state = _get_latest_telemetry(bin_id)
        results.append(state)
    return {"bins": results}


@app.get("/bins/{bin_id}/state")
def get_bin_state(bin_id: str):
    if bin_id not in BINS:
        raise HTTPException(status_code=404, detail=f"Bin {bin_id} not found")
    telemetry = _get_latest_telemetry(bin_id)
    actuator = _get_latest_actuator(bin_id)
    return {"telemetry": telemetry, "actuator": actuator}


@app.get("/bins/{bin_id}/events")
def get_bin_events(bin_id: str, limit: int = 20):
    if bin_id not in BINS:
        raise HTTPException(status_code=404, detail=f"Bin {bin_id} not found")
    query = f'''
    from(bucket: "{INFLUXDB_BUCKET}")
      |> range(start: -1h)
      |> filter(fn: (r) => r._measurement == "gateway_events")
      |> filter(fn: (r) => r.bin_id == "{bin_id}")
      |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> sort(columns: ["_time"], desc: true)
      |> limit(n: {limit})
    '''
    try:
        tables = query_api.query(query, org=INFLUXDB_ORG)
        events = []
        for table in tables:
            for record in table.records:
                events.append({
                    "timestamp": record.get_time().isoformat(),
                    "event_type": record.values.get("event_type", ""),
                    "severity": record.values.get("severity", ""),
                    "value": record.values.get("value", 0),
                    "threshold": record.values.get("threshold", 0),
                })
        return {"bin_id": bin_id, "events": events}
    except Exception as e:
        return {"bin_id": bin_id, "events": [], "error": str(e)}


@app.post("/bins/{bin_id}/command")
def send_command(bin_id: str, cmd: CommandRequest):
    if bin_id not in BINS:
        raise HTTPException(status_code=404, detail=f"Bin {bin_id} not found")

    valid_targets = ["lock", "compactor", "buzzer", "dispatch"]
    if cmd.target not in valid_targets:
        raise HTTPException(status_code=400, detail=f"Invalid target. Must be one of: {valid_targets}")
    if cmd.action not in ("on", "off"):
        raise HTTPException(status_code=400, detail="Action must be 'on' or 'off'")

    command = {
        "bin_id": bin_id,
        "target": cmd.target,
        "action": cmd.action,
        "reason": cmd.reason,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    topic = f"waste/{bin_id}/actuator/command"
    mqtt_client.publish(topic, json.dumps(command), qos=1)
    return {"status": "command_sent", "command": command}


@app.get("/config")
def get_config():
    """Get current rule engine thresholds (from environment)."""
    return {
        "fill_dispatch": float(os.getenv("FILL_DISPATCH_THRESHOLD", "85")),
        "fill_critical": float(os.getenv("FILL_CRITICAL_THRESHOLD", "95")),
        "temp_fire": float(os.getenv("TEMP_FIRE_THRESHOLD", "60")),
        "methane_alert": float(os.getenv("METHANE_ALERT_THRESHOLD", "500")),
        "weight_lock": float(os.getenv("WEIGHT_LOCK_THRESHOLD", "60")),
    }


@app.post("/config")
def update_config(cfg: ThresholdConfig):
    """
    Update rule engine thresholds at runtime.
    Publishes new thresholds to a config topic that gateway can subscribe.
    """
    updates = {}
    if cfg.fill_dispatch is not None:
        updates["FILL_DISPATCH_THRESHOLD"] = cfg.fill_dispatch
    if cfg.fill_critical is not None:
        updates["FILL_CRITICAL_THRESHOLD"] = cfg.fill_critical
    if cfg.temp_fire is not None:
        updates["TEMP_FIRE_THRESHOLD"] = cfg.temp_fire
    if cfg.methane_alert is not None:
        updates["METHANE_ALERT_THRESHOLD"] = cfg.methane_alert
    if cfg.weight_lock is not None:
        updates["WEIGHT_LOCK_THRESHOLD"] = cfg.weight_lock

    if not updates:
        raise HTTPException(status_code=400, detail="No thresholds provided")

    config_msg = {"thresholds": updates, "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    mqtt_client.publish("waste/gateway/config", json.dumps(config_msg), qos=1, retain=True)
    return {"status": "config_published", "updates": updates}


@app.get("/summary")
def system_summary():
    """
    Tổng quan hệ thống tức thì: số thùng đang online, số cần thu gom,
    số đang báo động, và trạng thái tóm tắt từng bin.
    Hữu ích cho dashboard overview hoặc màn hình giám sát trung tâm.
    """
    fill_threshold = float(os.getenv("FILL_DISPATCH_THRESHOLD", "85"))
    fill_critical = float(os.getenv("FILL_CRITICAL_THRESHOLD", "95"))
    offline_timeout = int(os.getenv("SENSOR_OFFLINE_TIMEOUT", "30"))

    now = datetime.now(timezone.utc)
    bins_summary = []
    total_online = 0
    total_due = 0
    total_critical = 0

    for bin_id in BINS:
        t = _get_latest_telemetry(bin_id)
        a = _get_latest_actuator(bin_id)

        if "fill_level" in t:
            ts = datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00"))
            age_s = (now - ts).total_seconds()
            is_online = age_s <= offline_timeout
        else:
            is_online = False
            age_s = None

        fill = t.get("fill_level")
        is_due = fill is not None and fill > fill_threshold
        is_crit = fill is not None and fill > fill_critical

        if is_online:
            total_online += 1
        if is_due:
            total_due += 1
        if is_crit:
            total_critical += 1

        bins_summary.append({
            "bin_id": bin_id,
            "online": is_online,
            "fill_level": fill,
            "due_for_collection": is_due,
            "critical": is_crit,
            "actuator_lock": a.get("lock"),
            "data_age_seconds": round(age_s, 1) if age_s is not None else None,
        })

    return {
        "total_bins": len(BINS),
        "online": total_online,
        "offline": len(BINS) - total_online,
        "due_for_collection": total_due,
        "critical": total_critical,
        "bins": bins_summary,
        "timestamp": now.isoformat(),
    }


@app.get("/bins/offline")
def get_offline_bins():
    """
    Danh sách thùng chưa gửi telemetry trong SENSOR_OFFLINE_TIMEOUT giây.
    Sensor_offline detection theo §5.17 nâng cao.
    """
    offline_timeout = int(os.getenv("SENSOR_OFFLINE_TIMEOUT", "30"))
    now = datetime.now(timezone.utc)
    offline = []
    online = []

    for bin_id in BINS:
        t = _get_latest_telemetry(bin_id)
        if "fill_level" not in t:
            offline.append({"bin_id": bin_id, "last_seen": None, "age_seconds": None})
            continue

        ts = datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00"))
        age_s = (now - ts).total_seconds()
        entry = {"bin_id": bin_id, "last_seen": ts.isoformat(), "age_seconds": round(age_s, 1)}
        if age_s > offline_timeout:
            offline.append(entry)
        else:
            online.append(entry)

    return {
        "offline_timeout_seconds": offline_timeout,
        "offline": offline,
        "online": online,
    }


@app.get("/bins/{bin_id}/eta")
def get_bin_eta(bin_id: str):
    """
    Dự báo thời điểm thùng đầy dựa trên tốc độ tăng fill_level trong 15 phút
    gần nhất (§5.17 nâng cao #6). Dùng hồi quy tuyến tính đơn giản (least-
    squares slope) trên chuỗi thời gian — đủ chính xác cho bài toán mô phỏng,
    không cần thư viện ML nặng.

    Trả về:
    - eta_minutes: số phút ước tính đến khi thùng đầy (null nếu không tính được)
    - eta_timestamp: thời điểm dự kiến thùng đầy (ISO 8601 UTC)
    - fill_rate_per_minute: tốc độ tăng (% / phút), âm = đang giảm
    - confidence: "high" (>=5 điểm), "low" (<5), "unavailable"
    """
    if bin_id not in BINS:
        raise HTTPException(status_code=404, detail=f"Bin {bin_id} not found")

    query = f'''
    from(bucket: "{INFLUXDB_BUCKET}")
      |> range(start: -15m)
      |> filter(fn: (r) => r._measurement == "bin_telemetry")
      |> filter(fn: (r) => r.bin_id == "{bin_id}")
      |> filter(fn: (r) => r._field == "fill_level")
      |> sort(columns: ["_time"])
    '''
    try:
        tables = query_api.query(query, org=INFLUXDB_ORG)
        points = []
        for table in tables:
            for record in table.records:
                ts_epoch = record.get_time().timestamp()
                val = record.get_value()
                if val is not None:
                    points.append((ts_epoch, float(val)))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"InfluxDB query failed: {e}")

    if len(points) < 2:
        return {
            "bin_id": bin_id,
            "eta_minutes": None,
            "eta_timestamp": None,
            "fill_rate_per_minute": None,
            "current_fill": points[0][1] if points else None,
            "confidence": "unavailable",
            "note": "Không đủ dữ liệu để tính xu hướng (cần ít nhất 2 điểm trong 15 phút)",
        }

    # Least-squares linear regression: y = a*x + b, tính slope a
    n = len(points)
    t0 = points[0][0]
    xs = [p[0] - t0 for p in points]
    ys = [p[1] for p in points]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    ss_xx = sum((x - mean_x) ** 2 for x in xs)

    slope_per_sec = ss_xy / ss_xx if ss_xx > 0 else 0.0
    fill_rate_per_minute = round(slope_per_sec * 60, 4)
    current_fill = ys[-1]

    confidence = "high" if n >= 5 else "low"

    if slope_per_sec <= 0:
        return {
            "bin_id": bin_id,
            "eta_minutes": None,
            "eta_timestamp": None,
            "fill_rate_per_minute": fill_rate_per_minute,
            "current_fill": current_fill,
            "confidence": confidence,
            "note": "Thùng không tăng mức đầy (ổn định hoặc đang giảm)",
        }

    seconds_to_full = (100.0 - current_fill) / slope_per_sec
    eta_dt = datetime.fromtimestamp(points[-1][0], tz=timezone.utc) + timedelta(seconds=seconds_to_full)
    eta_minutes = round(seconds_to_full / 60, 1)

    return {
        "bin_id": bin_id,
        "eta_minutes": eta_minutes,
        "eta_timestamp": eta_dt.isoformat(),
        "fill_rate_per_minute": fill_rate_per_minute,
        "current_fill": current_fill,
        "confidence": confidence,
    }


def _get_latest_telemetry(bin_id: str) -> dict:
    query = f'''
    from(bucket: "{INFLUXDB_BUCKET}")
      |> range(start: -5m)
      |> filter(fn: (r) => r._measurement == "bin_telemetry")
      |> filter(fn: (r) => r.bin_id == "{bin_id}")
      |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> sort(columns: ["_time"], desc: true)
      |> limit(n: 1)
    '''
    try:
        tables = query_api.query(query, org=INFLUXDB_ORG)
        for table in tables:
            for record in table.records:
                return {
                    "bin_id": bin_id,
                    "fill_level": record.values.get("fill_level", 0),
                    "weight_kg": record.values.get("weight_kg", 0),
                    "methane_ppm": record.values.get("methane_ppm", 0),
                    "temperature": record.values.get("temperature", 0),
                    "timestamp": record.get_time().isoformat(),
                }
    except Exception:
        pass
    return {"bin_id": bin_id, "status": "no_data"}


def _get_latest_actuator(bin_id: str) -> dict:
    # Range rộng hơn _get_latest_telemetry có chủ đích: sensor publish định kỳ
    # mỗi 5-7s nên cửa sổ 5 phút luôn có dữ liệu mới, nhưng actuator chỉ publish
    # status khi NHẬN LỆNH MỚI (event-driven). Nếu actuator đứng yên quá 5 phút
    # (ví dụ không có sự kiện nào kích hoạt), API sẽ báo sai "no_data" dù trạng
    # thái thật vẫn còn hiệu lực — bug phát hiện khi demo, actuator lock=on từ
    # 23 phút trước vẫn là trạng thái đúng nhưng bị query timeout bỏ qua.
    query = f'''
    from(bucket: "{INFLUXDB_BUCKET}")
      |> range(start: -24h)
      |> filter(fn: (r) => r._measurement == "actuator_status")
      |> filter(fn: (r) => r.bin_id == "{bin_id}")
      |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> sort(columns: ["_time"], desc: true)
      |> limit(n: 1)
    '''
    try:
        tables = query_api.query(query, org=INFLUXDB_ORG)
        for table in tables:
            for record in table.records:
                return {
                    "bin_id": bin_id,
                    "lock": "on" if record.values.get("lock", 0) else "off",
                    "compactor": "on" if record.values.get("compactor", 0) else "off",
                    "buzzer": "on" if record.values.get("buzzer", 0) else "off",
                    "dispatch": "on" if record.values.get("dispatch", 0) else "off",
                    "timestamp": record.get_time().isoformat(),
                }
    except Exception:
        pass
    return {"bin_id": bin_id, "status": "no_data"}
