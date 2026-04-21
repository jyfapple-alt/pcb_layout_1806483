from __future__ import annotations

import fnmatch
import math
from pathlib import Path
from typing import Any


def _import_runtime():
    from extract_pcb_geometry import extract_geometry
    from geometry_utils import point_to_segment_distance
    from kicad_parser import parse_kicad_pcb
    from kicad_writer import add_tracks_and_vias_to_pcb
    from routing_config import GridRouteConfig

    return {
        "extract_geometry": extract_geometry,
        "point_to_segment_distance": point_to_segment_distance,
        "parse_kicad_pcb": parse_kicad_pcb,
        "add_tracks_and_vias_to_pcb": add_tracks_and_vias_to_pcb,
        "GridRouteConfig": GridRouteConfig,
    }


def _distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def _board_bounds_contains(board_bounds: tuple[float, float, float, float] | None, x: float, y: float) -> bool:
    if not board_bounds:
        return True
    min_x, min_y, max_x, max_y = board_bounds
    return min_x <= x <= max_x and min_y <= y <= max_y


def _normalize_layers(layers: list[str] | None) -> list[str]:
    if not layers:
        return []
    return [str(layer) for layer in layers]


def _matches_layer(point_layer: str, anchor_layers: list[str] | None) -> bool:
    if not anchor_layers:
        return True
    if point_layer in anchor_layers:
        return True
    return any(layer in {"*.Cu", "*.cu"} for layer in anchor_layers)


def _select_net_ids(
    pcb_data,
    *,
    net_names: list[str] | None = None,
    net_patterns: list[str] | None = None,
) -> set[int]:
    exact = {name for name in (net_names or []) if name}
    patterns = [pattern for pattern in (net_patterns or []) if pattern]
    selected: set[int] = set()

    for net_id, net in pcb_data.nets.items():
        if not net.name:
            continue
        if exact and net.name in exact:
            selected.add(net_id)
            continue
        if patterns and any(fnmatch.fnmatch(net.name, pattern) for pattern in patterns):
            selected.add(net_id)

    if exact or patterns:
        return selected

    return {net_id for net_id, net in pcb_data.nets.items() if net.name}


def _conductor_maps(pcb_data) -> dict[str, Any]:
    pads_by_net: dict[int, list[dict[str, Any]]] = {}
    vias_by_net: dict[int, list[dict[str, Any]]] = {}
    segments_by_net: dict[int, list[dict[str, Any]]] = {}

    for net_id, pads in pcb_data.pads_by_net.items():
        pads_by_net[net_id] = [
            {
                "x": pad.global_x,
                "y": pad.global_y,
                "layers": list(pad.layers),
                "kind": "pad",
                "component": pad.component_ref,
                "pad_number": pad.pad_number,
            }
            for pad in pads
        ]

    for via in pcb_data.vias:
        vias_by_net.setdefault(via.net_id, []).append(
            {
                "x": via.x,
                "y": via.y,
                "layers": list(via.layers),
                "kind": "via",
            }
        )

    for seg in pcb_data.segments:
        segments_by_net.setdefault(seg.net_id, []).append(
            {
                "start": (seg.start_x, seg.start_y),
                "end": (seg.end_x, seg.end_y),
                "layer": seg.layer,
                "width": seg.width,
                "kind": "segment",
            }
        )

    return {
        "pads_by_net": pads_by_net,
        "vias_by_net": vias_by_net,
        "segments_by_net": segments_by_net,
    }


