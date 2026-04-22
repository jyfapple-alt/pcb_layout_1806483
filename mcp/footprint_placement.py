from __future__ import annotations

import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ORIGIN_TOLERANCE_MM = 0.05
COLLAPSE_GRID_MM = 0.1
DEFAULT_GRID_STEP_MM = 0.25

GROUND_TOKENS = ("GND", "VSS", "AGND", "DGND", "PGND", "GNDA", "GNDD")
INPUT_POWER_TOKENS = ("VIN", "VBAT", "VBUS", "VCC", "VDD", "PWR", "+5", "+12", "+24", "+3.3", "+1.8", "+2.5")
OUTPUT_POWER_TOKENS = ("VOUT", "VREG", "VSYS", "VCCOUT")
SWITCHING_TOKENS = ("SW", "LX", "PHASE")
CONTROL_TOKENS = ("EN", "FB", "ADJ", "COMP", "SS", "SYNC", "PG", "CTRL", "SCL", "SDA", "TX", "RX", "GPIO", "IO")
CONNECTOR_PREFIXES = {"J", "P", "CN", "X", "K"}

ROLE_PRIORITY = {
    "power_ic": 0,
    "ic": 1,
    "power_inductor": 2,
    "input_capacitor": 3,
    "output_capacitor": 4,
    "decoupling_capacitor": 5,
    "feedback_resistor": 6,
    "resistor": 7,
    "capacitor": 8,
    "input_connector": 9,
    "output_connector": 10,
    "control_connector": 11,
    "connector": 12,
    "transistor": 13,
    "diode": 14,
    "generic": 20,
}

NET_ROLE_WEIGHTS = {
    "switching": 6.0,
    "power_output": 5.0,
    "power_input": 4.0,
    "high_speed": 3.0,
    "control": 2.0,
    "ground": 1.5,
    "signal": 1.0,
}


def _import_runtime() -> dict[str, Any]:
    tools_root = Path(__file__).resolve().parent / "kicad_routing_tools"
    if str(tools_root) not in sys.path:
        sys.path.insert(0, str(tools_root))
    from kicad_parser import get_footprint_bounds, parse_kicad_pcb

    return {
        "get_footprint_bounds": get_footprint_bounds,
        "parse_kicad_pcb": parse_kicad_pcb,
    }


def _alpha_prefix(reference: str) -> str:
    match = re.match(r"[A-Za-z]+", reference or "")
    return match.group(0).upper() if match else ""


def _fmt_mm(value: float) -> str:
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        return "0"
    return text


def _close(a: float, b: float, tolerance: float = 1e-6) -> bool:
    return abs(float(a) - float(b)) <= tolerance


def _snap(value: float, grid_step: float) -> float:
    if grid_step <= 0:
        return float(value)
    return round(float(value) / grid_step) * grid_step


def _distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def _point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    if len(polygon) < 3:
        return True
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _normalize_rotation(rotation: float) -> float:
    normalized = float(rotation) % 360.0
    if normalized > 180.0:
        normalized -= 360.0
    if _close(normalized, -180.0):
        normalized = 180.0
    return normalized


def _rotate_local_point(x: float, y: float, rotation: float) -> tuple[float, float]:
    rad = math.radians(-rotation)
    cos_r = math.cos(rad)
    sin_r = math.sin(rad)
    return (x * cos_r - y * sin_r, x * sin_r + y * cos_r)


def _pad_local_corners(pad: Any, footprint_rotation: float) -> list[tuple[float, float]]:
    relative_rotation = _normalize_rotation(float(getattr(pad, "rotation", 0.0)) - float(footprint_rotation))
    half_w = float(getattr(pad, "size_x", 0.0)) / 2.0
    half_h = float(getattr(pad, "size_y", 0.0)) / 2.0
    corners: list[tuple[float, float]] = []
    for sx in (-half_w, half_w):
        for sy in (-half_h, half_h):
            dx, dy = _rotate_local_point(sx, sy, relative_rotation)
            corners.append((float(getattr(pad, "local_x", 0.0)) + dx, float(getattr(pad, "local_y", 0.0)) + dy))
    return corners


def _local_bbox_from_pads(pads: list[Any], footprint_rotation: float) -> tuple[float, float, float, float]:
    if not pads:
        return (-0.5, -0.5, 0.5, 0.5)

    xs: list[float] = []
    ys: list[float] = []
    for pad in pads:
        corners = _pad_local_corners(pad, footprint_rotation)
        xs.extend(point[0] for point in corners)
        ys.extend(point[1] for point in corners)
    return (min(xs), min(ys), max(xs), max(ys))


def _component_margins(
    local_bbox: tuple[float, float, float, float],
    footprint_name: str,
    component_role: str,
    prefix: str,
) -> tuple[float, float]:
    upper_name = (footprint_name or "").upper()
    bbox_width = max(0.1, local_bbox[2] - local_bbox[0])
    bbox_height = max(0.1, local_bbox[3] - local_bbox[1])
    long_axis_is_x = bbox_width >= bbox_height

    if component_role in {"input_connector", "output_connector", "control_connector", "connector"} or prefix in CONNECTOR_PREFIXES:
        return (1.0, 2.0) if long_axis_is_x else (2.0, 1.0)
    if "AXIAL" in upper_name:
        return (0.5, 2.0) if long_axis_is_x else (2.0, 0.5)
    if "THT" in upper_name:
        return (0.6, 1.2) if long_axis_is_x else (1.2, 0.6)
    if component_role in {"input_capacitor", "output_capacitor", "decoupling_capacitor", "capacitor"}:
        return (0.5, 1.0) if long_axis_is_x else (1.0, 0.5)
    if component_role == "power_inductor":
        return (0.6, 0.8) if long_axis_is_x else (0.8, 0.6)
    if component_role in {"power_ic", "ic"}:
        return (0.5, 0.5)
    return (0.7, 0.7)


