"""Ryu controller: L2 learning switch + congestion-triggered log prioritization.

- Learns MAC addresses (like simple_switch_13).
- Polls port stats of the bottleneck port (s1 port 6) every POLL seconds.
- When utilization >= HIGH, installs high-priority flows that send each
  log severity port into its own OVS queue.
- When utilization < LOW, removes those flows again.

Set PRIORITY=0 in the environment to disable prioritization (baseline run):
    PRIORITY=0 ryu-manager sdn/controller.py

If SDN_OUT is set to a folder, utilization samples and congestion events are
appended to <SDN_OUT>/events.jsonl (used by analyze.py for the reaction time).
"""
import json
import os
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (CONFIG_DISPATCHER, DEAD_DISPATCHER,
                                    MAIN_DISPATCHER, set_ev_cls)
from ryu.lib import hub
from ryu.lib.packet import ether_types, ethernet, packet
from ryu.ofproto import ofproto_v1_3

BOTTLENECK_DPID = 1          # s1
BOTTLENECK_PORT = 6          # s1-eth6 -> s2
LINK_BPS = 2000000
HIGH = 0.70                  # congested above 70% utilization
LOW = 0.30                   # recovered below 30%
POLL = 2                     # seconds
SERVER_IP = "10.0.0.5"
COOKIE = 0xABCD
PRIORITY_FLOW = 100
# UDP dst port -> OVS queue id
QUEUE_MAP = {5001: 1, 5002: 2, 5003: 0}


class LogPriorityController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.congested = False
        self.prev = None
        self.enabled = os.environ.get("PRIORITY", "1") == "1"
        out_dir = os.environ.get("SDN_OUT")
        self.events_path = os.path.join(out_dir, "events.jsonl") if out_dir else None
        self.logger.info("Prioritization %s",
                         "ENABLED" if self.enabled else "DISABLED (baseline)")
        hub.spawn(self._monitor)

    def _event(self, name, **fields):
        """Append one event line to events.jsonl (only when SDN_OUT is set)."""
        if not self.events_path:
            return
        record = {"ts_ns": time.time_ns(), "event": name, **fields}
        try:
            with open(self.events_path, "a") as handle:
                handle.write(json.dumps(record) + "\n")
        except OSError:
            pass

    # ---------- switch connection ----------
    @set_ev_cls(ofp_event.EventOFPStateChange,
                [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(dp.id, None)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _features(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER,
                                          ofp.OFPCML_NO_BUFFER)]
        self._add_flow(dp, 0, match, actions)

    def _add_flow(self, dp, priority, match, actions, cookie=0):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS,
                                             actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=priority,
                                      match=match, instructions=inst,
                                      cookie=cookie))

    # ---------- L2 learning ----------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        in_port = msg.match["in_port"]

        eth = packet.Packet(msg.data).get_protocols(ethernet.ethernet)[0]
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        table = self.mac_to_port.setdefault(dp.id, {})
        table[eth.src] = in_port
        out_port = table.get(eth.dst, ofp.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]

        if out_port != ofp.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=eth.dst,
                                    eth_src=eth.src)
            self._add_flow(dp, 1, match, actions)

        dp.send_msg(parser.OFPPacketOut(
            datapath=dp, buffer_id=ofp.OFP_NO_BUFFER, in_port=in_port,
            actions=actions, data=msg.data))

    # ---------- monitoring ----------
    def _monitor(self):
        while True:
            hub.sleep(POLL)
            dp = self.datapaths.get(BOTTLENECK_DPID)
            if dp is None:
                continue
            req = dp.ofproto_parser.OFPPortStatsRequest(
                dp, 0, port_no=BOTTLENECK_PORT)
            dp.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats(self, ev):
        dp = ev.msg.datapath
        if dp.id != BOTTLENECK_DPID:
            return
        for stat in ev.msg.body:
            if stat.port_no != BOTTLENECK_PORT:
                continue
            now = time.time()
            if self.prev is not None:
                prev_bytes, prev_time = self.prev
                bps = (stat.tx_bytes - prev_bytes) * 8 / (now - prev_time)
                util = bps / LINK_BPS
                self.logger.info("bottleneck util=%3.0f%% (%.0f kbps) "
                                 "congested=%s", util * 100, bps / 1000,
                                 self.congested)
                self._event("util", util=round(util, 3), kbps=round(bps / 1000))
                self._evaluate(dp, util)
            self.prev = (stat.tx_bytes, now)

    def _evaluate(self, dp, util):
        if not self.enabled:
            return
        if not self.congested and util >= HIGH:
            self.congested = True
            self.logger.warning("CONGESTION detected -> installing "
                                "priority rules")
            self._event("congestion_detected")
            self._install_priority(dp)
        elif self.congested and util < LOW:
            self.congested = False
            self.logger.warning("Congestion cleared -> removing "
                                "priority rules")
            self._event("congestion_cleared")
            self._remove_priority(dp)

    # ---------- dynamic rules ----------
    def _install_priority(self, dp):
        parser = dp.ofproto_parser
        for udp_port, queue in QUEUE_MAP.items():
            match = parser.OFPMatch(eth_type=0x0800, ip_proto=17,
                                    ipv4_dst=SERVER_IP, udp_dst=udp_port)
            actions = [parser.OFPActionSetQueue(queue),
                       parser.OFPActionOutput(BOTTLENECK_PORT)]
            self._add_flow(dp, PRIORITY_FLOW, match, actions, cookie=COOKIE)

    def _remove_priority(self, dp):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        dp.send_msg(parser.OFPFlowMod(
            datapath=dp, cookie=COOKIE, cookie_mask=0xFFFFFFFFFFFFFFFF,
            table_id=ofp.OFPTT_ALL, command=ofp.OFPFC_DELETE,
            out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
            match=parser.OFPMatch()))