def build_coordinate_context(
    pcb_path: str | Path,
    *,
    net_names: list[str] | None = None,
    net_patterns: list[str] | None = None,
    max_pads_per_net: int = 12,
    max_segments_per_net: int = 20,
    max_vias_per_net: int = 12,
    max_stubs_per_net: int = 12,
) -> dict[str, Any]:
    runtime = _import_runtime()
    parse_kicad_pcb = runtime["parse_kicad_pcb"]
    extract_geometry = runtime["extract_geometry"]
    GridRouteConfig = runtime["GridRouteConfig"]

    resolved = Path(pcb_path).resolve()
    pcb_data = parse_kicad_pcb(str(resolved))
    selected_net_ids = _select_net_ids(pcb_data, net_names=net_names, net_patterns=net_patterns)
    selected_net_names = [pcb_data.nets[net_id].name for net_id in sorted(selected_net_ids)]
    geometry = extract_geometry(pcb_data, selected_net_names or None)
    defaults = GridRouteConfig()

    nets: list[dict[str, Any]] = []
    stubs_by_net: dict[int, list[dict[str, Any]]] = {}
    for stub in geometry["stubs"]:
        stubs_by_net.setdefault(stub["net_id"], []).append(stub)

    for net_id in sorted(selected_net_ids):
        net = pcb_data.nets[net_id]
        if not net.name:
            continue

        pads = [pad for pad in geometry["pads"] if pad["net_id"] == net_id][:max_pads_per_net]
        segments = [seg for seg in geometry["segments"] if seg["net_id"] == net_id][:max_segments_per_net]
        vias = [via for via in geometry["vias"] if via["net_id"] == net_id][:max_vias_per_net]
        stubs = stubs_by_net.get(net_id, [])[:max_stubs_per_net]

        nets.append(
            {
                "net_id": net_id,
                "net_name": net.name,
                "pad_count": len(pcb_data.pads_by_net.get(net_id, [])),
                "segment_count": len([seg for seg in geometry["segments"] if seg["net_id"] == net_id]),
                "via_count": len([via for via in geometry["vias"] if via["net_id"] == net_id]),
                "pads": pads,
                "segments": segments,
                "vias": vias,
                "stubs": stubs,
            }
        )

    return {
        "pcb_path": str(resolved),
        "board": {
            "bounds": pcb_data.board_info.board_bounds,
            "outline_point_count": len(pcb_data.board_info.board_outline),
            "copper_layers": list(pcb_data.board_info.copper_layers),
            "total_nets": len(pcb_data.nets),
            "selected_nets": len(nets),
        },
        "defaults": {
            "track_width": defaults.track_width,
            "clearance": defaults.clearance,
            "via_size": defaults.via_size,
            "via_drill": defaults.via_drill,
            "grid_step": defaults.grid_step,
            "layers": list(defaults.layers),
        },
        "nets": nets,
        "schema_hint": {
            "coordinate_plan": {
                "default_track_width": defaults.track_width,
                "default_via_size": defaults.via_size,
                "default_via_drill": defaults.via_drill,
                "grid_step": defaults.grid_step,
                "routes": [
                    {
                        "net": "NET_NAME",
                        "track_width": defaults.track_width,
                        "points": [
                            {"x": 10.0, "y": 10.0, "layer": "F.Cu"},
                            {"x": 12.0, "y": 10.0, "layer": "F.Cu"},
                            {"x": 12.0, "y": 10.0, "layer": "B.Cu"},
                            {"x": 15.0, "y": 10.0, "layer": "B.Cu"},
                        ],
                    }
                ],
            },
            "rules": [
                "Repeat the same XY with a different layer to request a via.",
                "Keep consecutive same-layer points distinct.",
                "Anchor the first and last point to an existing same-net pad, via, segment, or stub.",
                "Snap coordinates to the routing grid when possible.",
            ],
        },
    }


