"""
ThingsBoard Gateway Bridge (SV3)
================================
Kết nối local MQTT broker với ThingsBoard Cloud qua ThingsBoard Gateway MQTT API.
- Subscribe waste/+/gateway/normalized → push telemetry lên TB cho từng bin (sub-device).
- Subscribe v1/gateway/rpc từ TB → chuyển thành command gửi xuống local actuator.
"""
import json
import os
import time
import threading
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

LOCAL_BROKER = os.getenv("MQTT_BROKER", "mosquitto")
LOCAL_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")

TB_HOST = os.getenv("TB_HOST", "thingsboard.cloud")
TB_PORT = int(os.getenv("TB_PORT", "1883"))
TB_GATEWAY_TOKEN = os.getenv("TB_GATEWAY_TOKEN", "")

BINS = os.getenv("BIN_IDS", "bin-01,bin-02,bin-03").split(",")

connected_devices = set()
_last_telemetry_push = {}
TB_PUSH_INTERVAL = float(os.getenv("TB_PUSH_INTERVAL", "10"))


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── ThingsBoard MQTT Client ──────────────────────────────────────────────────

def tb_on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[tb-gateway] Connected to ThingsBoard Cloud")
        client.subscribe("v1/gateway/rpc", qos=1)
        client.subscribe("v1/gateway/attributes", qos=1)
        # Request current shared attributes on connect
        client.publish("v1/gateway/attributes/request", json.dumps({"sharedKeys": "fill_dispatch,fill_critical,temp_fire,methane_alert,weight_lock"}))
    else:
        print(f"[tb-gateway] TB connection failed, rc={rc}")


def tb_on_message(client, userdata, msg):
    """Handle RPC and shared attributes from ThingsBoard."""
    try:
        payload = json.loads(msg.payload.decode())

        if "v1/gateway/attributes" in msg.topic:
            _handle_shared_attributes(userdata, payload)
            return

        # RPC handling
        device = payload.get("device", "")
        data = payload.get("data", {})
        rpc_id = data.get("id", 0)
        method = data.get("method", "")
        params = data.get("params", {})

        method_map = {
            "setLock": "lock",
            "setCompactor": "compactor",
            "setBuzzer": "buzzer",
            "setDispatch": "dispatch",
        }

        target = method_map.get(method)
        if not target:
            print(f"[tb-gateway] Unknown RPC method: {method}")
            reply = {"device": device, "id": rpc_id, "data": {"success": False, "error": "unknown_method"}}
            client.publish("v1/gateway/rpc", json.dumps(reply))
            return

        action = "on" if params else "off"
        if isinstance(params, bool):
            action = "on" if params else "off"
        elif isinstance(params, str):
            action = params

        bin_id = device
        command = {
            "bin_id": bin_id,
            "target": target,
            "action": action,
            "reason": "thingsboard_rpc",
            "timestamp": _now_iso(),
        }
        local_client = userdata["local_client"]
        local_client.publish(f"waste/{bin_id}/actuator/command", json.dumps(command), qos=1)
        print(f"[tb-gateway] RPC {method}({params}) → {bin_id}/{target}={action}")

        reply = {"device": device, "id": rpc_id, "data": {"success": True}}
        client.publish("v1/gateway/rpc", json.dumps(reply))

    except Exception as e:
        print(f"[tb-gateway] RPC error: {e}")


# ── Local MQTT Client ─────────────────────────────────────────────────────────

def local_on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[tb-gateway] Connected to local MQTT broker")
        client.subscribe("waste/+/gateway/normalized", qos=1)
        client.subscribe("waste/+/gateway/event", qos=1)
    else:
        print(f"[tb-gateway] Local MQTT failed, rc={rc}")


def local_on_message(client, userdata, msg):
    """Receive normalized telemetry or events → push to ThingsBoard."""
    try:
        data = json.loads(msg.payload.decode())
        bin_id = data.get("bin_id", "")
        if not bin_id:
            return

        tb_client = userdata["tb_client"]

        if bin_id not in connected_devices:
            connect_payload = json.dumps({"device": bin_id})
            tb_client.publish("v1/gateway/connect", connect_payload)
            connected_devices.add(bin_id)
            print(f"[tb-gateway] Connected device: {bin_id}")

        if msg.topic.endswith("/gateway/event"):
            _forward_event_as_alarm(tb_client, bin_id, data)
            return

        now = time.time()
        if bin_id in _last_telemetry_push and (now - _last_telemetry_push[bin_id]) < TB_PUSH_INTERVAL:
            return
        _last_telemetry_push[bin_id] = now

        ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        telemetry = {
            bin_id: [{"ts": ts, "values": {
                "fill_level": data.get("fill_level", 0),
                "weight_kg": data.get("weight_kg", 0),
                "methane_ppm": data.get("methane_ppm", 0),
                "temperature": data.get("temperature", 0),
                "fill_status": data.get("fill_status", "low"),
            }}]
        }
        tb_client.publish("v1/gateway/telemetry", json.dumps(telemetry))

    except Exception as e:
        print(f"[tb-gateway] Forward error: {e}")


