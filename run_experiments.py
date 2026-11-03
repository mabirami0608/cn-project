#!/usr/bin/env python3
"""Automated experiment matrix: baseline (no prioritisation) vs SDN prioritisation
under several congestion levels.  Run as root:

    sudo python3 run_experiments.py                       # default matrix
    sudo python3 run_experiments.py --noise 0 4 8 --repeat 3 --duration 60

For every (noise level, mode) it starts ryu-manager, builds the Mininet
topology, runs the log server, 4 log clients and the UDP noise generator,
then stores everything in results/noise<N>_<mode>_r<K>/.
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from mininet.clean import cleanup
from mininet.log import setLogLevel

from topology import CLIENTS, SERVER_IP, SINK_IP, build_network, clear_qos, setup_qos

HERE = Path(__file__).resolve().parent
PY = sys.executable


def host_popen(host, script, *args, log):
    cmd = [PY, "-u", str(HERE / script), *map(str, args)]
    return host.popen(cmd, stdout=open(log, "w"), stderr=subprocess.STDOUT)


def run_one(noise, priority, repeat, args):
    mode = "sdn" if priority else "baseline"
    out = (Path(args.out) / f"noise{noise:g}_{mode}_r{repeat}").resolve()
    out.mkdir(parents=True, exist_ok=True)
    print(f"\n=== noise={noise} Mbps  mode={mode}  repeat={repeat}  -> {out}")

    env = dict(os.environ, PRIORITY="1" if priority else "0", SDN_OUT=str(out))
    ryu = subprocess.Popen(["ryu-manager", str(HERE / "controller.py")], env=env,
                           stdout=open(out / "controller.log", "w"), stderr=subprocess.STDOUT)
    time.sleep(3)
    net = None
    procs = []
    try:
        net = build_network()
        net.start()
        net.waitConnected()
        setup_qos()
        net.pingAll(timeout="1")          # let the learning switches learn all MACs
        hosts = {h.name: h for h in net.hosts}

        server = host_popen(hosts["h5"], "udp_server.py", "--out-dir", out, "--quiet", log=out / "server.log")
        sink = host_popen(hosts["h7"], "traffic_gen.py", "sink", "--port", 9000, log=out / "sink.log")
        procs += [server, sink]
        time.sleep(1)

        count = int(args.duration * args.rate)
        noise_len = args.warmup + 0.75 * args.duration     # leave a quiet tail -> "congestion cleared"
        if noise > 0:
            procs.append(host_popen(hosts["h6"], "traffic_gen.py", "send", "--dst", SINK_IP,
                                    "--port", 9000, "--rate-mbps", noise,
                                    "--duration", noise_len, log=out / "noise.log"))
        time.sleep(args.warmup if noise > 0 else 1)

        clients = [host_popen(hosts[name], "udp_client.py", "--server", SERVER_IP,
                              "--client-id", name, "--count", count, "--rate", args.rate,
                              "--out-dir", out, log=out / f"client_{name}.log")
                   for name in CLIENTS]
        for c in clients:
            c.wait()
        time.sleep(3)                       # drain in-flight packets / queues
        server.terminate()
        server.wait(timeout=5)
        (out / "meta.json").write_text(json.dumps(
            {"noise_mbps": noise, "mode": mode, "repeat": repeat,
             "duration": args.duration, "rate_per_client": args.rate,
             "clients": len(CLIENTS), "warmup": args.warmup}))
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        if net:
            net.stop()
        clear_qos()
        ryu.terminate()
        ryu.wait()
        cleanup()

    with open(out / "analysis.txt", "w") as report:
        subprocess.run([PY, str(HERE / "analyze.py"), "--dir", str(out),
                        "--json", str(out / "summary.json")], stdout=report)
    print((out / "analysis.txt").read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--noise", type=float, nargs="+", default=[0, 3, 6],
                        help="Background traffic in Mbps (bottleneck is 2 Mbps)")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--duration", type=float, default=40, help="Seconds of log traffic")
    parser.add_argument("--rate", type=float, default=20, help="Log messages/s per client")
    parser.add_argument("--warmup", type=float, default=6,
                        help="Seconds noise runs before clients start (lets the controller react)")
    parser.add_argument("--out", default="results")
    args = parser.parse_args()
    if os.geteuid() != 0:
        sys.exit("Run with sudo (Mininet needs root).")
    setLogLevel("warning")
    cleanup()
    for r in range(1, args.repeat + 1):
        for noise in args.noise:
            for priority in (False, True):
                run_one(noise, priority, r, args)
    subprocess.run([PY, str(HERE / "compare.py"), args.out])


if __name__ == "__main__":
    main()
