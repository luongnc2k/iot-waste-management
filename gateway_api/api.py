import json
import os
import time
from datetime import datetime, timezone

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
    fill_dispatch: float = None
    fill_critical: float = None
    temp_fire: float = None
    methane_alert: float = None
    weight_lock: float = None


@app.get("/health")
def health():
    return {"status": "ok", "service": "gateway-api", "bins": BINS}


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
    query = f'''
    from(bucket: "{INFLUXDB_BUCKET}")
      |> range(start: -5m)
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