def validate_coordinate_plan(
    pcb_path: str | Path,
    coordinate_plan: dict[str, Any],
    *,
    endpoint_tolerance: float = 0.2,
    grid_tolerance: float = 0.01,
) -> dict[str, Any]:
    runtime = _import_runtime()
    parse_kicad_pcb = runtime["parse_kicad_pcb"]
    point_to_segment_distance = runtime["point_to_segment_distance"]
    GridRouteConfig = runtime["GridRouteConfig"]

    resolved = Path(pcb_path).resolve()
    pcb_data = parse_kicad_pcb(str(resolved))
    maps = _conductor_maps(pcb_data)
    defaults = GridRouteConfig()
    board_bounds = pcb_data.board_info.board_bounds
    copper_layers = list(pcb_data.board_info.copper_layers) or list(defaults.layers)

    plan_defaults = {
        "track_width": float(coordinate_plan.get("default_track_width", defaults.track_width)),
        "via_size": float(coordinate_plan.get("default_via_size", defaults.via_size)),
        "via_drill": float(coordinate_plan.get("default_via_drill", defaults.via_drill)),
        "grid_step": float(coordinate_plan.get("grid_step", defaults.grid_step)),
    }

    generated_segments_by_net: dict[int, list[dict[str, Any]]] = {}
    generated_vias_by_net: dict[int, list[dict[str, Any]]] = {}
    tracks: list[dict[str, Any]] = []
    vias: list[dict[str, Any]] = []
    route_summaries: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    via_keys: set[tuple[int, float, float, tuple[str, ...]]] = set()

    def add_error(route_index: int, point_index: int | None, message: str) -> None:
        errors.append({"route_index": route_index, "point_index": point_index, "message": message})

    def add_warning(route_index: int, point_index: int | None, message: str) -> None:
        warnings.append({"route_index": route_index, "point_index": point_index, "message": message})

    def on_grid(value: float) -> bool:
        snapped = round(value / plan_defaults["grid_step"]) * plan_defaults["grid_step"]
        return abs(value - snapped) <= grid_tolerance

    def has_anchor(net_id: int, x: float, y: float, layer: str) -> bool:
        for pad in maps["pads_by_net"].get(net_id, []):
            if _matches_layer(layer, pad["layers"]) and _distance(x, y, pad["x"], pad["y"]) <= endpoint_tolerance:
                return True
        for via in maps["vias_by_net"].get(net_id, []):
            if _matches_layer(layer, via["layers"]) and _distance(x, y, via["x"], via["y"]) <= endpoint_tolerance:
                return True
        for via in generated_vias_by_net.get(net_id, []):
            if _matches_layer(layer, via["layers"]) and _distance(x, y, via["x"], via["y"]) <= endpoint_tolerance:
                return True
        for seg in maps["segments_by_net"].get(net_id, []):
            if seg["layer"] == layer and point_to_segment_distance(x, y, seg["start"][0], seg["start"][1], seg["end"][0], seg["end"][1]) <= endpoint_tolerance:
                return True
        for seg in generated_segments_by_net.get(net_id, []):
            if seg["layer"] == layer and point_to_segment_distance(x, y, seg["start"][0], seg["start"][1], seg["end"][0], seg["end"][1]) <= endpoint_tolerance:
                return True
        return False

    routes = coordinate_plan.get("routes") or []
    if not isinstance(routes, list) or not routes:
        add_error(-1, None, "coordinate_plan.routes must be a non-empty list.")

    for route_index, route in enumerate(routes):
        if not isinstance(route, dict):
            add_error(route_index, None, "Each route entry must be an object.")
            continue

        net_name = route.get("net") or route.get("net_name")
        net_id = route.get("net_id")
        if net_id is None:
            if not net_name:
                add_error(route_index, None, "Route must include net or net_id.")
                continue
            matches = [nid for nid, net in pcb_data.nets.items() if net.name == net_name]
            if not matches:
                add_error(route_index, None, f"Unknown net: {net_name}")
                continue
            net_id = matches[0]
        net = pcb_data.nets.get(int(net_id))
        if net is None or not net.name:
            add_error(route_index, None, f"Unknown net_id: {net_id}")
            continue

        points = route.get("points") or []
        if not isinstance(points, list) or len(points) < 2:
            add_error(route_index, None, f"Route for {net.name} must contain at least two points.")
            continue

        route_track_width = float(route.get("track_width", plan_defaults["track_width"]))
        route_via_size = float(route.get("via_size", plan_defaults["via_size"]))
        route_via_drill = float(route.get("via_drill", plan_defaults["via_drill"]))
        normalized_points: list[dict[str, Any]] = []

        for point_index, point in enumerate(points):
            if not isinstance(point, dict):
                add_error(route_index, point_index, "Each point must be an object with x, y, and layer.")
                continue

            if "x" not in point or "y" not in point:
                add_error(route_index, point_index, "Each point must include x and y.")
                continue

            x = float(point["x"])
            y = float(point["y"])
            prev_layer = normalized_points[-1]["layer"] if normalized_points else copper_layers[0]
            layer = str(point.get("layer") or prev_layer)

            if layer not in copper_layers:
                add_error(route_index, point_index, f"Layer {layer} is not a board copper layer.")
                continue

            if not _board_bounds_contains(board_bounds, x, y):
                add_error(route_index, point_index, f"Point ({x}, {y}) lies outside the board bounds.")

            if not on_grid(x) or not on_grid(y):
                add_warning(route_index, point_index, f"Point ({x}, {y}) is off the {plan_defaults['grid_step']}mm grid.")

            normalized_points.append({"x": x, "y": y, "layer": layer})

        if len(normalized_points) < 2:
            continue

        first_point = normalized_points[0]
        last_point = normalized_points[-1]
        if not has_anchor(net.net_id, first_point["x"], first_point["y"], first_point["layer"]):
            add_error(route_index, 0, f"Route start for {net.name} does not anchor to an existing same-net conductor.")
        if not has_anchor(net.net_id, last_point["x"], last_point["y"], last_point["layer"]):
            add_error(route_index, len(normalized_points) - 1, f"Route end for {net.name} does not anchor to an existing same-net conductor.")

        route_track_count = 0
        route_via_count = 0
        for point_index in range(len(normalized_points) - 1):
            start = normalized_points[point_index]
            end = normalized_points[point_index + 1]
            dist = _distance(start["x"], start["y"], end["x"], end["y"])

            if start["layer"] == end["layer"]:
                if dist <= 1e-6:
                    add_error(route_index, point_index, f"Zero-length same-layer segment in {net.name}.")
                    continue
                track = {
                    "start": (start["x"], start["y"]),
                    "end": (end["x"], end["y"]),
                    "width": route_track_width,
                    "layer": start["layer"],
                    "net_id": net.net_id,
                }
                tracks.append(track)
                generated_segments_by_net.setdefault(net.net_id, []).append(track)
                route_track_count += 1
            else:
                if dist > endpoint_tolerance:
                    add_error(route_index, point_index, f"Layer transition in {net.name} must repeat the same XY point for via insertion.")
                    continue

                via_layers = [start["layer"], end["layer"]]
                via_key = (
                    net.net_id,
                    round(start["x"], 4),
                    round(start["y"], 4),
                    tuple(via_layers),
                )
                if via_key not in via_keys:
                    via = {
                        "x": start["x"],
                        "y": start["y"],
                        "size": route_via_size,
                        "drill": route_via_drill,
                        "layers": via_layers,
                        "net_id": net.net_id,
                    }
                    vias.append(via)
                    generated_vias_by_net.setdefault(net.net_id, []).append(via)
                    via_keys.add(via_key)
                    route_via_count += 1

        route_summaries.append(
            {
                "route_index": route_index,
                "net_id": net.net_id,
                "net_name": net.name,
                "track_count": route_track_count,
                "via_count": route_via_count,
                "point_count": len(normalized_points),
            }
        )

    return {
        "valid": not errors,
        "pcb_path": str(resolved),
        "defaults": plan_defaults,
        "route_summaries": route_summaries,
        "tracks": tracks,
        "vias": vias,
        "errors": errors,
        "warnings": warnings,
        "normalized_plan": {
            "routes": route_summaries,
            "track_count": len(tracks),
            "via_count": len(vias),
        },
    }


