# Distributed Log Aggregation System (UDP + SDN)

Mininet + Ryu project. Several hosts send logs over UDP to one log server. The path to the server has a 2 Mbps bottleneck. A Ryu (OpenFlow 1.3) controller watches that link and, when it gets congested, puts the more important logs into higher-priority queues. We measure loss and delivery time with and without this.

Team: [Name 1 (SRN)], [Name 2 (SRN)], [Name 3 (SRN)]

## Files

```
app/udp_client.py        log client
app/udp_server.py        log server (UDP ports 5001, 5002, 5003)
app/analyze.py           loss and delay per severity from the client and server files
sdn/topology.py          Mininet topology and OVS queues
sdn/controller.py        Ryu controller
tests/test_*.cmd         step-by-step tests typed into the Mininet prompt
tests/test_malformed.py  sends 8 bad or valid packets to check the server
tests/traffic_gen.py     background UDP traffic (noise) generator
tests/run_experiments.py automatic experiment matrix (baseline vs SDN)
tests/compare.py         builds the comparison table and plot
docs/                    design document material, architecture diagram
```

## Topology

```
h1 h2 h3 h4 (clients)  h6 (noise source)
        \   |   |   /      /
              s1 ======= 2 Mbps bottleneck (s1-eth6) ======= s2 --- h5 (log server 10.0.0.5)
                                                                 \-- h7 (noise sink 10.0.0.7)
```

Log packets are JSON: `client_id`, `seq`, `severity`, `event_ts_ns`, `message`. The severity decides the UDP port, and the controller turns each port into a queue:

| Severity | UDP port | Queue (set by the controller during congestion) |
|---|---|---|
| CRITICAL | 5001 | 1, highest priority |
| ERROR | 5002 | 2, medium |
| WARNING, INFO | 5003 | 0, default (shared with the noise traffic) |

The controller polls the bottleneck port every 2 seconds. At 70% utilization it installs the three priority flows, and below 30% it removes them.

## Setup (Ubuntu)

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch python3-pip git iperf netcat-openbsd
sudo systemctl enable --now openvswitch-switch

# Ryu needs Python 3.9 (it does not install on 3.12 or newer), so it gets its own environment
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
uv python install 3.9
uv venv --python 3.9 ~/ryu-env
source ~/ryu-env/bin/activate
uv pip install "setuptools<58" wheel pbr
uv pip install --no-build-isolation eventlet==0.30.2 ryu
ryu-manager --version
```

Mininet and the app scripts use the normal system Python.

## A. Quick demo: run the tests by hand

Open three terminals. Start in this order: `sudo mn -c`, then Ryu, then the topology. `mn -c` also stops any running Ryu.

Terminal 1, controller:

```bash
source ~/ryu-env/bin/activate
cd <repo folder>
ryu-manager sdn/controller.py          # use PRIORITY=0 ryu-manager sdn/controller.py for the baseline
```

Terminal 2, network:

```bash
cd <repo folder>
sudo mn -c
sudo python3 sdn/topology.py
```

At the `mininet>` prompt type `pingall` (expect 0% dropped), then run a test with `source`:

| Test | Ryu mode | Command at `mininet>` | Read the result |
|---|---|---|---|
| Normal traffic | normal | `source tests/test_normal.cmd` | `results/normal/summary.txt` |
| Malformed packets | normal | `source tests/test_bad.cmd` | `results/bad/malformed.jsonl` (7 lines) and `results/bad/logs.jsonl` (1 line) |
| Congestion, no priority | `PRIORITY=0` | `source tests/test_base.cmd` | `results/base/summary.txt` |
| Congestion, with priority | normal | `source tests/test_prio.cmd` | `results/prio/summary.txt`, `flows.txt`, `tc.txt` |

Read the files from a third, normal terminal. Each test takes 30 to 40 seconds, so wait for the `mininet>` prompt to come back. Between the baseline and prioritized tests, type `exit`, run `sudo mn -c`, restart Ryu in the other mode, start the topology again and run `pingall`.

## B. Full experiment matrix (baseline vs SDN)

`tests/run_experiments.py` does everything automatically. For every noise level it runs the baseline and the SDN version: it starts Ryu, builds the network, runs the server, four clients and the noise generator, then analyzes the result.

Do not start Ryu yourself for this, because the script starts its own. Activate the Ryu environment so that `ryu-manager` is on the PATH, and call the system Python explicitly (the environment's Python has no Mininet):

```bash
source ~/ryu-env/bin/activate
cd <repo folder>
sudo env PATH="$PATH" /usr/bin/python3 tests/run_experiments.py --noise 0 3 6 --duration 40 --repeat 1
```

Options: `--noise` (background Mbps, the bottleneck is 2), `--duration` (seconds of log traffic), `--rate` (logs per second per client), `--repeat` (runs per setting).

Each run is saved in `results/experiments/noise<N>_<mode>_r<K>/` with `analysis.txt`, `summary.json`, `controller.log` and `events.jsonl` (the controller's utilization samples and congestion events). When all runs finish it prints the comparison table and saves `results/experiments/comparison.png` and `comparison.json`. To rebuild it without re-running: `python3 tests/compare.py results/experiments`.

The table has loss per severity, CRITICAL average and p95 delay, and the controller's reaction time (seconds from the start of the noise until the priority rules were installed).

## Running the app by hand

```bash
python3 app/udp_server.py --out-dir run1 --run-time 30
python3 app/udp_client.py --server 10.0.0.5 --client-id client1 --count 1000 --rate 50 --out-dir run1
python3 app/analyze.py --dir run1
```

## Problems we ran into

- `pingall` fails or hosts say "Destination Host Unreachable": Ryu is not running or `mn -c` stopped it. Restart in the order above.
- `Unable to contact the remote controller`: start Ryu before the topology.
- Ryu fails to install with a `get_script_args` error: the Python version is too new. Use the 3.9 environment from the setup.
- Pasting long commands at the `mininet>` prompt can add stray characters, and a host command left running in the foreground blocks the prompt. Use the `.cmd` files with `source`, and put `&` after anything long-running.
- `sudo python3 tests/run_experiments.py` cannot find `ryu-manager`: use the `sudo env PATH="$PATH" /usr/bin/python3 ...` form above.
- `results/` and the `sent_*.jsonl` and `logs*` files are created by the runs and are not meant to be committed, except the few evidence files you choose to copy into `docs/evidence/`.