def _expand_local_bbox(
    local_bbox: tuple[float, float, float, float],
    margin_x: float,
    margin_y: float,
) -> tuple[float, float, float, float]:
    return (
        local_bbox[0] - margin_x,
        local_bbox[1] - margin_y,
        local_bbox[2] + margin_x,
        local_bbox[3] + margin_y,
    )


def _rotated_local_corners(local_bbox: tuple[float, float, float, float], rotation: float) -> list[tuple[float, float]]:
    min_x, min_y, max_x, max_y = local_bbox
    return [
        _rotate_local_point(min_x, min_y, rotation),
        _rotate_local_point(min_x, max_y, rotation),
        _rotate_local_point(max_x, min_y, rotation),
        _rotate_local_point(max_x, max_y, rotation),
    ]


def _bbox_from_origin(
    origin_x: float,
    origin_y: float,
    local_bbox: tuple[float, float, float, float],
    rotation: float,
) -> tuple[float, float, float, float]:
    points = _rotated_local_corners(local_bbox, rotation)
    xs = [origin_x + point[0] for point in points]
    ys = [origin_y + point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _component_size(
    component: dict[str, Any],
    rotation: float,
) -> tuple[float, float]:
    min_x, min_y, max_x, max_y = _bbox_from_origin(0.0, 0.0, component["local_bbox"], rotation)
    return (max_x - min_x, max_y - min_y)


def _component_bbox(
    component: dict[str, Any],
    x: float,
    y: float,
    rotation: float,
) -> tuple[float, float, float, float]:
    return _bbox_from_origin(x, y, component["local_bbox"], rotation)


def _bbox_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    clearance: float,
    ) -> bool:
    return not (
        first[2] + clearance <= second[0]
        or second[2] + clearance <= first[0]
        or first[3] + clearance <= second[1]
        or second[3] + clearance <= first[1]
    )


def _inside_board(
    board_bounds: tuple[float, float, float, float] | None,
    board_outline: list[tuple[float, float]],
    board_cutouts: list[list[tuple[float, float]]],
    x: float,
    y: float,
) -> bool:
    if board_bounds:
        min_x, min_y, max_x, max_y = board_bounds
        if x < min_x or x > max_x or y < min_y or y > max_y:
            return False
    if board_outline and not _point_in_polygon(x, y, board_outline):
        return False
    for cutout in board_cutouts:
        if cutout and _point_in_polygon(x, y, cutout):
            return False
    return True


def _component_inside_board(
    board_bounds: tuple[float, float, float, float] | None,
    board_outline: list[tuple[float, float]],
    board_cutouts: list[list[tuple[float, float]]],
    component: dict[str, Any],
    x: float,
    y: float,
    rotation: float,
) -> bool:
    checkpoints = [(x, y)] + [(x + dx, y + dy) for dx, dy in _rotated_local_corners(component["local_bbox"], rotation)]
    return all(_inside_board(board_bounds, board_outline, board_cutouts, px, py) for px, py in checkpoints)


def _usable_bounds(
    board_bounds: tuple[float, float, float, float] | None,
    board_margin: float,
) -> tuple[float, float, float, float]:
    if not board_bounds:
        return (-50.0, -50.0, 50.0, 50.0)
    min_x, min_y, max_x, max_y = board_bounds
    usable = (
        min_x + board_margin,
        min_y + board_margin,
        max_x - board_margin,
        max_y - board_margin,
    )
    if usable[0] >= usable[2] or usable[1] >= usable[3]:
        return board_bounds
    return usable


def _net_role(net_name: str, pads: list[Any]) -> str:
    upper_name = (net_name or "").upper()
    pinfunctions = " ".join((getattr(pad, "pinfunction", "") or "").upper() for pad in pads)
    pintypes = " ".join((getattr(pad, "pintype", "") or "").lower() for pad in pads)

    if any(token in upper_name for token in GROUND_TOKENS) or any(token in pinfunctions for token in GROUND_TOKENS):
        return "ground"
    if any(token in upper_name for token in SWITCHING_TOKENS) or any(token in pinfunctions for token in SWITCHING_TOKENS):
        return "switching"
    if any(token in upper_name for token in OUTPUT_POWER_TOKENS):
        return "power_output"
    if any(token in upper_name for token in INPUT_POWER_TOKENS) or ("power_in" in pintypes and any(token in pinfunctions for token in INPUT_POWER_TOKENS)):
        return "power_input"
    if any(token in upper_name for token in CONTROL_TOKENS) or any(token in pinfunctions for token in CONTROL_TOKENS):
        return "control"
    if upper_name.endswith(("_P", "_N", "_DP", "_DN")) or any(token in upper_name for token in ("USB", "ETH", "LVDS", "HDMI", "PCIE")):
        return "high_speed"
    return "signal"


