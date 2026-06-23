"""
ThingsBoard Gateway Bridge (SV3)
================================
Kết nối local MQTT broker với ThingsBoard Cloud qua ThingsBoard Gateway MQTT API.
- Subscribe waste/+/gateway/normalized → push telemetry lên TB cho từng bin (sub-device).
- Subscribe v1/gateway/rpc từ TB → chuyển thành command gửi xuống local actuator.
"""
import json
import os
import signal
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


# ── Hàm thuần (pure functions) ────────────────────────────────────────────────
# Tách riêng logic chuyển đổi dữ liệu khỏi I/O (MQTT publish) để unit test được
# độc lập, không cần broker/ThingsBoard thật — cùng triết lý với rule_engine.py
# của SV2 (xem test_tb_gateway.py).

RPC_METHOD_MAP = {
    "setLock": "lock",
    "setCompactor": "compactor",
    "setBuzzer": "buzzer",
    "setDispatch": "dispatch",
}

SHARED_ATTR_MAP = {
    "fill_dispatch": "FILL_DISPATCH_THRESHOLD",
    "fill_critical": "FILL_CRITICAL_THRESHOLD",
    "temp_fire": "TEMP_FIRE_THRESHOLD",
    "methane_alert": "METHANE_ALERT_THRESHOLD",
    "weight_lock": "WEIGHT_LOCK_THRESHOLD",
}


def rpc_action_from_params(params) -> str:
    """Map RPC params (bool/str/dict/None) → action 'on'/'off'."""
    if isinstance(params, bool):
        return "on" if params else "off"
    if isinstance(params, str):
        return params
    return "on" if params else "off"


def rpc_to_command(bin_id: str, method: str, params) -> dict | None:
    """
    Chuyển RPC ThingsBoard (method/params) thành command MQTT cục bộ.
    Trả về None nếu method không nằm trong RPC_METHOD_MAP (unknown method).
    """
    target = RPC_METHOD_MAP.get(method)
    if not target:
        return None
    return {
        "bin_id": bin_id,
        "target": target,
        "action": rpc_action_from_params(params),
        "reason": "thingsboard_rpc",
        "timestamp": _now_iso(),
    }


def build_telemetry_values(data: dict) -> dict:
    """Trích field telemetry cần đẩy lên ThingsBoard từ bản ghi normalized."""
    return {
        "fill_level": data.get("fill_level", 0),
        "weight_kg": data.get("weight_kg", 0),
        "methane_ppm": data.get("methane_ppm", 0),
        "temperature": data.get("temperature", 0),
        "fill_status": data.get("fill_status", "low"),
    }


def build_alarm_values(event_data: dict) -> dict:
    """Trích field event → alarm telemetry để ThingsBoard tạo Alarm rule."""
    return {
        "alarm_type": event_data.get("event_type", "unknown"),
        "alarm_severity": event_data.get("severity", "info"),
        "alarm_value": event_data.get("value", 0),
        "alarm_threshold": event_data.get("threshold", 0),
        "alarm_action": event_data.get("action_taken", ""),
    }


def build_gateway_telemetry(bin_id: str, values: dict, ts_ms: int) -> dict:
    """Bọc values theo format ThingsBoard Gateway API: {device: [{ts, values}]}."""
    return {bin_id: [{"ts": ts_ms, "values": values}]}


def map_shared_attributes(payload: dict) -> dict:
    """
    Trích threshold từ shared attributes ThingsBoard → dict {ENV_KEY: value}.
    payload có thể là {"shared": {...}} (push notification) hoặc {...} thẳng
    (response của attributes/request). Bỏ qua key không nằm trong SHARED_ATTR_MAP.
    """
    shared = payload.get("shared", payload)
    thresholds = {}
    for attr_key, env_key in SHARED_ATTR_MAP.items():
        if attr_key in shared:
            try:
                thresholds[env_key] = float(shared[attr_key])
            except (TypeError, ValueError):
                continue
    return thresholds


# ── ThingsBoard MQTT Client ──────────────────────────────────────────────────

