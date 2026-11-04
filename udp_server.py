#!/usr/bin/env python3
"""Central UDP log collector listening on ports 5001, 5002, and 5003."""

import argparse
import json
import select
import signal
import socket
import time
from pathlib import Path

PORTS = (5001, 5002, 5003)
VALID_SEVERITIES = {"INFO", "WARNING", "ERROR", "CRITICAL"}


def valid_packet(packet, receiving_port):
    if not isinstance(packet, dict):
        return False, "JSON root must be an object"
    required = ("client_id", "seq", "severity", "event_ts_ns", "message")
    if any(key not in packet for key in required):
        return False, "missing required field"
    if not isinstance(packet["client_id"], str) or not packet["client_id"]:
        return False, "client_id must be a non-empty string"
    if not isinstance(packet["seq"], int) or packet["seq"] < 1:
        return False, "seq must be a positive integer"
    if packet["severity"] not in VALID_SEVERITIES:
        return False, "invalid severity"
    if not isinstance(packet["event_ts_ns"], int):
        return False, "event_ts_ns must be an integer"
    if not isinstance(packet["message"], str):
        return False, "message must be a string"

    expected_port = 5001 if packet["severity"] == "CRITICAL" else (
        5002 if packet["severity"] == "ERROR" else 5003
    )
    if receiving_port != expected_port:
        return False, f"severity/port mismatch (expected {expected_port})"
    return True, ""


def _stop(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description="Central UDP log server")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--out-dir", default=".",
                        help="folder for logs.jsonl, logs.txt and malformed.jsonl (default: current folder)")
    parser.add_argument("--quiet", action="store_true", help="do not print every accepted log")
    parser.add_argument("--run-time", type=float, default=0,
                        help="stop after N seconds (0 = run until Ctrl+C)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "logs.jsonl"
    text_log_path = out_dir / "logs.txt"
    malformed_path = out_dir / "malformed.jsonl"
    signal.signal(signal.SIGTERM, _stop)

    sockets = []
    for port in PORTS:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((args.bind, port))
        sockets.append(sock)
        print(f"Listening on UDP port {port}")

    print("UDP log server running. Press Ctrl+C to stop.", flush=True)
    deadline = time.monotonic() + args.run_time if args.run_time else None
    accepted = rejected = 0

    try:
        with log_path.open("a", encoding="utf-8") as json_log, \
             text_log_path.open("a", encoding="utf-8") as text_log, \
             malformed_path.open("a", encoding="utf-8") as malformed_log:
            while True:
                timeout = 1.0
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    timeout = min(timeout, remaining)
                readable, _, _ = select.select(sockets, [], [], timeout)
                for sock in readable:
                    data, address = sock.recvfrom(65535)
                    received_ts_ns = time.time_ns()
                    port = sock.getsockname()[1]
                    try:
                        packet = json.loads(data.decode("utf-8"))
                        ok, reason = valid_packet(packet, port)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        packet = None
                        ok, reason = False, f"invalid JSON/UTF-8: {exc}"

                    if not ok:
                        bad_record = {
                            "received_ts_ns": received_ts_ns,
                            "source_ip": address[0],
                            "source_port": address[1],
                            "destination_port": port,
                            "reason": reason,
                            "raw": data[:300].decode("utf-8", errors="replace"),
                        }
                        malformed_log.write(json.dumps(bad_record) + "\n")
                        malformed_log.flush()
                        rejected += 1
                        print(f"Rejected malformed packet from {address}: {reason}", flush=True)
                        continue

                    latency_ms = (received_ts_ns - packet["event_ts_ns"]) / 1_000_000
                    record = {
                        **packet,
                        "source_ip": address[0],
                        "source_port": address[1],
                        "destination_port": port,
                        "received_ts_ns": received_ts_ns,
                        "latency_ms": round(latency_ms, 3),
                    }
                    json_log.write(json.dumps(record) + "\n")
                    json_log.flush()
                    text_log.write(
                        f'{received_ts_ns} | {packet["severity"]} | '
                        f'{packet["client_id"]}#{packet["seq"]} | '
                        f'{address[0]} | {packet["message"]} | '
                        f'{latency_ms:.3f} ms\n'
                    )
                    text_log.flush()
                    accepted += 1
                    if not args.quiet:
                        print(
                            f'[{packet["severity"]}] {packet["client_id"]}#{packet["seq"]} '
                            f'from {address[0]} - {packet["message"]}'
                        )
    except KeyboardInterrupt:
        print("\nStopping UDP log server...")
    finally:
        for sock in sockets:
            sock.close()
        print(f"Server stopped: {accepted} accepted, {rejected} rejected", flush=True)


if __name__ == "__main__":
    main()
