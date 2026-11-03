#!/usr/bin/env python3
"""Controlled background UDP traffic used to congest the s1->s2 bottleneck.

  python3 traffic_gen.py sink --port 9000
  python3 traffic_gen.py send --dst 10.0.0.7 --port 9000 --rate-mbps 5 --duration 30
"""

import argparse
import signal
import socket
import time

OVERHEAD = 42  # Ethernet 14 + IP 20 + UDP 8


def _stop(signum, frame):
    raise KeyboardInterrupt


def send(args):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    payload = b"x" * args.size
    interval = (args.size + OVERHEAD) * 8 / (args.rate_mbps * 1e6)
    end = time.monotonic() + args.duration
    nxt = time.monotonic()
    sent = 0
    while True:
        now = time.monotonic()
        if now >= end:
            break
        if now < nxt:
            time.sleep(min(nxt - now, 0.005))
            continue
        while nxt <= now:
            try:
                sock.sendto(payload, (args.dst, args.port))
                sent += 1
            except OSError:
                pass
            nxt += interval
    print(f"noise sender: {sent} packets, ~{sent * (args.size + OVERHEAD) * 8 / args.duration / 1e6:.2f} Mbps offered",
          flush=True)


def sink(args):
    signal.signal(signal.SIGTERM, _stop)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    sock.bind(("0.0.0.0", args.port))
    packets = size = 0
    try:
        while True:
            data, _ = sock.recvfrom(65535)
            packets += 1
            size += len(data)
    except KeyboardInterrupt:
        print(f"noise sink: {packets} packets, {size} bytes received", flush=True)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("send")
    s.add_argument("--dst", required=True)
    s.add_argument("--port", type=int, default=9000)
    s.add_argument("--rate-mbps", type=float, required=True)
    s.add_argument("--duration", type=float, default=30)
    s.add_argument("--size", type=int, default=1200, help="UDP payload bytes")
    k = sub.add_parser("sink")
    k.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    send(args) if args.mode == "send" else sink(args)


if __name__ == "__main__":
    main()
