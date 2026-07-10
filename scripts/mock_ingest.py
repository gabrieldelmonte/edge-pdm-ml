"""
Sanity-check script: sends a synthetic CSV payload through the full pipeline.

Usage:
    # Test middleware only (it will attempt to forward to the server):
    python mock_ingest.py --target middleware

    # Test the server inference endpoint directly (bypasses middleware):
    python mock_ingest.py --target server

    # Test the MQTT round trip: publish to pipeline/input as a mock sensor
    # would, then wait for the inference result on pipeline/result (this is
    # the "flows back to the sensor" path):
    python mock_ingest.py --target mqtt

    # Test middleware (HTTP) + server:
    python mock_ingest.py --target both

    # Test everything, including the MQTT round trip:
    python mock_ingest.py --target all

Requirements: pip install requests paho-mqtt
"""

import argparse
import hashlib
import json
import math
import sys
import time

import requests

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
MW_URL            = "http://localhost:8080/api/ingest"
SERVER_URL        = "http://localhost:8001/inference"
MQTT_HOST         = "localhost"
MQTT_PORT         = 1883
MQTT_INPUT_TOPIC  = "pipeline/input"
MQTT_RESULT_TOPIC = "pipeline/result"
MQTT_RESULT_TIMEOUT_S = 30

# ---------------------------------------------------------------------------
# Synthetic data generation
# ---------------------------------------------------------------------------
SAMPLE_RATE_HZ = 1344          # LIS3DH normal mode, matches common config
N_SAMPLES      = SAMPLE_RATE_HZ  # 1 second of data
SCALE          = 1000           # raw int16 amplitude

def generate_csv(n_samples: int, sample_rate_hz: int) -> str:
    """Return CSV string with n_samples rows of 'x,y,z\\n'.

    X: 50 Hz sine (bearing defect-like component)
    Y: 120 Hz sine (radial loading)
    Z: DC + small noise (axial)
    """
    rows = []
    for i in range(n_samples):
        t = i / sample_rate_hz
        x = int(SCALE * math.sin(2 * math.pi * 50  * t))
        y = int(SCALE * math.sin(2 * math.pi * 120 * t))
        z = int(SCALE * 0.1 * math.sin(2 * math.pi * 10 * t))
        rows.append(f"{x},{y},{z}")
    return "\n".join(rows) + "\n"


def sha256_of(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Middleware test (HTTP transport)
# ---------------------------------------------------------------------------
def test_middleware(file_id: str, csv_data: str, checksum: str) -> bool:
    payload = {
        "file_id":          file_id,
        "capture_sequence": 1,
        "sensor_id":        "mock_lis3dh",
        "csv_data":         csv_data,
        "checksum":         checksum,
        "current_ma":       0.0,
        "sample_rate_hz":   SAMPLE_RATE_HZ,
        "batch_index":      0,
        "is_last_batch":    True,
    }
    print(f"POST {MW_URL}")
    print(f"  file_id={file_id}  samples={csv_data.count(chr(10))}  checksum={checksum[:16]}...")
    t0 = time.perf_counter()
    try:
        r = requests.post(MW_URL, json=payload, timeout=30)
    except requests.ConnectionError:
        print(f"  ERROR: cannot connect to middleware at {MW_URL}")
        print("  Is 'cd src/pipeline/middleware && docker compose up -d' running?")
        return False
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"  HTTP {r.status_code}  ({elapsed_ms:.0f} ms round trip)")
    try:
        body = r.json()
        print(f"  Response: {json.dumps(body, indent=2)}")
    except Exception:
        print(f"  Response body (raw): {r.text[:500]}")
    return r.status_code == 200


# ---------------------------------------------------------------------------
# Server direct test
# ---------------------------------------------------------------------------
def test_server(file_id: str, csv_data: str, checksum: str) -> bool:
    payload = {
        "file_id":          file_id,
        "csv_data":         csv_data,
        "checksum":         checksum,
        "capture_sequence": 1,
        "sensor_id":        None,   # None = server picks first available model
        "current_ma":       0.0,
        "sample_rate_hz":   SAMPLE_RATE_HZ,
    }
    print(f"POST {SERVER_URL}")
    print(f"  file_id={file_id}  samples={csv_data.count(chr(10))}  checksum={checksum[:16]}...")
    t0 = time.perf_counter()
    try:
        r = requests.post(SERVER_URL, json=payload, timeout=30)
    except requests.ConnectionError:
        print(f"  ERROR: cannot connect to server at {SERVER_URL}")
        print("  Is 'cd src/pipeline/server && docker compose up -d' running?")
        return False
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"  HTTP {r.status_code}  ({elapsed_ms:.0f} ms round trip)")
    try:
        body = r.json()
        print(f"  Response: {json.dumps(body, indent=2)}")
    except Exception:
        print(f"  Response body (raw): {r.text[:500]}")
    return r.status_code == 200