def _component_role(reference: str, footprint_name: str, nets: list[str], net_roles: list[str], pads: list[Any]) -> str:
    prefix = _alpha_prefix(reference)
    upper_footprint = (footprint_name or "").upper()
    upper_pins = " ".join((getattr(pad, "pinfunction", "") or "").upper() for pad in pads)

    if prefix in CONNECTOR_PREFIXES:
        if "power_input" in net_roles:
            return "input_connector"
        if "power_output" in net_roles:
            return "output_connector"
        if "control" in net_roles or "signal" in net_roles:
            return "control_connector"
        return "connector"

    if prefix == "U":
        if {"power_input", "power_output", "switching"} & set(net_roles) or any(token in upper_pins for token in ("VIN", "VI", "SW", "LX", "FB", "EN")):
            return "power_ic"
        return "ic"

    if prefix == "L":
        if "switching" in net_roles or "power_output" in net_roles:
            return "power_inductor"
        return "generic"

    if prefix == "C":
        if "power_input" in net_roles and "ground" in net_roles:
            return "input_capacitor"
        if "power_output" in net_roles and "ground" in net_roles:
            return "output_capacitor"
        if "ground" in net_roles:
            return "decoupling_capacitor"
        return "capacitor"

    if prefix == "R":
        if "control" in net_roles and ("power_output" in net_roles or any("FB" in net.upper() for net in nets)):
            return "feedback_resistor"
        return "resistor"

    if prefix == "Q":
        return "transistor"
    if prefix == "D":
        return "diode"

    if any(token in upper_footprint for token in ("USB", "HEADER", "CONN")):
        return "connector"
    return "generic"


def _build_component_data(pcb_data) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    components: dict[str, dict[str, Any]] = {}
    for reference, footprint in sorted(pcb_data.footprints.items()):
        pads = list(footprint.pads)
        net_names = sorted({pad.net_name for pad in pads if getattr(pad, "net_name", "")})
        net_roles = sorted({_net_role(net_name, [pad for pad in pads if pad.net_name == net_name]) for net_name in net_names})
        prefix = _alpha_prefix(reference)
        component_role = _component_role(reference, footprint.footprint_name, net_names, net_roles, pads)
        local_bbox = _local_bbox_from_pads(pads, float(footprint.rotation))
        margin_x, margin_y = _component_margins(local_bbox, footprint.footprint_name, component_role, prefix)
        expanded_local_bbox = _expand_local_bbox(local_bbox, margin_x, margin_y)
        width, height = _component_size({"local_bbox": expanded_local_bbox}, float(footprint.rotation))
        components[reference] = {
            "reference": reference,
            "footprint_name": footprint.footprint_name,
            "value": footprint.value,
            "x": float(footprint.x),
            "y": float(footprint.y),
            "rotation": float(footprint.rotation),
            "layer": footprint.layer,
            "width": float(width),
            "height": float(height),
            "local_bbox": expanded_local_bbox,
            "outline_margin_x": float(margin_x),
            "outline_margin_y": float(margin_y),
            "pad_count": len(pads),
            "nets": net_names,
            "net_roles": net_roles,
            "pinfunctions": sorted({getattr(pad, "pinfunction", "") for pad in pads if getattr(pad, "pinfunction", "")}),
            "prefix": prefix,
            "component_role": component_role,
        }

    edges: dict[tuple[str, str], dict[str, Any]] = {}
    centrality: dict[str, float] = defaultdict(float)
    for net_id, net in sorted(pcb_data.nets.items()):
        if not net.name:
            continue
        refs = sorted({pad.component_ref for pad in net.pads if getattr(pad, "component_ref", "")})
        if len(refs) < 2:
            continue
        net_role = _net_role(net.name, net.pads)
        weight = NET_ROLE_WEIGHTS.get(net_role, 1.0)
        for left_index, left_ref in enumerate(refs):
            for right_ref in refs[left_index + 1 :]:
                key = tuple(sorted((left_ref, right_ref)))
                edge = edges.setdefault(
                    key,
                    {
                        "from": key[0],
                        "to": key[1],
                        "weight": 0.0,
                        "shared_nets": [],
                        "net_roles": [],
                    },
                )
                edge["weight"] += weight
                edge["shared_nets"].append(net.name)
                edge["net_roles"].append(net_role)
                centrality[left_ref] += weight
                centrality[right_ref] += weight

    edge_list = sorted(
        (
            {
                **edge,
                "shared_nets": sorted(set(edge["shared_nets"])),
                "net_roles": sorted(set(edge["net_roles"])),
            }
            for edge in edges.values()
        ),
        key=lambda item: (-item["weight"], item["from"], item["to"]),
    )

    for reference, component in components.items():
        component["centrality"] = round(float(centrality.get(reference, 0.0)), 3)
        connected = []
        for edge in edge_list:
            if edge["from"] == reference:
                connected.append({"reference": edge["to"], "weight": edge["weight"], "shared_nets": edge["shared_nets"], "net_roles": edge["net_roles"]})
            elif edge["to"] == reference:
                connected.append({"reference": edge["from"], "weight": edge["weight"], "shared_nets": edge["shared_nets"], "net_roles": edge["net_roles"]})
        connected.sort(key=lambda item: (-item["weight"], item["reference"]))
        component["connected_components"] = connected[:12]

    return components, edge_list


def _collapsed_groups(components: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[float, float], list[str]] = defaultdict(list)
    for reference, component in components.items():
        key = (
            round(component["x"] / COLLAPSE_GRID_MM) * COLLAPSE_GRID_MM,
            round(component["y"] / COLLAPSE_GRID_MM) * COLLAPSE_GRID_MM,
        )
        groups[key].append(reference)
    collapsed = [
        {"x": key[0], "y": key[1], "references": sorted(refs), "count": len(refs)}
        for key, refs in groups.items()
        if len(refs) > 1
    ]
    collapsed.sort(key=lambda item: (-item["count"], item["x"], item["y"]))
    return collapsed


