#!/usr/bin/env python3
"""Send timestamped, sequenced log messages over UDP.

Severity -> destination port (the SDN controller prioritises by this port):
  CRITICAL 5001 | ERROR 5002 | WARNING, INFO 5003
"""

import argparse
import json
import socket
import time
from pathlib import Path

PORT_BY_SEVERITY = {
    "CRITICAL": 5001,  # highest priority
    "ERROR": 5002,     # medium priority
    "WARNING": 5003,
    "INFO": 5003,      # normal priority
}
SEVERITIES = ["INFO", "WARNING", "ERROR", "CRITICAL"]
MESSAGES = {
    "INFO": "Client accessed server",
    "WARNING": "Response time is increasing",
    "ERROR": "Request failed",
    "CRITICAL": "Critical service failure",
}


def main():
    parser = argparse.ArgumentParser(description="UDP log client")
    parser.add_argument("--server", required=True, help="Server IP address")
    parser.add_argument("--client-id", default="client1")
    parser.add_argument("--count", type=int, default=100, help="Number of messages")
    parser.add_argument("--rate", type=float, default=20.0, help="Messages per second")
    parser.add_argument("--severity", choices=SEVERITIES + ["MIXED"], default="MIXED")
    parser.add_argument("--out-dir", default=".", help="Where the sent_<id>.jsonl file goes")
    parser.add_argument("--sent-file", default=None, help="Defaults to <out-dir>/sent_<client-id>.jsonl")
    parser.add_argument("--base-port", type=int, default=5001,
                        help="Base port; expected ports are 5001, 5002, 5003")
    args = parser.parse_args()

    if args.count < 1:
        parser.error("--count must be at least 1")
    if args.rate <= 0:
        parser.error("--rate must be greater than 0")
    if args.base_port != 5001:
        parser.error("This project currently uses UDP ports 5001, 5002, and 5003")

    sent_path = Path(args.sent_file or Path(args.out_dir) / f"sent_{args.client_id}.jsonl")
    sent_path.parent.mkdir(parents=True, exist_ok=True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = 1.0 / args.rate
    sent_count = 0
    start = time.monotonic()

    try:
        with sent_path.open("a", encoding="utf-8") as sent_file:
            for seq in range(1, args.count + 1):
                severity = args.severity if args.severity != "MIXED" else SEVERITIES[(seq - 1) % len(SEVERITIES)]
                sent_ts_ns = time.time_ns()
                packet = {
                    "version": 1,
                    "client_id": args.client_id,
                    "seq": seq,
                    "severity": severity,
                    "event_ts_ns": sent_ts_ns,
                    "message": MESSAGES[severity],
                }
                encoded = json.dumps(packet, separators=(",", ":")).encode("utf-8")
                destination_port = PORT_BY_SEVERITY[severity]
                try:
                    sock.sendto(encoded, (args.server, destination_port))
                except OSError as exc:
                    print(f"Send failed for seq={seq}: {exc}")
                else:
                    sent_file.write(json.dumps({
                        "client_id": args.client_id,
                        "seq": seq,
                        "severity": severity,
                        "sent_ts_ns": sent_ts_ns,
                        "destination_port": destination_port,
                    }) + "\n")
                    sent_file.flush()
                    sent_count += 1
                # absolute schedule: no drift, steady rate
                delay = start + seq * interval - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
    finally:
        sock.close()

    print(f"Client {args.client_id}: sent {sent_count}/{args.count} messages")
    print(f"Sent-packet records: {sent_path}")


if __name__ == "__main__":
    main()
