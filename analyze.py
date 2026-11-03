#!/usr/bin/env python3
"""Compare client send records with server receive records."""

import argparse
import csv
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


def percentile(values, pct):
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def stats(latencies):
    if not latencies:
        return None, None, None
    return sum(latencies) / len(latencies), percentile(latencies, 95), max(latencies)


def fmt(value):
    return f"{value:.1f}" if value is not None else "n/a"


def main():
    parser = argparse.ArgumentParser(description="Analyze UDP log delivery")
    parser.add_argument("--dir", default=".", help="Run directory (all other paths are relative to it)")
    parser.add_argument("--sent-glob", default="sent_*.jsonl",
                        help='Glob for client send records (default: "sent_*.jsonl")')
    parser.add_argument("--received", default="logs.jsonl")
    parser.add_argument("--malformed", default="malformed.jsonl")
    parser.add_argument("--json", default=None, help="Write machine-readable summary here")
    args = parser.parse_args()

    base = Path(args.dir)
    sent_paths = sorted(glob.glob(str(base / args.sent_glob)))
    if not sent_paths:
        print(f"No sent records found for pattern: {base / args.sent_glob}")
        print("Run the client first, or pass --dir/--sent-glob with the correct path/pattern.")
        return

    sent = []
    for path in sent_paths:
        sent.extend(read_jsonl(path))
    received = read_jsonl(base / args.received)
    malformed = read_jsonl(base / args.malformed)

    sent_keys = set()
    sent_by_severity = defaultdict(set)
    for record in sent:
        key = (record.get("client_id"), record.get("seq"))
        sent_keys.add(key)
        sent_by_severity[record.get("severity", "UNKNOWN")].add(key)

    received_by_severity = defaultdict(dict)
    duplicates = 0
    for record in received:
        key = (record.get("client_id"), record.get("seq"))
        if key in sent_keys:
            bucket = received_by_severity[record.get("severity", "UNKNOWN")]
            duplicates += key in bucket
            bucket[key] = record

    print("\nUDP LOG DELIVERY SUMMARY")
    print("=" * 92)
    print(f'{"Severity":<10} {"Sent":>7} {"Recv":>7} {"Lost":>6} {"Loss %":>8} '
          f'{"Avg ms":>9} {"P95 ms":>9} {"Max ms":>9}')
    print("-" * 92)

    summary = {"severity": {}}
    total_sent = total_received = 0
    all_latencies = []
    for severity in SEVERITIES:
        sent_count = len(sent_by_severity[severity])
        records = received_by_severity[severity]
        received_count = len(records)
        lost = max(0, sent_count - received_count)
        loss_pct = 100.0 * lost / sent_count if sent_count else 0.0
        lats = [r["latency_ms"] for r in records.values()
                if isinstance(r.get("latency_ms"), (int, float))]
        avg, p95, mx = stats(lats)
        print(f"{severity:<10} {sent_count:>7} {received_count:>7} {lost:>6} {loss_pct:>7.2f}% "
              f"{fmt(avg):>9} {fmt(p95):>9} {fmt(mx):>9}")
        summary["severity"][severity] = {
            "sent": sent_count, "received": received_count, "lost": lost,
            "loss_pct": round(loss_pct, 3), "avg_ms": avg, "p95_ms": p95, "max_ms": mx}
        total_sent += sent_count
        total_received += received_count
        all_latencies.extend(lats)

    total_lost = max(0, total_sent - total_received)
    total_loss_pct = 100.0 * total_lost / total_sent if total_sent else 0.0
    avg_all, p95_all, max_all = stats(all_latencies)
    summary["total"] = {"sent": total_sent, "received": total_received, "lost": total_lost,
                        "loss_pct": round(total_loss_pct, 3), "avg_ms": avg_all,
                        "p95_ms": p95_all, "max_ms": max_all,
                        "malformed": len(malformed), "duplicates": duplicates}
    print("-" * 92)
    print(f"TOTAL sent={total_sent}, received={total_received}, lost={total_lost}, loss={total_loss_pct:.2f}%")
    print(f"Average delivery time: {fmt(avg_all)} ms   (P95 {fmt(p95_all)} ms, max {fmt(max_all)} ms)")
    print(f"Malformed packets rejected by server: {len(malformed)}   Duplicates: {duplicates}")

    # SDN reaction time: first send -> first CONGESTION event from the controller
    events = base / "sdn_events.csv"
    if events.exists() and sent:
        first_sent = min(r["sent_ts_ns"] for r in sent if "sent_ts_ns" in r)
        with events.open() as handle:
            rows = list(csv.DictReader(handle))
        cong = [int(r["ts_ns"]) for r in rows if r["event"] == "CONGESTION"]
        if cong:
            react = (cong[0] - first_sent) / 1e9
            summary["sdn_reaction_s"] = round(react, 2)
            print(f"Controller detected congestion {react:.1f} s after the first log was sent "
                  f"({len(cong)} congestion event(s)).")

    print("\nNote: delivery time uses sender and receiver wall-clock timestamps.")
    print("All Mininet hosts share one machine clock, so this is accurate here.")
    print("Loss is estimated by matching (client_id, seq); UDP does not guarantee delivery.")

    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