def _placement_hints(
    pcb_data,
    components: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    board_bounds = pcb_data.board_info.board_bounds
    board_outline = list(pcb_data.board_info.board_outline or [])
    board_cutouts = list(pcb_data.board_info.board_cutouts or [])
    at_origin: list[str] = []
    outside_board: list[str] = []

    for reference, component in components.items():
        if abs(component["x"]) <= ORIGIN_TOLERANCE_MM and abs(component["y"]) <= ORIGIN_TOLERANCE_MM:
            at_origin.append(reference)
        if not _component_inside_board(
            board_bounds,
            board_outline,
            board_cutouts,
            component,
            component["x"],
            component["y"],
            component["rotation"],
        ):
            outside_board.append(reference)

    collapsed = _collapsed_groups(components)
    total = max(1, len(components))
    collapsed_refs: set[str] = set()
    for group in collapsed:
        if group["count"] >= max(3, math.ceil(total * 0.4)):
            collapsed_refs.update(group["references"])

    suggested_refs = sorted(set(at_origin) | set(outside_board) | collapsed_refs)
    return {
        "footprints_at_origin": sorted(at_origin),
        "footprints_outside_board": sorted(outside_board),
        "collapsed_groups": collapsed[:10],
        "suggested_refs": suggested_refs,
        "needs_placement": bool(suggested_refs),
    }


def build_placement_context(
    pcb_path: str | Path,
    *,
    references: list[str] | None = None,
) -> dict[str, Any]:
    runtime = _import_runtime()
    parse_kicad_pcb = runtime["parse_kicad_pcb"]

    resolved = Path(pcb_path).resolve()
    pcb_data = parse_kicad_pcb(str(resolved))
    components, connections = _build_component_data(pcb_data)
    hints = _placement_hints(pcb_data, components)
    selected_refs = sorted(set(references or []) & set(components)) if references else sorted(components)

    usable = _usable_bounds(pcb_data.board_info.board_bounds, board_margin=0.25)
    board = {
        "bounds": pcb_data.board_info.board_bounds,
        "usable_bounds": usable,
        "outline": list(pcb_data.board_info.board_outline or []),
        "cutouts": list(pcb_data.board_info.board_cutouts or []),
        "copper_layers": list(pcb_data.board_info.copper_layers or []),
        "total_footprints": len(components),
        "total_segments": len(pcb_data.segments),
        "total_vias": len(pcb_data.vias),
        "total_zones": len(getattr(pcb_data, "zones", []) or []),
    }

    return {
        "pcb_path": str(resolved),
        "board": board,
        "placement_hints": hints,
        "footprints": [components[reference] for reference in selected_refs],
        "connections": [
            edge
            for edge in connections
            if edge["from"] in selected_refs or edge["to"] in selected_refs
        ][:200],
        "schema_hint": {
            "placement_plan": {
                "grid_step": DEFAULT_GRID_STEP_MM,
                "placements": [
                    {
                        "reference": "U1",
                        "x": 40.0,
                        "y": 42.0,
                        "rotation": 0.0,
                    }
                ],
            },
            "rules": [
                "Infer placement from board outline, component size, shared nets, and component roles instead of memorizing coordinates.",
                "Treat each placement x/y as the KiCad footprint origin, not the geometric center of the package body.",
                "Keep footprints inside the board outline and outside cutouts.",
                "For elongated or edge-adjacent footprints, choose rotation explicitly so the full footprint envelope stays inside the board.",
                "Avoid footprint overlaps and reserve extra gap for routing channels.",
                "Prefer placing connectors near board edges, power ICs near the center of the power path, and passives close to the IC pins they serve.",
            ],
        },
    }


def _normalize_plan(
    pcb_data,
    placement_plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    placements = placement_plan.get("placements") or []
    if not isinstance(placements, list):
        raise ValueError("placement_plan.placements must be a list.")

    normalized: list[dict[str, Any]] = []
    duplicate_refs: set[str] = set()
    seen: set[str] = set()
    for item in placements:
        if not isinstance(item, dict):
            raise ValueError("Each placement entry must be an object.")
        reference = str(item.get("reference") or "").strip()
        if not reference:
            raise ValueError("Each placement must include a reference.")
        if reference not in pcb_data.footprints:
            raise ValueError(f"Unknown footprint reference: {reference}")
        if reference in seen:
            duplicate_refs.add(reference)
        seen.add(reference)
        footprint = pcb_data.footprints[reference]
        x = float(item.get("x"))
        y = float(item.get("y"))
        rotation = float(item.get("rotation", footprint.rotation))
        normalized.append(
            {
                "reference": reference,
                "x": x,
                "y": y,
                "rotation": rotation,
            }
        )
    return normalized, sorted(duplicate_refs)


def validate_placement_plan(
    pcb_path: str | Path,
    placement_plan: dict[str, Any],
    *,
    placement_gap: float = 1.0,
    board_margin: float = 0.25,
) -> dict[str, Any]:
    runtime = _import_runtime()
    parse_kicad_pcb = runtime["parse_kicad_pcb"]

    resolved = Path(pcb_path).resolve()
    pcb_data = parse_kicad_pcb(str(resolved))
    components, _ = _build_component_data(pcb_data)
    board_bounds = pcb_data.board_info.board_bounds
    board_outline = list(pcb_data.board_info.board_outline or [])
    board_cutouts = list(pcb_data.board_info.board_cutouts or [])
    usable_bounds = _usable_bounds(board_bounds, board_margin)

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    placements, duplicate_refs = _normalize_plan(pcb_data, placement_plan)
    if duplicate_refs:
        errors.append({"reference": None, "message": f"Duplicate placement entries: {', '.join(duplicate_refs)}"})

    planned_by_ref = {item["reference"]: item for item in placements}
    occupied: list[dict[str, Any]] = []

    for reference, component in components.items():
        if reference in planned_by_ref:
            continue
        occupied.append(
            {
                "reference": reference,
                "bbox": _component_bbox(component, component["x"], component["y"], component["rotation"]),
            }
        )

    summaries: list[dict[str, Any]] = []
    for item in placements:
        component = components[item["reference"]]
        width, height = _component_size(component, item["rotation"])
        min_x, min_y, max_x, max_y = _component_bbox(component, item["x"], item["y"], item["rotation"])

        if usable_bounds:
            usable_min_x, usable_min_y, usable_max_x, usable_max_y = usable_bounds
            if min_x < usable_min_x or max_x > usable_max_x or min_y < usable_min_y or max_y > usable_max_y:
                errors.append(
                    {
                        "reference": item["reference"],
                        "message": "Footprint bounding box exceeds the usable board area.",
                    }
                )

        if not _component_inside_board(board_bounds, board_outline, board_cutouts, component, item["x"], item["y"], item["rotation"]):
            errors.append(
                {
                    "reference": item["reference"],
                    "message": "Footprint would fall outside the board outline or into a cutout.",
                }
            )

        current_bbox = (min_x, min_y, max_x, max_y)
        for other in occupied:
            if _bbox_overlap(current_bbox, other["bbox"], placement_gap):
                errors.append(
                    {
                        "reference": item["reference"],
                        "message": f"Footprint overlaps {other['reference']} within {placement_gap}mm clearance.",
                    }
                )

        occupied.append({"reference": item["reference"], "bbox": current_bbox})
        summaries.append(
            {
                "reference": item["reference"],
                "x": item["x"],
                "y": item["y"],
                "rotation": item["rotation"],
                "width": round(width, 3),
                "height": round(height, 3),
            }
        )

    if not placements:
        warnings.append({"reference": None, "message": "No placement entries supplied; plan is a no-op."})
    if pcb_data.segments:
        warnings.append({"reference": None, "message": "Board already contains tracks; moving footprints may invalidate existing copper."})

    return {
        "valid": not errors,
        "pcb_path": str(resolved),
        "placement_gap": placement_gap,
        "board_margin": board_margin,
        "placement_count": len(placements),
        "errors": errors,
        "warnings": warnings,
        "placements": summaries,
    }


def _find_matching_paren(text: str, start_index: int) -> int:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start_index, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("Unbalanced parentheses while scanning KiCad footprint block.")


def _find_top_level_at_span(block_text: str) -> tuple[int, int] | None:
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(block_text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "(":
            if depth == 1 and block_text.startswith("(at", index):
                end_index = _find_matching_paren(block_text, index)
                return index, end_index + 1
            depth += 1
        elif char == ")":
            depth -= 1
    return None


def _rewrite_footprint_positions(content: str, placements_by_ref: dict[str, dict[str, float]]) -> tuple[str, list[str]]:
    replacements: list[tuple[int, int, str]] = []
    found_refs: set[str] = set()

    search_start = 0
    while True:
        block_start = content.find('(footprint "', search_start)
        if block_start < 0:
            break
        block_end = _find_matching_paren(content, block_start)
        block_text = content[block_start : block_end + 1]
        ref_match = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', block_text)
        if ref_match:
            reference = ref_match.group(1)
            if reference in placements_by_ref:
                at_span = _find_top_level_at_span(block_text)
                if at_span is None:
                    raise ValueError(f"Unable to locate top-level (at ...) for footprint {reference}")
                placement = placements_by_ref[reference]
                replacement = f'(at {_fmt_mm(placement["x"])} {_fmt_mm(placement["y"])} {_fmt_mm(placement["rotation"])})'
                replacements.append((block_start + at_span[0], block_start + at_span[1], replacement))
                found_refs.add(reference)
        search_start = block_end + 1

    missing = sorted(set(placements_by_ref) - found_refs)
    updated = content
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        updated = updated[:start] + replacement + updated[end:]
    return updated, missing


def apply_placement_plan(
    pcb_path: str | Path,
    output_path: str | Path,
    placement_plan: dict[str, Any],
    *,
    placement_gap: float = 1.0,
    board_margin: float = 0.25,
) -> dict[str, Any]:
    runtime = _import_runtime()
    parse_kicad_pcb = runtime["parse_kicad_pcb"]

    resolved_input = Path(pcb_path).resolve()
    resolved_output = Path(output_path).resolve()
    validation = validate_placement_plan(
        resolved_input,
        placement_plan,
        placement_gap=placement_gap,
        board_margin=board_margin,
    )
    if not validation["valid"]:
        return {
            "success": False,
            "input_path": str(resolved_input),
            "output_path": str(resolved_output),
            "validation": validation,
            "error": "Placement validation failed.",
        }

    pcb_data = parse_kicad_pcb(str(resolved_input))
    normalized, _ = _normalize_plan(pcb_data, placement_plan)
    placements_by_ref = {item["reference"]: item for item in normalized}
    original = resolved_input.read_text(encoding="utf-8")
    updated, missing = _rewrite_footprint_positions(original, placements_by_ref)
    if missing:
        return {
            "success": False,
            "input_path": str(resolved_input),
            "output_path": str(resolved_output),
            "validation": validation,
            "error": f"Unable to locate footprint blocks for: {', '.join(missing)}",
        }

    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(updated, encoding="utf-8")

    try:
        reparsed = parse_kicad_pcb(str(resolved_output))
    except Exception as exc:
        return {
            "success": False,
            "input_path": str(resolved_input),
            "output_path": str(resolved_output),
            "validation": validation,
            "error": f"Output board could not be reparsed: {exc}",
        }

    return {
        "success": True,
        "input_path": str(resolved_input),
        "output_path": str(resolved_output),
        "placement_count": len(normalized),
        "validation": validation,
        "board_summary": {
            "total_footprints": len(reparsed.footprints),
            "total_segments": len(reparsed.segments),
            "total_vias": len(reparsed.vias),
        },
    }


def _select_references_to_place(
    context: dict[str, Any],
    references: list[str] | None,
    zero_only: bool,
) -> list[str]:
    component_refs = {item["reference"] for item in context["footprints"]}
    if references:
        return sorted({reference for reference in references if reference in component_refs})
    if zero_only:
        hinted = context["placement_hints"].get("suggested_refs") or []
        return sorted(reference for reference in hinted if reference in component_refs)
    return sorted(component_refs)


def _choose_flow(context: dict[str, Any], selected_refs: list[str]) -> str:
    components = {item["reference"]: item for item in context["footprints"]}
    selected = [components[reference] for reference in selected_refs if reference in components]
    if any(item["component_role"] == "input_connector" for item in selected) and any(item["component_role"] == "output_connector" for item in selected):
        return "left_to_right"
    bounds = context["board"].get("usable_bounds") or context["board"].get("bounds")
    if not bounds:
        return "left_to_right"
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    return "left_to_right" if width >= height else "top_to_bottom"


def _component_map_from_context(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["reference"]: item for item in context.get("footprints") or []}


def _primary_anchor(component_map: dict[str, dict[str, Any]], selected_refs: list[str]) -> str | None:
    if not selected_refs:
        return None
    selected = [component_map[reference] for reference in selected_refs if reference in component_map]
    selected.sort(
        key=lambda item: (
            ROLE_PRIORITY.get(item["component_role"], 99),
            -item.get("centrality", 0.0),
            item["reference"],
        )
    )
    for preferred_role in ("power_ic", "ic", "power_inductor"):
        for item in selected:
            if item["component_role"] == preferred_role:
                return item["reference"]
    return selected[0]["reference"]


def _neighbor_summary(
    component: dict[str, Any],
    placed_positions: dict[str, dict[str, float]],
    fixed_components: dict[str, dict[str, Any]],
    component_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    neighbors = []
    for entry in component.get("connected_components") or []:
        reference = entry["reference"]
        if reference in placed_positions:
            neighbors.append(
                {
                    **entry,
                    "x": placed_positions[reference]["x"],
                    "y": placed_positions[reference]["y"],
                    "width": component_map[reference]["width"],
                    "height": component_map[reference]["height"],
                }
            )
        elif reference in fixed_components:
            fixed = fixed_components[reference]
            neighbors.append(
                {
                    **entry,
                    "x": fixed["x"],
                    "y": fixed["y"],
                    "width": fixed["width"],
                    "height": fixed["height"],
                }
            )
    neighbors.sort(key=lambda item: (-item["weight"], item["reference"]))
    return neighbors


def _search_position(
    target_x: float,
    target_y: float,
    *,
    component: dict[str, Any],
    rotation: float,
    occupied_boxes: list[tuple[str, tuple[float, float, float, float]]],
    board_bounds: tuple[float, float, float, float] | None,
    board_outline: list[tuple[float, float]],
    board_cutouts: list[list[tuple[float, float]]],
    usable_bounds: tuple[float, float, float, float],
    placement_gap: float,
    grid_step: float,
) -> tuple[float, float, bool]:
    target_x = _snap(target_x, grid_step)
    target_y = _snap(target_y, grid_step)
    min_x, min_y, max_x, max_y = usable_bounds
    width, height = _component_size(component, rotation)

    max_radius = max(
        8,
        int(max((max_x - min_x) / max(grid_step, 0.1), (max_y - min_y) / max(grid_step, 0.1))),
    )
    for radius in range(0, max_radius + 1):
        candidate_offsets: list[tuple[int, int]] = []
        if radius == 0:
            candidate_offsets.append((0, 0))
        else:
            for dx in range(-radius, radius + 1):
                candidate_offsets.append((dx, -radius))
                candidate_offsets.append((dx, radius))
            for dy in range(-radius + 1, radius):
                candidate_offsets.append((-radius, dy))
                candidate_offsets.append((radius, dy))
        seen_offsets: set[tuple[int, int]] = set()
        for dx, dy in candidate_offsets:
            if (dx, dy) in seen_offsets:
                continue
            seen_offsets.add((dx, dy))
            x = _snap(target_x + dx * grid_step, grid_step)
            y = _snap(target_y + dy * grid_step, grid_step)
            bbox = _component_bbox(component, x, y, rotation)
            if bbox[0] < min_x or bbox[2] > max_x or bbox[1] < min_y or bbox[3] > max_y:
                continue
            if not _component_inside_board(board_bounds, board_outline, board_cutouts, component, x, y, rotation):
                continue
            if any(_bbox_overlap(bbox, other_bbox, placement_gap) for _, other_bbox in occupied_boxes):
                continue
            return x, y, True
    return target_x, target_y, False


def _rotation_candidates(component: dict[str, Any]) -> list[float]:
    base = component["rotation"]
    candidates: list[float] = []
    seen: set[float] = set()
    for offset in (0.0, 90.0, 180.0, 270.0):
        value = _normalize_rotation(base + offset)
        key = round(value, 3)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(value)
    return candidates


def _rotation_penalty(component: dict[str, Any], rotation: float, flow: str) -> float:
    width, height = _component_size(component, rotation)
    role = component["component_role"]
    delta = abs(_normalize_rotation(rotation - component["rotation"]))
    penalty = delta * 0.02

    if role in {"input_connector", "output_connector", "control_connector", "connector", "input_capacitor", "output_capacitor", "decoupling_capacitor", "capacitor"}:
        penalty += (width if flow == "left_to_right" else height) * 0.35
    elif role == "power_inductor":
        if flow == "left_to_right":
            penalty += max(0.0, height - width)
        else:
            penalty += max(0.0, width - height)
    return penalty


def _position_penalty(
    component: dict[str, Any],
    x: float,
    y: float,
    flow: str,
    usable_bounds: tuple[float, float, float, float],
) -> float:
    min_x, min_y, max_x, max_y = usable_bounds
    width = max(1.0, max_x - min_x)
    height = max(1.0, max_y - min_y)
    role = component["component_role"]

    x_norm = (x - min_x) / width
    y_norm = (y - min_y) / height
    penalty = 0.0

    if flow == "left_to_right":
        if role in {"input_connector", "input_capacitor"}:
            penalty += x_norm * 10.0
        elif role in {"output_connector", "output_capacitor"}:
            penalty += (1.0 - x_norm) * 10.0
        elif role == "control_connector":
            penalty += x_norm * 9.0 + y_norm * 3.0
    else:
        if role in {"input_connector", "input_capacitor"}:
            penalty += y_norm * 10.0
        elif role in {"output_connector", "output_capacitor"}:
            penalty += (1.0 - y_norm) * 10.0
        elif role == "control_connector":
            penalty += y_norm * 9.0 + x_norm * 3.0

    return penalty


def _placement_target(
    component: dict[str, Any],
    *,
    neighbors: list[dict[str, Any]],
    anchor: dict[str, Any] | None,
    usable_bounds: tuple[float, float, float, float],
    flow: str,
    placement_gap: float,
) -> tuple[float, float, str]:
    min_x, min_y, max_x, max_y = usable_bounds
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    role = component["component_role"]
    width = component["width"]
    height = component["height"]

    if neighbors:
        total_weight = sum(max(0.5, float(entry["weight"])) for entry in neighbors)
        avg_x = sum(entry["x"] * max(0.5, float(entry["weight"])) for entry in neighbors) / total_weight
        avg_y = sum(entry["y"] * max(0.5, float(entry["weight"])) for entry in neighbors) / total_weight
    elif anchor:
        avg_x = anchor["x"]
        avg_y = anchor["y"]
    else:
        avg_x = center_x
        avg_y = center_y

    neighbor = neighbors[0] if neighbors else anchor
    spacing_x = ((neighbor["width"] if neighbor else width) + width) / 2.0 + placement_gap + 0.8
    spacing_y = ((neighbor["height"] if neighbor else height) + height) / 2.0 + placement_gap + 0.8

    if role == "power_ic":
        if flow == "left_to_right":
            return center_x - (max_x - min_x) * 0.08, center_y, "Place the main power IC near the center of the left-to-right power path."
        return center_x, center_y - (max_y - min_y) * 0.08, "Place the main power IC near the center of the top-to-bottom power path."
    if role == "power_inductor":
        if flow == "left_to_right":
            return avg_x + spacing_x, avg_y, "Place the inductor downstream of the switching IC to shorten the SW current loop."
        return avg_x, avg_y + spacing_y, "Place the inductor downstream of the switching IC along the main power flow."
    if role == "input_capacitor":
        if flow == "left_to_right":
            return avg_x - spacing_x, avg_y, "Place the input capacitor upstream and close to the power IC input pins."
        return avg_x, avg_y - spacing_y, "Place the input capacitor upstream and close to the power IC input pins."
    if role == "output_capacitor":
        if flow == "left_to_right":
            return avg_x + spacing_x, avg_y, "Place the output capacitor close to the regulator output and load path."
        return avg_x, avg_y + spacing_y, "Place the output capacitor close to the regulator output and load path."
    if role == "decoupling_capacitor":
        return avg_x, avg_y - spacing_y, "Place the decoupling capacitor adjacent to the IC it supports."
    if role == "feedback_resistor":
        if flow == "left_to_right":
            return avg_x + spacing_x * 0.6, avg_y - spacing_y * 0.8, "Place the feedback network close to the control IC and away from the noisy switching loop."
        return avg_x + spacing_x * 0.8, avg_y + spacing_y * 0.6, "Place the feedback network close to the control IC and away from the noisy switching loop."
    if role == "input_connector":
        return min_x + width / 2.0, avg_y, "Place the input connector on the board edge near the upstream power entry."
    if role == "output_connector":
        return max_x - width / 2.0, avg_y, "Place the output connector on the opposite edge near the downstream load path."
    if role == "control_connector":
        return min_x + width / 2.0, min_y + height / 2.0 + (center_y - min_y) * 0.4, "Place the control connector on the edge, away from the main switching current loop."
    if role == "connector":
        return min_x + width / 2.0, avg_y, "Place the connector near the board edge close to the components it serves."

    if neighbors:
        return avg_x, avg_y, "Place the component close to the footprints it shares the strongest nets with."
    return center_x, center_y, "Place the component near the board center when there is no stronger connectivity anchor."


def auto_place_footprints(
    pcb_path: str | Path,
    output_path: str | Path,
    *,
    references: list[str] | None = None,
    zero_only: bool = True,
    placement_gap: float = 1.0,
    board_margin: float = 0.25,
    grid_step: float = DEFAULT_GRID_STEP_MM,
) -> dict[str, Any]:
    context = build_placement_context(pcb_path, references=references)
    component_map = _component_map_from_context(context)
    selected_refs = _select_references_to_place(context, references, zero_only)
    if not selected_refs:
        no_op_plan = {"grid_step": grid_step, "placements": []}
        return {
            "success": True,
            "input_path": str(Path(pcb_path).resolve()),
            "output_path": str(Path(output_path).resolve()),
            "placement_plan": no_op_plan,
            "validation": {
                "valid": True,
                "placement_count": 0,
                "errors": [],
                "warnings": [{"reference": None, "message": "No footprints required automatic placement."}],
            },
            "placed_references": [],
            "skipped_references": sorted(component_map),
            "strategy": "heuristic_connectivity",
            "reasoning": [],
        }

    flow = _choose_flow(context, selected_refs)
    board_bounds = context["board"].get("bounds")
    board_outline = list(context["board"].get("outline") or [])
    board_cutouts = list(context["board"].get("cutouts") or [])
    usable_bounds = _usable_bounds(board_bounds, board_margin)

    fixed_components = {
        reference: component
        for reference, component in component_map.items()
        if reference not in selected_refs
    }
    occupied_boxes = [
        (
            reference,
            _component_bbox(component, component["x"], component["y"], component["rotation"]),
        )
        for reference, component in fixed_components.items()
    ]

    anchor_ref = _primary_anchor(component_map, selected_refs)
    placed_positions: dict[str, dict[str, float]] = {}
    reasoning: list[dict[str, str]] = []

    order = sorted(
        selected_refs,
        key=lambda reference: (
            0 if reference == anchor_ref else 1,
            ROLE_PRIORITY.get(component_map[reference]["component_role"], 99),
            -component_map[reference].get("centrality", 0.0),
            reference,
        ),
    )

    for reference in order:
        component = component_map[reference]
        anchor_component = component_map[anchor_ref] if anchor_ref else None
        anchor_position = None
        if anchor_component and anchor_ref in placed_positions:
            anchor_position = {
                **anchor_component,
                **placed_positions[anchor_ref],
            }
        neighbors = _neighbor_summary(component, placed_positions, fixed_components, component_map)
        target_x, target_y, reason = _placement_target(
            component,
            neighbors=neighbors,
            anchor=anchor_position,
            usable_bounds=usable_bounds,
            flow=flow,
            placement_gap=placement_gap,
        )
        best_choice: dict[str, Any] | None = None
        for rotation in _rotation_candidates(component):
            x, y, found = _search_position(
                target_x,
                target_y,
                component=component,
                rotation=rotation,
                occupied_boxes=occupied_boxes,
                board_bounds=board_bounds,
                board_outline=board_outline,
                board_cutouts=board_cutouts,
                usable_bounds=usable_bounds,
                placement_gap=placement_gap,
                grid_step=grid_step,
            )
            if not found:
                continue
            score = (
                _distance(target_x, target_y, x, y)
                + _rotation_penalty(component, rotation, flow)
                + _position_penalty(component, x, y, flow, usable_bounds)
            )
            candidate = {
                "x": x,
                "y": y,
                "rotation": rotation,
                "score": score,
            }
            if best_choice is None or candidate["score"] < best_choice["score"]:
                best_choice = candidate

        if best_choice is None:
            x, y, _ = _search_position(
                target_x,
                target_y,
                component=component,
                rotation=component["rotation"],
                occupied_boxes=occupied_boxes,
                board_bounds=board_bounds,
                board_outline=board_outline,
                board_cutouts=board_cutouts,
                usable_bounds=usable_bounds,
                placement_gap=placement_gap,
                grid_step=grid_step,
            )
            best_choice = {
                "x": x,
                "y": y,
                "rotation": component["rotation"],
            }
        placed_positions[reference] = {
            "x": best_choice["x"],
            "y": best_choice["y"],
            "rotation": best_choice["rotation"],
        }
        occupied_boxes.append(
            (
                reference,
                _component_bbox(component, best_choice["x"], best_choice["y"], best_choice["rotation"]),
            )
        )
        if not _close(best_choice["rotation"], component["rotation"], tolerance=1.0):
            reason = f"{reason} Rotate to {_fmt_mm(best_choice['rotation'])}deg to keep the larger footprint envelope inside the board and reduce edge-normal span."
        reasoning.append({"reference": reference, "reason": reason})

    placement_plan = {
        "grid_step": grid_step,
        "strategy": "heuristic_connectivity",
        "placements": [
            {
                "reference": reference,
                "x": placed_positions[reference]["x"],
                "y": placed_positions[reference]["y"],
                "rotation": placed_positions[reference]["rotation"],
            }
            for reference in order
        ],
    }
    result = apply_placement_plan(
        pcb_path,
        output_path,
        placement_plan,
        placement_gap=placement_gap,
        board_margin=board_margin,
    )
    result["placement_plan"] = placement_plan
    result["placed_references"] = order
    result["skipped_references"] = sorted(reference for reference in component_map if reference not in selected_refs)
    result["strategy"] = "heuristic_connectivity"
    result["reasoning"] = reasoning
    result["flow"] = flow
    return result