def apply_coordinate_plan(
    pcb_path: str | Path,
    output_path: str | Path,
    coordinate_plan: dict[str, Any],
    *,
    endpoint_tolerance: float = 0.2,
    grid_tolerance: float = 0.01,
) -> dict[str, Any]:
    runtime = _import_runtime()
    parse_kicad_pcb = runtime["parse_kicad_pcb"]
    add_tracks_and_vias_to_pcb = runtime["add_tracks_and_vias_to_pcb"]

    validation = validate_coordinate_plan(
        pcb_path,
        coordinate_plan,
        endpoint_tolerance=endpoint_tolerance,
        grid_tolerance=grid_tolerance,
    )
    if not validation["valid"]:
        return {
            "success": False,
            "pcb_path": str(Path(pcb_path).resolve()),
            "output_path": str(Path(output_path).resolve()),
            "validation": validation,
        }

    pcb_data = parse_kicad_pcb(str(Path(pcb_path).resolve()))
    output_resolved = Path(output_path).resolve()
    output_resolved.parent.mkdir(parents=True, exist_ok=True)
    wrote = add_tracks_and_vias_to_pcb(
        str(Path(pcb_path).resolve()),
        str(output_resolved),
        validation["tracks"],
        validation["vias"],
        net_id_to_name=getattr(pcb_data, "net_id_to_name", None),
    )

    return {
        "success": bool(wrote),
        "pcb_path": str(Path(pcb_path).resolve()),
        "output_path": str(output_resolved),
        "track_count": len(validation["tracks"]),
        "via_count": len(validation["vias"]),
        "validation": validation,
    }
