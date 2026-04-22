from __future__ import annotations

import math
import re
import sys
from pathlib import Path
from typing import Any

COPPER_LAYER_RE = re.compile(r'^\s*\(\s*\d+\s+"([^"]+\.Cu)"\s+signal\)')
FANOUT_KEYWORDS = ("BGA", "PGA", "QFN", "QFP", "LQFP", "TQFP")
GROUND_PATTERNS = ("GND", "VSS", "AGND", "DGND", "PGND", "GNDA", "GNDD")
POWER_PATTERNS = ("VCC", "VDD", "+3.3", "+5", "+12", "+1.8", "+2.5", "VBUS", "VBAT", "VIN")
DIFF_PATTERNS = [
    ("_P", "_N"),
    ("_p", "_n"),
    ("+", "-"),
    ("_DP", "_DN"),
    ("_D+", "_D-"),
    ("_TX+", "_TX-"),
    ("_RX+", "_RX-"),
    ("_TXP", "_TXN"),
    ("_RXP", "_RXN"),
    ("_t", "_c"),
    ("_T", "_C"),
]
HIGH_SPEED_NET_PATTERNS = {
    "ultra_high": ["DDR3", "DDR4", "DDR5", "LPDDR", "PCIE", "SATA", "USB3", "SGMII", "XGMII", "TMDS"],
    "high": ["DDR", "DQ", "DQS", "RGMII", "RMII", "QSPI", "QIO", "SDIO", "LVDS", "HDMI", "USB", "ETH", "ULPI", "EMMC"],
    "medium": ["SPI", "SCK", "SCLK", "MOSI", "MISO", "CLK", "MCLK", "BCLK", "JTAG", "TCK", "SWDIO", "SWCLK", "CAN"],
}


def _import_parse_kicad_pcb():
    tools_root = Path(__file__).resolve().parent / "kicad_routing_tools"
    if str(tools_root) not in sys.path:
        sys.path.insert(0, str(tools_root))
    from kicad_parser import parse_kicad_pcb

    return parse_kicad_pcb


def detect_copper_layers(pcb_path: Path) -> list[str]:
    copper_layers: list[str] = []
    for line in pcb_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = COPPER_LAYER_RE.match(line)
        if match:
            copper_layers.append(match.group(1))
    return copper_layers


def classify_speed(net_names: list[str]) -> dict[str, Any]:
    matched: list[dict[str, str]] = []
    highest_tier: str | None = None
    tier_order = ["ultra_high", "high", "medium"]
    for net_name in net_names:
        upper_name = net_name.upper()
        for tier in tier_order:
            if any(token in upper_name for token in HIGH_SPEED_NET_PATTERNS[tier]):
                matched.append({"net": net_name, "tier": tier})
                if highest_tier is None or tier_order.index(tier) < tier_order.index(highest_tier):
                    highest_tier = tier
                break
    return {
        "highest_tier": highest_tier or "low",
        "matched_nets": matched[:20],
    }


def find_differential_pairs(net_names: list[str]) -> list[dict[str, str]]:
    found_pairs: list[dict[str, str]] = []
    used_nets: set[str] = set()
    name_set = set(net_names)
    for name in sorted(net_names):
        if name in used_nets:
            continue
        for pos, neg in DIFF_PATTERNS:
            if name.endswith(pos):
                pair_name = name[: -len(pos)] + neg
                if pair_name in name_set and pair_name not in used_nets:
                    found_pairs.append({"positive": name, "negative": pair_name})
                    used_nets.add(name)
                    used_nets.add(pair_name)
                    break
    return found_pairs


def _is_ground(name: str) -> bool:
    upper = name.upper()
    return any(token in upper for token in GROUND_PATTERNS)


def _is_power(name: str) -> bool:
    upper = name.upper()
    return any(token in upper for token in POWER_PATTERNS) or (name.startswith("+") and any(ch.isdigit() for ch in name))


