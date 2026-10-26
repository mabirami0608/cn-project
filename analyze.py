#!/usr/bin/env python3
"""Compare client send records with server receive records."""

import argparse
import glob
import json
from collections import defaultdict
from pathlib import Path

SEVERITIES = ("CRITICAL", "ERROR", "WARNING", "INFO")


def read_jsonl(path):
    records = []
    file_path = Path(path)
    if not file_path.exists():
        return records
    with file_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"Warning: skipping invalid JSON in {path}:{line_number}")
    return records


def main():
    parser = argparse.ArgumentParser(description="Analyze UDP log delivery")
    parser.add_argument("--sent-glob", default="sent_*.jsonl",
                        help='Glob for client send records (default: "sent_*.jsonl")')
    parser.add_argument("--received", default="logs.jsonl")
    parser.add_argument("--malformed", default="malformed.jsonl")
    args = parser.parse_args()

    sent_paths = sorted(glob.glob(args.sent_glob))
    if not sent_paths:
        print(f"No sent records found for pattern: {args.sent_glob}")
        print("Run the client first, or pass --sent-glob with the correct path/pattern.")
        return

    sent = []
    for path in sent_paths:
        sent.extend(read_jsonl(path))
    received = read_jsonl(args.received)
    malformed = read_jsonl(args.malformed)

    sent_keys = set()
    sent_by_severity = defaultdict(set)
    for record in sent:
        key = (record.get("client_id"), record.get("seq"))
        severity = record.get("severity", "UNKNOWN")
        sent_keys.add(key)
        sent_by_severity[severity].add(key)

    received_by_severity = defaultdict(dict)
    for record in received:
        key = (record.get("client_id"), record.get("seq"))
        if key in sent_keys:
            received_by_severity[record.get("severity", "UNKNOWN")][key] = record

    print("\nUDP LOG DELIVERY SUMMARY")
    print("=" * 78)
    print(f'{"Severity":<12} {"Sent":>8} {"Received":>10} {"Lost":>8} {"Loss %":>10} {"Avg delay":>13}')
    print("-" * 78)

    total_sent = total_received = 0
    all_latencies = []
    for severity in SEVERITIES:
        sent_count = len(sent_by_severity[severity])
        received_records = received_by_severity[severity]
        received_count = len(received_records)
        lost = max(0, sent_count - received_count)
        loss_pct = (100.0 * lost / sent_count) if sent_count else 0.0
        latencies = [
            rec["latency_ms"] for rec in received_records.values()
            if isinstance(rec.get("latency_ms"), (int, float))
        ]
        avg_latency = sum(latencies) / len(latencies) if latencies else None
        delay_text = f"{avg_latency:.3f} ms" if avg_latency is not None else "n/a"
        print(f"{severity:<12} {sent_count:>8} {received_count:>10} {lost:>8} {loss_pct:>9.2f}% {delay_text:>13}")
        total_sent += sent_count
        total_received += received_count
        all_latencies.extend(latencies)

    total_lost = max(0, total_sent - total_received)
    total_loss_pct = (100.0 * total_lost / total_sent) if total_sent else 0.0
    avg_all = sum(all_latencies) / len(all_latencies) if all_latencies else None
    print("-" * 78)
    print(f"TOTAL sent={total_sent}, received={total_received}, lost={total_lost}, loss={total_loss_pct:.2f}%")
    print(f"Average delivery time: {avg_all:.3f} ms" if avg_all is not None else "Average delivery time: n/a")
    print(f"Malformed packets rejected by server: {len(malformed)}")
    print("\nNote: delivery time uses sender and receiver wall-clock timestamps.")
    print("For accurate multi-host measurements, keep host clocks synchronized.")
    print("Loss is estimated by matching (client_id, seq); UDP does not guarantee delivery.")


if __name__ == "__main__":
    main()
