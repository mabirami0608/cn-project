#!/usr/bin/env python3
"""Robustness test: send invalid datagrams; the server must reject, log and survive."""

import argparse
import json
import socket
import time

p = argparse.ArgumentParser()
p.add_argument("--server", default="127.0.0.1")
args = p.parse_args()

good = {"client_id": "t", "seq": 1, "severity": "CRITICAL",
        "event_ts_ns": time.time_ns(), "message": "ok"}
cases = [
    ("not json",             5001, b"hello world"),
    ("invalid utf-8",        5001, b"\xff\xfe\x00"),
    ("json array",           5001, b"[1,2,3]"),
    ("missing field",        5001, json.dumps({"client_id": "t"}).encode()),
    ("bad severity",         5003, json.dumps({**good, "severity": "FATAL"}).encode()),
    ("seq is a string",      5001, json.dumps({**good, "seq": "1"}).encode()),
    ("wrong port for sev.",  5003, json.dumps(good).encode()),   # CRITICAL sent to 5003
    ("valid packet",         5001, json.dumps(good).encode()),   # must be accepted
]
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for name, port, data in cases:
    s.sendto(data, (args.server, port))
    print(f"sent: {name:<22} -> port {port}")
    time.sleep(0.1)
print("Expected: 7 rejected, 1 accepted (see malformed.jsonl / logs.jsonl)")