def analyze_board(pcb_path: str | Path) -> dict[str, Any]:
    resolved = Path(pcb_path).resolve()
    parse_kicad_pcb = _import_parse_kicad_pcb()
    pcb = parse_kicad_pcb(str(resolved))

    copper_layers = detect_copper_layers(resolved)
    named_nets = []
    for net_id, net in sorted(pcb.nets.items()):
        if not net.name:
            continue
        named_nets.append(
            {
                "net_id": net_id,
                "name": net.name,
                "pad_count": len(net.pads),
            }
        )

    net_names = [net["name"] for net in named_nets]
    routed_net_ids = {seg.net_id for seg in pcb.segments if getattr(seg, "net_id", 0)} | {
        via.net_id for via in pcb.vias if getattr(via, "net_id", 0)
    }
    zones = [
        {
            "net_id": getattr(zone, "net_id", None),
            "net_name": getattr(zone, "net_name", None),
            "layer": getattr(zone, "layer", None),
            "layers": getattr(zone, "layers", None),
        }
        for zone in getattr(pcb, "zones", [])
    ]
    zone_net_names = {zone["net_name"] for zone in zones if zone.get("net_name")}

    power_nets = [net for net in named_nets if _is_power(net["name"])]
    ground_nets = [net for net in named_nets if _is_ground(net["name"])]
    diff_pairs = find_differential_pairs(net_names)

    fanout_candidates: list[dict[str, Any]] = []
    footprints_at_origin: list[str] = []
    footprints_outside_board: list[str] = []
    collapsed_positions: dict[tuple[float, float], list[str]] = {}
    board_bounds = pcb.board_info.board_bounds
    for ref, footprint in sorted(pcb.footprints.items()):
        footprint_name = footprint.footprint_name or ""
        pad_count = len(footprint.pads)
        upper_name = footprint_name.upper()
        needs_fanout = any(token in upper_name for token in FANOUT_KEYWORDS) or pad_count > 40
        if needs_fanout:
            fanout_candidates.append(
                {
                    "reference": ref,
                    "footprint": footprint_name,
                    "pad_count": pad_count,
                    "recommended_tool": "run_qfn_fanout" if ("QFN" in upper_name or "QFP" in upper_name) else "run_bga_fanout",
                }
            )
        if abs(float(footprint.x)) <= 0.05 and abs(float(footprint.y)) <= 0.05:
            footprints_at_origin.append(ref)
        if board_bounds:
            min_x, min_y, max_x, max_y = board_bounds
            if footprint.x < min_x or footprint.x > max_x or footprint.y < min_y or footprint.y > max_y:
                footprints_outside_board.append(ref)
        collapsed_key = (round(float(footprint.x), 1), round(float(footprint.y), 1))
        collapsed_positions.setdefault(collapsed_key, []).append(ref)

    unrouted_named_nets = [
        net
        for net in named_nets
        if net["pad_count"] >= 2 and net["net_id"] not in routed_net_ids and net["name"] not in zone_net_names
    ]

    collapsed_groups = [
        {
            "x": key[0],
            "y": key[1],
            "count": len(refs),
            "references": sorted(refs),
        }
        for key, refs in collapsed_positions.items()
        if len(refs) > 1
    ]
    collapsed_groups.sort(key=lambda item: (-item["count"], item["x"], item["y"]))

    collapsed_refs: set[str] = set()
    collapse_threshold = max(3, math.ceil(max(1, len(pcb.footprints)) * 0.4))
    for group in collapsed_groups:
        if group["count"] >= collapse_threshold:
            collapsed_refs.update(group["references"])

    placement_hints = {
        "footprints_at_origin": sorted(footprints_at_origin),
        "footprints_outside_board": sorted(footprints_outside_board),
        "collapsed_groups": collapsed_groups[:10],
        "suggested_refs": sorted(set(footprints_at_origin) | set(footprints_outside_board) | collapsed_refs),
    }
    placement_hints["needs_placement"] = bool(placement_hints["suggested_refs"])

    planning_hints = {
        "has_existing_ground_zone": any(zone.get("net_name") and _is_ground(zone["net_name"]) for zone in zones),
        "has_diff_pairs": bool(diff_pairs),
        "needs_fanout": bool(fanout_candidates),
        "needs_plane_repair_after_routing": bool(zones),
        "suggested_routing_layers": copper_layers or ["F.Cu", "B.Cu"],
        "suggested_power_nets_for_wide_traces": [net["name"] for net in power_nets if net["name"] not in zone_net_names],
    }

    return {
        "pcb_path": str(resolved),
        "board": {
            "total_nets": len(pcb.nets),
            "named_nets": len(named_nets),
            "total_footprints": len(pcb.footprints),
            "total_segments": len(pcb.segments),
            "total_vias": len(pcb.vias),
            "total_zones": len(zones),
            "fresh_board": len(pcb.segments) == 0,
            "copper_layers": copper_layers,
            "board_bounds": board_bounds,
        },
        "zones": zones,
        "nets": named_nets,
        "ground_nets": ground_nets,
        "power_nets": power_nets,
        "differential_pairs": diff_pairs,
        "fanout_candidates": fanout_candidates,
        "unrouted_named_nets": unrouted_named_nets,
        "high_speed_hints": classify_speed(net_names),
        "placement_hints": placement_hints,
        "planning_hints": planning_hints,
    }