def _forward_event_as_alarm(tb_client, bin_id, event_data):
    """Push gateway events as telemetry attributes to trigger TB alarms."""
    ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    alarm_telemetry = {
        bin_id: [{"ts": ts, "values": {
            "alarm_type": event_data.get("event_type", "unknown"),
            "alarm_severity": event_data.get("severity", "info"),
            "alarm_value": event_data.get("value", 0),
            "alarm_threshold": event_data.get("threshold", 0),
            "alarm_action": event_data.get("action_taken", ""),
        }}]
    }
    tb_client.publish("v1/gateway/telemetry", json.dumps(alarm_telemetry))
    print(f"[tb-gateway] ALARM {bin_id}: {event_data.get('event_type')} ({event_data.get('severity')})")


def _handle_shared_attributes(userdata, payload):
    """
    Receive shared attributes from ThingsBoard (threshold config).
    Forward as config update to local gateway via MQTT retained message.
    """
    local_client = userdata["local_client"]
    thresholds = {}
    attr_map = {
        "fill_dispatch": "FILL_DISPATCH_THRESHOLD",
        "fill_critical": "FILL_CRITICAL_THRESHOLD",
        "temp_fire": "TEMP_FIRE_THRESHOLD",
        "methane_alert": "METHANE_ALERT_THRESHOLD",
        "weight_lock": "WEIGHT_LOCK_THRESHOLD",
    }
    shared = payload.get("shared", payload)
    for attr_key, env_key in attr_map.items():
        if attr_key in shared:
            thresholds[env_key] = float(shared[attr_key])

    if thresholds:
        config_msg = {"thresholds": thresholds, "source": "thingsboard_shared_attributes",
                      "timestamp": _now_iso()}
        local_client.publish("waste/gateway/config", json.dumps(config_msg), qos=1, retain=True)
        print(f"[tb-gateway] Shared attributes → config update: {thresholds}")


def main():
    if not TB_GATEWAY_TOKEN:
        print("[tb-gateway] ERROR: TB_GATEWAY_TOKEN not set. Exiting.")
        return

    # ThingsBoard client
    tb_client = mqtt.Client(client_id="tb-gateway-cloud")
    tb_client.username_pw_set(TB_GATEWAY_TOKEN)
    tb_client.on_connect = tb_on_connect
    tb_client.on_message = tb_on_message
    tb_client.on_disconnect = lambda c, u, rc: print(f"[tb-gateway] TB disconnected (rc={rc}), auto-reconnecting...")
    tb_client.reconnect_delay_set(min_delay=1, max_delay=30)

    # Local client
    local_client = mqtt.Client(client_id="tb-gateway-local")
    local_client.on_connect = local_on_connect
    local_client.on_message = local_on_message
    local_client.on_disconnect = lambda c, u, rc: print(f"[tb-gateway] Local MQTT disconnected (rc={rc}), auto-reconnecting...")
    local_client.reconnect_delay_set(min_delay=1, max_delay=10)
    local_client = mqtt.Client(client_id="tb-gateway-local")
    local_client.on_connect = local_on_connect
    local_client.on_message = local_on_message

    # Cross-reference
    tb_client.user_data_set({"local_client": local_client})
    local_client.user_data_set({"tb_client": tb_client})

    # Connect local
    print(f"[tb-gateway] Connecting to local broker {LOCAL_BROKER}:{LOCAL_PORT}")
    if MQTT_USER:
        local_client.username_pw_set(MQTT_USER, MQTT_PASS)
    while True:
        try:
            local_client.connect(LOCAL_BROKER, LOCAL_PORT, keepalive=60)
            break
        except Exception as e:
            print(f"[tb-gateway] Local connect failed: {e}, retrying...")
            time.sleep(5)

    # Connect ThingsBoard
    print(f"[tb-gateway] Connecting to ThingsBoard {TB_HOST}:{TB_PORT}")
    while True:
        try:
            tb_client.connect(TB_HOST, TB_PORT, keepalive=60)
            break
        except Exception as e:
            print(f"[tb-gateway] TB connect failed: {e}, retrying...")
            time.sleep(5)

    local_client.loop_start()
    tb_client.loop_start()

    print("[tb-gateway] Running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[tb-gateway] Shutting down...")
    finally:
        local_client.loop_stop()
        tb_client.loop_stop()
        local_client.disconnect()
        tb_client.disconnect()


if __name__ == "__main__":
    main()