def tb_on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[tb-gateway] Connected to ThingsBoard Cloud")
        client.subscribe("v1/gateway/rpc", qos=1)
        client.subscribe("v1/gateway/attributes", qos=1)         # push update của sub-device
        client.subscribe("v1/devices/me/attributes", qos=1)      # push update của gateway-device chính nó
        client.subscribe("v1/devices/me/attributes/response/+", qos=1)
        # Ngưỡng cấu hình (fill_dispatch, temp_fire, ...) là Shared Attributes
        # của CHÍNH device gateway (waste-gateway), không phải của sub-device
        # nào — nên phải dùng API "v1/devices/me/..." (request/response của
        # device đang connect), KHÔNG phải "v1/gateway/attributes/request"
        # (API đó dành cho xin attributes của một sub-device cụ thể, yêu cầu
        # payload {"id":..,"device":..,"keys":[...]}; gửi sai format như cũ
        # khiến ThingsBoard ngắt kết nối ngay sau mỗi lần connect — xem
        # SV3-Tasks.md mục bug "tb-gateway flapping disconnect rc=7").
        client.publish("v1/devices/me/attributes/request/1", json.dumps({
            "sharedKeys": "fill_dispatch,fill_critical,temp_fire,methane_alert,weight_lock"
        }))
    else:
        print(f"[tb-gateway] TB connection failed, rc={rc}")


def tb_on_message(client, userdata, msg):
    """Handle RPC and shared attributes from ThingsBoard."""
    try:
        payload = json.loads(msg.payload.decode())

        if msg.topic.startswith("v1/gateway/attributes") or msg.topic.startswith("v1/devices/me/attributes"):
            _handle_shared_attributes(userdata, payload)
            return

        # RPC handling
        device = payload.get("device", "")
        data = payload.get("data", {})
        rpc_id = data.get("id", 0)
        method = data.get("method", "")
        params = data.get("params", {})

        command = rpc_to_command(device, method, params)
        if command is None:
            print(f"[tb-gateway] Unknown RPC method: {method}")
            reply = {"device": device, "id": rpc_id, "data": {"success": False, "error": "unknown_method"}}
            client.publish("v1/gateway/rpc", json.dumps(reply))
            return

        bin_id = command["bin_id"]
        local_client = userdata["local_client"]
        local_client.publish(f"waste/{bin_id}/actuator/command", json.dumps(command), qos=1)
        print(f"[tb-gateway] RPC {method}({params}) → {bin_id}/{command['target']}={command['action']}")

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
        telemetry = build_gateway_telemetry(bin_id, build_telemetry_values(data), ts)
        tb_client.publish("v1/gateway/telemetry", json.dumps(telemetry))

    except Exception as e:
        print(f"[tb-gateway] Forward error: {e}")


def _forward_event_as_alarm(tb_client, bin_id, event_data):
    """Push gateway events as telemetry attributes to trigger TB alarms."""
    ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    alarm_telemetry = build_gateway_telemetry(bin_id, build_alarm_values(event_data), ts)
    tb_client.publish("v1/gateway/telemetry", json.dumps(alarm_telemetry))
    print(f"[tb-gateway] ALARM {bin_id}: {event_data.get('event_type')} ({event_data.get('severity')})")


def _handle_shared_attributes(userdata, payload):
    """
    Receive shared attributes from ThingsBoard (threshold config).
    Forward as config update to local gateway via MQTT retained message
    (waste/gateway/config) — gateway.py subscribe và áp dụng runtime (§5.17.3).
    """
    local_client = userdata["local_client"]
    thresholds = map_shared_attributes(payload)

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

    # `docker stop`/compose recreate gửi SIGTERM, không phải SIGINT — nếu chỉ
    # bắt KeyboardInterrupt thì SIGTERM sẽ kill tiến trình ngay, không gửi
    # MQTT DISCONNECT sạch. ThingsBoard giữ session "ma" tới hết keepalive,
    # khiến lần kết nối kế tiếp bị kick liên tục (flapping rc=7 đã gặp khi
    # debug). Raise KeyboardInterrupt từ signal handler để tái dùng đúng
    # nhánh dọn dẹp try/except/finally bên dưới.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

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
