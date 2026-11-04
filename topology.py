#!/usr/bin/env python3
"""Mininet topology + OVS QoS queues for the SDN log-aggregation project.

        h1 h2 h3 h4 (log clients, 10.0.0.1-4)      h5 (log server 10.0.0.5)
          \\ |  |  /  h6 (noise src .6)               |      h7 (noise sink .7)
           \\|  | /    /                              |       /
            [ s1 ]=====(2 Mbps bottleneck, s1-eth6)=====[ s2 ]
   s1 ports: h1..h4 = 1..4, h6 = 5, s2 = 6        s2 ports: h5 = 1, h7 = 2, s1 = 3

Usage (root, ryu-manager controller.py already running):
    sudo python3 topology.py            # builds net, QoS, drops into Mininet CLI
"""
import argparse
import subprocess
from functools import partial

from mininet.cli import CLI
from mininet.link import Link
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch, RemoteController

BOTTLENECK_IFACE = "s1-eth6"
LINK_BPS = 2_000_000
SERVER_IP = "10.0.0.5"
SINK_IP = "10.0.0.7"
CLIENTS = ["h1", "h2", "h3", "h4"]


def build_network(controller_ip="127.0.0.1", controller_port=6653):
    net = Mininet(controller=None, link=Link, autoSetMacs=True, build=False,
                  switch=partial(OVSKernelSwitch, protocols="OpenFlow13"))
    net.addController("c0", controller=RemoteController,
                      ip=controller_ip, port=controller_port)
    s1 = net.addSwitch("s1", dpid="0000000000000001")
    s2 = net.addSwitch("s2", dpid="0000000000000002")
    for i, name in enumerate(CLIENTS, 1):
        net.addLink(net.addHost(name, ip=f"10.0.0.{i}/24"), s1, port2=i)
    net.addLink(net.addHost("h6", ip="10.0.0.6/24"), s1, port2=5)      # noise source
    net.addLink(s1, s2, port1=6, port2=3)                              # bottleneck
    net.addLink(net.addHost("h5", ip="10.0.0.5/24"), s2, port2=1)      # log server
    net.addLink(net.addHost("h7", ip="10.0.0.7/24"), s2, port2=2)      # noise sink
    return net


def _ovs(*args, check=True):
    return subprocess.run(["ovs-vsctl", *args], check=check,
                          capture_output=True, text=True)


def clear_qos():
    _ovs("--all", "destroy", "qos", check=False)
    _ovs("--all", "destroy", "queue", check=False)


def setup_qos(iface=BOTTLENECK_IFACE, rate=LINK_BPS):
    """2 Mbps HTB on the bottleneck port with 3 queues (lower priority = served first).

    queue 1: CRITICAL (udp 5001)  prio 0, min 1.0 Mbps
    queue 2: ERROR    (udp 5002)  prio 1, min 0.5 Mbps
    queue 0: default  (WARNING/INFO + background traffic) prio 2, min 0.2 Mbps
    The same QoS is used for baseline and SDN runs; only the controller's
    set_queue rules differ.
    """
    clear_qos()
    r = str(rate)
    _ovs("--", "set", "port", iface, "qos=@q",
         "--", "--id=@q", "create", "qos", "type=linux-htb",
         f"other-config:max-rate={r}",
         "queues:0=@q0", "queues:1=@q1", "queues:2=@q2",
         "--", "--id=@q0", "create", "queue",
         "other-config:min-rate=200000", f"other-config:max-rate={r}", "other-config:priority=2",
         "--", "--id=@q1", "create", "queue",
         "other-config:min-rate=1000000", f"other-config:max-rate={r}", "other-config:priority=0",
         "--", "--id=@q2", "create", "queue",
         "other-config:min-rate=500000", f"other-config:max-rate={r}", "other-config:priority=1")
    subprocess.run(["ip", "link", "set", iface, "txqueuelen", "100"], check=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--controller-ip", default="127.0.0.1")
    parser.add_argument("--controller-port", type=int, default=6653)
    args = parser.parse_args()
    setLogLevel("info")
    net = build_network(args.controller_ip, args.controller_port)
    try:
        net.start()
        net.waitConnected()
        setup_qos()
        net.pingAll()
        CLI(net)
    finally:
        net.stop()
        clear_qos()


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
