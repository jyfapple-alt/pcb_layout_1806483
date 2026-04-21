from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import uuid


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_constraints(constraints: dict[str, Any] | None) -> dict[str, Any]:
    data = deepcopy(constraints or {})
    data.setdefault("route_mode", "balanced")
    data.setdefault("route_diff_pairs_first", True)
    data.setdefault("prefer_existing_zones", True)
    data.setdefault("use_power_planes", None)
    data.setdefault("coordinate_mode", "algorithm_only")
    data.setdefault("layers", None)
    data.setdefault("track_width", None)
    data.setdefault("clearance", None)
    data.setdefault("power_track_width", 0.6)
    data.setdefault("diff_pair_gap", None)
    data.setdefault("max_iterations", 500000 if data["route_mode"] == "balanced" else 1000000)
    data.setdefault("max_ripup", 5 if data["route_mode"] == "balanced" else 10)
    data.setdefault("no_bga_zones", [])
    data.setdefault("notes", "")
    return data


def _artifact_path(session: dict[str, Any], base_name: str, step_index: int, label: str) -> str:
    output_dir = Path(session["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    return str(output_dir / f"{base_name}_step{step_index}_{label}.kicad_pcb")


def build_routing_plan(
    session: dict[str, Any],
    analysis: dict[str, Any],
    *,
    objective: str = "autoroute",
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_constraints(constraints)
    base_name = Path(session["board_path"]).stem
    current_input = session.get("working_board_path") or session["board_path"]
    steps: list[dict[str, Any]] = []
    step_index = 1

    copper_layers = analysis["board"].get("copper_layers") or ["F.Cu", "B.Cu"]
    layers = normalized["layers"] or copper_layers
    existing_zone_names = {zone["net_name"] for zone in analysis.get("zones", []) if zone.get("net_name")}
    fanout_candidates = analysis.get("fanout_candidates", [])
    diff_pairs = analysis.get("differential_pairs", [])
    ground_nets = analysis.get("ground_nets", [])
    power_nets = analysis.get("power_nets", [])
    planning_hints = analysis.get("planning_hints", {})

    use_power_planes = normalized["use_power_planes"]
    if use_power_planes is None:
        use_power_planes = bool(ground_nets) and not planning_hints.get("has_existing_ground_zone", False)

    if use_power_planes and ground_nets:
        plane_nets = [ground_nets[0]["name"]]
        plane_layers = ["B.Cu" if "B.Cu" in layers else layers[-1]]
        output_board = _artifact_path(session, base_name, step_index, "planes")
        steps.append(
            {
                "step_id": f"step-{step_index:02d}-power-planes",
                "kind": "create_power_planes",
                "reason": "Create initial copper plane strategy for the main ground net.",
                "input_board": current_input,
                "output_board": output_board,
                "parameters": {
                    "nets": plane_nets,
                    "plane_layers": plane_layers,
                    "layers": layers,
                    "clearance": normalized["clearance"],
                    "track_width": normalized["track_width"],
                    "power_nets": [net["name"] for net in power_nets] or None,
                    "power_nets_widths": [normalized["power_track_width"]] * len(power_nets) if power_nets else None,
                    "timeout_seconds": 900,
                },
            }
        )
        current_input = output_board
        step_index += 1

    for candidate in fanout_candidates:
        output_board = _artifact_path(session, base_name, step_index, f"fanout_{candidate['reference']}")
        tool_name = candidate["recommended_tool"]
        steps.append(
            {
                "step_id": f"step-{step_index:02d}-{candidate['reference'].lower()}-fanout",
                "kind": tool_name,
                "reason": f"Escape-route dense package {candidate['reference']} before main routing.",
                "input_board": current_input,
                "output_board": output_board,
                "parameters": {
                    "component": candidate["reference"],
                    "layers": layers if tool_name == "run_bga_fanout" else None,
                    "track_width": normalized["track_width"],
                    "clearance": normalized["clearance"],
                    "timeout_seconds": 300,
                },
            }
        )
        current_input = output_board
        step_index += 1

    if normalized["route_diff_pairs_first"] and diff_pairs:
        output_board = _artifact_path(session, base_name, step_index, "diff")
        diff_patterns = sorted({pair["positive"] for pair in diff_pairs} | {pair["negative"] for pair in diff_pairs})
        steps.append(
            {
                "step_id": f"step-{step_index:02d}-diff-route",
                "kind": "route_differential_pairs",
                "reason": "Route differential pairs before general single-ended nets.",
                "input_board": current_input,
                "output_board": output_board,
                "parameters": {
                    "nets": diff_patterns,
                    "layers": layers,
                    "track_width": normalized["track_width"],
                    "clearance": normalized["clearance"],
                    "diff_pair_gap": normalized["diff_pair_gap"],
                    "max_iterations": normalized["max_iterations"],
                    "max_ripup": normalized["max_ripup"],
                    "timeout_seconds": 900,
                },
            }
        )
        current_input = output_board
        step_index += 1

    output_board = _artifact_path(session, base_name, step_index, "signals")
    steps.append(
        {
            "step_id": f"step-{step_index:02d}-single-route",
            "kind": "route_single_ended",
            "reason": "Route remaining single-ended nets using the current board state and user constraints.",
            "input_board": current_input,
            "output_board": output_board,
            "parameters": {
                "nets": ["*"],
                "layers": layers,
                "track_width": normalized["track_width"],
                "clearance": normalized["clearance"],
                "power_nets": planning_hints.get("suggested_power_nets_for_wide_traces") or None,
                "power_nets_widths": (
                    [normalized["power_track_width"]] * len(planning_hints.get("suggested_power_nets_for_wide_traces") or [])
                    if planning_hints.get("suggested_power_nets_for_wide_traces")
                    else None
                ),
                "max_iterations": normalized["max_iterations"],
                "max_ripup": normalized["max_ripup"],
                "no_bga_zones": normalized["no_bga_zones"] or None,
                "timeout_seconds": 900,
            },
        }
    )
    current_input = output_board
    step_index += 1

    if analysis.get("zones"):
        output_board = _artifact_path(session, base_name, step_index, "plane_repair")
        steps.append(
            {
                "step_id": f"step-{step_index:02d}-plane-repair",
                "kind": "repair_disconnected_planes",
                "reason": "Reconnect isolated plane regions after routing changed copper topology.",
                "input_board": current_input,
                "output_board": output_board,
                "parameters": {
                    "layers": layers,
                    "track_width": normalized["track_width"],
                    "clearance": normalized["clearance"],
                    "max_iterations": normalized["max_iterations"],
                    "timeout_seconds": 900,
                },
            }
        )
        current_input = output_board
        step_index += 1

    for kind, reason in [
        ("check_connectivity", "Verify all intended nets are connected."),
        ("check_drc", "Verify routing obeys electrical and clearance rules."),
        ("check_orphan_stubs", "Verify no dangling stubs remain after routing."),
    ]:
        steps.append(
            {
                "step_id": f"step-{step_index:02d}-{kind.replace('_', '-')}",
                "kind": kind,
                "reason": reason,
                "input_board": current_input,
                "output_board": None,
                "parameters": {
                    "timeout_seconds": 120,
                    **({"clearance": normalized["clearance"]} if kind == "check_drc" and normalized["clearance"] is not None else {}),
                },
            }
        )
        step_index += 1

    return {
        "plan_id": f"plan_{uuid.uuid4().hex[:8]}",
        "generated_at": utc_now_iso(),
        "objective": objective,
        "coordinate_mode": normalized["coordinate_mode"],
        "constraints": normalized,
        "analysis_snapshot": {
            "board": analysis["board"],
            "planning_hints": analysis["planning_hints"],
            "fanout_candidate_count": len(fanout_candidates),
            "diff_pair_count": len(diff_pairs),
            "power_net_count": len(power_nets),
        },
        "steps": steps,
    }


def summarize_execution_failures(session: dict[str, Any]) -> dict[str, Any]:
    history = session.get("execution_history") or []
    if not history:
        return {
            "session_id": session["session_id"],
            "status": "no-execution-history",
            "failed_steps": [],
            "next_actions": [],
        }

    latest = history[-1]
    failed_steps: list[dict[str, Any]] = []
    next_actions: list[dict[str, Any]] = []

    for step in latest.get("steps", []):
        result = step.get("result", {})
        json_summary = result.get("json_summary") or {}
        failed_single = json_summary.get("failed_single") or []
        failed_multipoint = json_summary.get("failed_multipoint") or []
        if not result.get("success", True) or failed_single or failed_multipoint:
            failed_steps.append(
                {
                    "step_id": step.get("step_id"),
                    "kind": step.get("kind"),
                    "reason": step.get("reason"),
                    "success": result.get("success"),
                    "returncode": result.get("returncode"),
                    "failed_single": failed_single,
                    "failed_multipoint": failed_multipoint,
                    "stdout_tail": result.get("stdout_tail"),
                    "stderr_tail": result.get("stderr_tail"),
                    "log_path": result.get("log_path"),
                }
            )

            if step.get("kind") in {"route_single_ended", "route_differential_pairs"}:
                next_actions.append(
                    {
                        "action": "retry-routing-with-aggressive-parameters",
                        "target_step_id": step.get("step_id"),
                        "reason": "Routing step reported failures or incomplete connectivity.",
                        "parameter_updates": {
                            "max_ripup": max((step.get("parameters") or {}).get("max_ripup") or 3, 10),
                            "max_iterations": max((step.get("parameters") or {}).get("max_iterations") or 200000, 1000000),
                            "extra_args": ["--no-bga-zones"],
                        },
                    }
                )

            if step.get("kind") == "check_connectivity":
                next_actions.append(
                    {
                        "action": "inspect-connectivity-breaks",
                        "target_step_id": step.get("step_id"),
                        "reason": "Connectivity check failed after routing.",
                    }
                )

    status = "healthy" if not failed_steps else "needs-attention"
    if status == "healthy":
        next_actions.append(
            {
                "action": "finalize",
                "reason": "Latest execution completed without detected failures.",
            }
        )

    return {
        "session_id": session["session_id"],
        "status": status,
        "latest_execution_id": latest.get("execution_id"),
        "failed_steps": failed_steps,
        "next_actions": next_actions,
    }