# ---------------------------------------------------------------------------
# MQTT round-trip test: mock sensor -> middleware -> server -> middleware
#                        -> mock sensor (the "flows back to sensor" path)
# ---------------------------------------------------------------------------
def test_mqtt(file_id: str, csv_data: str, checksum: str, batch_size: int = 64) -> bool:
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("  ERROR: paho-mqtt not installed. Run: pip install paho-mqtt")
        return False

    result_holder: dict = {}

    def on_connect(client, userdata, flags, reason_code, properties=None):
        client.subscribe(MQTT_RESULT_TOPIC, qos=1)

    def on_message(client, userdata, msg):
        try:
            body = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return
        if body.get("file_id") == file_id:
            result_holder["body"] = body
            result_holder["received_at"] = time.perf_counter()

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message

    print(f"Connecting to MQTT broker {MQTT_HOST}:{MQTT_PORT}")
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    except (ConnectionRefusedError, OSError) as e:
        print(f"  ERROR: cannot connect to MQTT broker at {MQTT_HOST}:{MQTT_PORT} ({e})")
        print("  Is 'cd src/pipeline/middleware && docker compose up -d' running?")
        return False

    client.loop_start()
    time.sleep(0.5)  # give the subscribe a moment to land before we publish

    # Split into batch_size-row chunks to mirror the ESP32 firmware's
    # mqtt_batch_size behavior: a large capture is never sent as one giant
    # MQTT message, it is published as many small batches that the
    # middleware accumulates and assembles before forwarding to inference.
    lines = csv_data.splitlines()
    n_batches = max(1, (len(lines) + batch_size - 1) // batch_size)
    print(f"PUBLISH {MQTT_INPUT_TOPIC} ({n_batches} batches of up to {batch_size} rows)")
    print(f"  file_id={file_id}  samples={len(lines)}  checksum={checksum[:16]}...")

    t0 = time.perf_counter()
    for batch_index, start in enumerate(range(0, len(lines), batch_size)):
        batch_lines = lines[start:start + batch_size]
        is_last = (start + batch_size) >= len(lines)
        # Every row carries its own trailing newline (matches the ESP32
        # firmware's per-line "%d,%d,%d\n" format) so concatenating all
        # batches in order reconstructs csv_data exactly, byte for byte.
        payload = {
            "file_id":          file_id,
            "sensor_id":        "mock_lis3dh",
            "csv_data":         "".join(f"{l}\n" for l in batch_lines),
            "checksum":         checksum if is_last else "",
            "capture_sequence": 1,
            "current_ma":       0.0,
            "sample_rate_hz":   SAMPLE_RATE_HZ,
            "batch_index":      batch_index,
            "is_last_batch":    is_last,
        }
        client.publish(MQTT_INPUT_TOPIC, json.dumps(payload), qos=1)

    print(f"  Waiting up to {MQTT_RESULT_TIMEOUT_S}s on {MQTT_RESULT_TOPIC} for the result...")
    deadline = t0 + MQTT_RESULT_TIMEOUT_S
    while "body" not in result_holder and time.perf_counter() < deadline:
        time.sleep(0.1)

    client.loop_stop()
    client.disconnect()

    if "body" not in result_holder:
        print(f"  TIMEOUT: no result received on {MQTT_RESULT_TOPIC} within {MQTT_RESULT_TIMEOUT_S}s")
        return False

    elapsed_ms = (result_holder["received_at"] - t0) * 1000
    print(f"  Result received after {elapsed_ms:.0f} ms (publish -> result delivered)")
    print(f"  Response: {json.dumps(result_holder['body'], indent=2)}")
    return result_holder["body"].get("status") == "ok"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Mock ingest sanity check")
    parser.add_argument(
        "--target",
        choices=["middleware", "server", "mqtt", "both", "all"],
        default="both",
        help="Which path(s) to test (default: both = middleware + server)",
    )
    args = parser.parse_args()

    ts = int(time.time())
    csv_data = generate_csv(N_SAMPLES, SAMPLE_RATE_HZ)
    checksum = sha256_of(csv_data)

    print(f"Generated {N_SAMPLES} samples at {SAMPLE_RATE_HZ} Hz")
    print(f"CSV size: {len(csv_data)} bytes  SHA-256: {checksum}")
    print()

    results: dict[str, bool] = {}

    if args.target in ("middleware", "both", "all"):
        print("--- Middleware test (HTTP transport) ---")
        results["middleware"] = test_middleware(f"mock-{ts}-http", csv_data, checksum)
        print()

    if args.target in ("server", "both", "all"):
        print("--- Server direct test ---")
        results["server"] = test_server(f"mock-{ts}-direct", csv_data, checksum)
        print()

    if args.target in ("mqtt", "all"):
        print("--- MQTT round-trip test (sensor -> middleware -> server -> sensor) ---")
        results["mqtt"] = test_mqtt(f"mock-{ts}-mqtt", csv_data, checksum)
        print()

    print("--- Summary ---")
    all_ok = True
    for name, ok in results.items():
        status = "OK" if ok else "FAIL"
        print(f"  {name}: {status}")
        if not ok:
            all_ok = False

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
