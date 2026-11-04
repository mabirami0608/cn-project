#!/usr/bin/env python3
"""Compare client send records with server receive records.

Works on one run folder (--dir) or on explicit file paths. With --json it also
writes a machine-readable summary that compare.py uses to build the
baseline-vs-SDN table.
"""

import argparse
import glob
import json
import math
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


def percentile(values, pct):
    """Nearest-rank percentile, or None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def make_stats(sent_count, latencies, received_count):
    lost = max(0, sent_count - received_count)
    return {
        "sent": sent_count,
        "received": received_count,
        "lost": lost,
        "loss_pct": round(100.0 * lost / sent_count, 3) if sent_count else 0.0,
        "avg_ms": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "p95_ms": round(percentile(latencies, 95), 3) if latencies else None,
    }


def sdn_reaction_seconds(base):
    """Seconds from the start of background traffic to the controller's
    'congestion detected' event. Needs meta.json and events.jsonl in the run folder."""
    meta_path = Path(base) / "meta.json"
    events_path = Path(base) / "events.jsonl"
    if not meta_path.exists() or not events_path.exists():
        return None
    start = json.loads(meta_path.read_text()).get("noise_start_ns")
    if not start:
        return None
    for event in read_jsonl(events_path):
        if event.get("event") == "congestion_detected":
            return round((event["ts_ns"] - start) / 1e9, 2)
    return None


def fmt(value):
    return f"{value:.3f} ms" if value is not None else "n/a"


def main():
    parser = argparse.ArgumentParser(description="Analyze UDP log delivery")
    parser.add_argument("--dir", default=None,
                        help="run folder holding sent_*.jsonl, logs.jsonl and malformed.jsonl")
    parser.add_argument("--sent-glob", default=None,
                        help='glob for client send records (default: "<dir>/sent_*.jsonl")')
    parser.add_argument("--received", default=None, help="default: <dir>/logs.jsonl")
    parser.add_argument("--malformed", default=None, help="default: <dir>/malformed.jsonl")
    parser.add_argument("--json", default=None, help="also write a JSON summary to this path")
    args = parser.parse_args()

    base = Path(args.dir) if args.dir else Path(".")
    sent_glob = args.sent_glob or str(base / "sent_*.jsonl")
    received_path = args.received or str(base / "logs.jsonl")
    malformed_path = args.malformed or str(base / "malformed.jsonl")

    sent_paths = sorted(glob.glob(sent_glob))
    if not sent_paths:
        print(f"No sent records found for pattern: {sent_glob}")
        print("Run the client first, or pass --dir / --sent-glob with the correct path.")
        return

    sent = []
    for path in sent_paths:
        sent.extend(read_jsonl(path))
    received = read_jsonl(received_path)
    malformed = read_jsonl(malformed_path)

    sent_keys = set()
    sent_by_severity = defaultdict(set)
    for record in sent:
        key = (record.get("client_id"), record.get("seq"))
        sent_keys.add(key)
        sent_by_severity[record.get("severity", "UNKNOWN")].add(key)

    received_by_severity = defaultdict(dict)
    for record in received:
        key = (record.get("client_id"), record.get("seq"))
        if key in sent_keys:
            received_by_severity[record.get("severity", "UNKNOWN")][key] = record

    print("\nUDP LOG DELIVERY SUMMARY")
    print("=" * 92)
    print(f'{"Severity":<10} {"Sent":>7} {"Received":>9} {"Lost":>7} {"Loss %":>8} '
          f'{"Avg delay":>13} {"P95 delay":>13}')
    print("-" * 92)

    summary = {"severity": {}}
    total_sent = total_received = 0
    all_latencies = []
    for severity in SEVERITIES:
        sent_count = len(sent_by_severity[severity])
        records = received_by_severity[severity]
        latencies = [r["latency_ms"] for r in records.values()
                     if isinstance(r.get("latency_ms"), (int, float))]
        stats = make_stats(sent_count, latencies, len(records))
        summary["severity"][severity] = stats
        print(f'{severity:<10} {stats["sent"]:>7} {stats["received"]:>9} {stats["lost"]:>7} '
              f'{stats["loss_pct"]:>7.2f}% {fmt(stats["avg_ms"]):>13} {fmt(stats["p95_ms"]):>13}')
        total_sent += sent_count
        total_received += len(records)
        all_latencies.extend(latencies)

    total = make_stats(total_sent, all_latencies, total_received)
    summary["total"] = total
    summary["malformed"] = len(malformed)
    summary["sdn_reaction_s"] = sdn_reaction_seconds(base)

    print("-" * 92)
    print(f'TOTAL sent={total["sent"]}, received={total["received"]}, '
          f'lost={total["lost"]}, loss={total["loss_pct"]:.2f}%')
    print(f'Average delivery time: {fmt(total["avg_ms"])}')
    print(f"Malformed packets rejected by server: {len(malformed)}")
    if summary["sdn_reaction_s"] is not None:
        print(f'Controller reaction time: {summary["sdn_reaction_s"]} s after background traffic started')
    print("\nNote: delivery time uses sender and receiver wall-clock timestamps.")
    print("All Mininet hosts share one clock, so the values are comparable.")
    print("Loss is estimated by matching (client_id, seq); UDP does not guarantee delivery.")

    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
