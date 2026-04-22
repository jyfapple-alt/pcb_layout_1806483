from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from coordinate_routing import (
    apply_coordinate_plan,
    build_coordinate_context,
    validate_kicad_pcb_file,
    validate_coordinate_plan,
)
from fastmcp import FastMCP
from footprint_placement import (
    apply_placement_plan as apply_footprint_placement_plan,
    auto_place_footprints as auto_place_footprints_heuristic,
    build_placement_context,
    validate_placement_plan,
)
from route_analysis import analyze_board
from route_planner import build_routing_plan, summarize_execution_failures
from routing_session_store import add_note, create_session, list_sessions, load_session, save_session, utc_now_iso

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MCP_ROOT = Path(__file__).resolve().parent
EMBEDDED_TOOLS_ROOT = MCP_ROOT / "kicad_routing_tools"
LOG_ROOT = PROJECT_ROOT / "mcp" / "logs"
SESSION_ROOT = MCP_ROOT / "routing_sessions"
LOG_ROOT.mkdir(parents=True, exist_ok=True)
SESSION_ROOT.mkdir(parents=True, exist_ok=True)

TOOLS_ROOT_SOURCE = "embedded"
tools_root_override = os.environ.get("PCB_ROUTING_TOOLS_ROOT")
if tools_root_override:
    TOOLS_ROOT = Path(tools_root_override).expanduser().resolve()
    TOOLS_ROOT_SOURCE = "env"
else:
    TOOLS_ROOT = EMBEDDED_TOOLS_ROOT

RUST_ROUTER_ROOT = TOOLS_ROOT / "rust_router"

if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))


SCRIPT_PATHS = {
    "build_router.py": TOOLS_ROOT / "build_router.py",
    "list_nets.py": TOOLS_ROOT / "list_nets.py",
    "bga_fanout.py": TOOLS_ROOT / "bga_fanout.py",
    "qfn_fanout.py": TOOLS_ROOT / "qfn_fanout.py",
    "route.py": TOOLS_ROOT / "route.py",
    "route_diff.py": TOOLS_ROOT / "route_diff.py",
    "route_planes.py": TOOLS_ROOT / "route_planes.py",
    "route_disconnected_planes.py": TOOLS_ROOT / "route_disconnected_planes.py",
    "check_connected.py": TOOLS_ROOT / "check_connected.py",
    "check_drc.py": TOOLS_ROOT / "check_drc.py",
    "check_orphan_stubs.py": TOOLS_ROOT / "check_orphan_stubs.py",
}

DEFAULT_SCRIPT_CWDS = {
    "build_router.py": TOOLS_ROOT,
}

COPPER_LAYER_RE = re.compile(r'^\s*\(\s*\d+\s+"([^"]+\.Cu)"\s+signal\)')
FANOUT_KEYWORDS = ("BGA", "PGA", "QFN", "QFP", "LQFP", "TQFP")
HIGH_SPEED_NET_PATTERNS = {
    "ultra_high": ["DDR3", "DDR4", "DDR5", "LPDDR", "PCIE", "SATA", "USB3", "SGMII", "XGMII", "TMDS"],
    "high": ["DDR", "DQ", "DQS", "RGMII", "RMII", "QSPI", "QIO", "SDIO", "LVDS", "HDMI", "USB", "ETH", "ULPI", "EMMC"],
    "medium": ["SPI", "SCK", "SCLK", "MOSI", "MISO", "CLK", "MCLK", "BCLK", "JTAG", "TCK", "SWDIO", "SWCLK", "CAN"],
}

mcp = FastMCP(
    name="pcb-routing-mcp",
    instructions=(
        "Local KiCad PCB routing tools for this repository. "
        "Use these tools to inspect a .kicad_pcb board, build the Rust router, "
        "run fanout or autorouting steps, repair plane zones, and verify DRC/connectivity. "
        "The server uses the embedded runtime under mcp/kicad_routing_tools by default."
    ),
)


def _import_parse_kicad_pcb():
    try:
        from kicad_parser import parse_kicad_pcb
    except ImportError as exc:
        raise RuntimeError(
            f"Unable to import kicad_parser from tools root: {TOOLS_ROOT}. "
            "Check that the embedded routing runtime exists or set PCB_ROUTING_TOOLS_ROOT."
        ) from exc
    return parse_kicad_pcb


def _resolve_path(path: str | None) -> Path | None:
    if path is None:
        return None
    raw = Path(path)
    return raw.resolve(strict=False) if raw.is_absolute() else (PROJECT_ROOT / raw).resolve(strict=False)


def _require_existing_file(path: str) -> Path:
    resolved = _resolve_path(path)
    if resolved is None or not resolved.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return resolved


def _append_value(args: list[str], flag: str, value: Any) -> None:
    if value is None:
        return
    args.extend([flag, str(value)])


def _append_values(args: list[str], flag: str, values: list[Any] | None) -> None:
    if not values:
        return
    args.append(flag)
    args.extend(str(v) for v in values)


def _tail(text: str, max_lines: int = 80) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[-max_lines:])


def _write_log(script_name: str, output: str) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = LOG_ROOT / f"{timestamp}-{script_name.replace('.py', '')}.log"
    log_path.write_text(output, encoding="utf-8")
    return log_path


def _extract_json_summary(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        if line.startswith("JSON_SUMMARY:"):
            payload = line.split("JSON_SUMMARY:", 1)[1].strip()
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return {"raw": payload}
    return None


def _run_script(
    script_name: str,
    args: list[str] | None = None,
    *,
    timeout_seconds: int = 600,
    cwd: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    if script_name not in SCRIPT_PATHS:
        raise ValueError(f"Unsupported script: {script_name}")

    script_path = SCRIPT_PATHS[script_name]
    if not script_path.exists():
        raise FileNotFoundError(
            f"Embedded routing script not found: {script_path}. "
            "Check mcp/kicad_routing_tools or set PCB_ROUTING_TOOLS_ROOT."
        )
    command = [sys.executable, "-X", "utf8", str(script_path)]
    if args:
        command.extend(args)

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra_env:
        env.update(extra_env)

    resolved_cwd = _resolve_path(cwd) if cwd else DEFAULT_SCRIPT_CWDS.get(script_name, PROJECT_ROOT)
    started = time.time()

    try:
        completed = subprocess.run(
            command,
            cwd=resolved_cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            close_fds=True,
            timeout=timeout_seconds,
        )
        duration_seconds = round(time.time() - started, 3)
        combined = completed.stdout
        if completed.stderr:
            combined = f"{combined}\n{completed.stderr}" if combined else completed.stderr
        log_path = _write_log(script_name, combined)
        return {
            "success": completed.returncode == 0,
            "script": script_name,
            "command": command,
            "cwd": str(resolved_cwd),
            "returncode": completed.returncode,
            "duration_seconds": duration_seconds,
            "stdout_tail": _tail(completed.stdout),
            "stderr_tail": _tail(completed.stderr),
            "log_path": str(log_path),
            "json_summary": _extract_json_summary(combined),
        }
    except subprocess.TimeoutExpired as exc:
        duration_seconds = round(time.time() - started, 3)
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        combined = stdout
        if stderr:
            combined = f"{combined}\n{stderr}" if combined else stderr
        log_path = _write_log(script_name, combined)
        return {
            "success": False,
            "script": script_name,
            "command": command,
            "cwd": str(resolved_cwd),
            "returncode": None,
            "duration_seconds": duration_seconds,
            "timed_out": True,
            "stdout_tail": _tail(stdout),
            "stderr_tail": _tail(stderr),
            "log_path": str(log_path),
            "json_summary": _extract_json_summary(combined),
        }


def _detect_copper_layers(pcb_path: Path) -> list[str]:
    copper_layers: list[str] = []
    for line in pcb_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = COPPER_LAYER_RE.match(line)
        if match:
            copper_layers.append(match.group(1))
    return copper_layers


def _classify_speed(net_names: list[str]) -> dict[str, Any]:
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


@mcp.tool(description="Inspect a KiCad PCB and return board summary, copper layers, zones, and likely fanout candidates.")
def inspect_pcb(pcb_path: str) -> dict[str, Any]:
    resolved = _require_existing_file(pcb_path)
    parse_kicad_pcb = _import_parse_kicad_pcb()
    pcb = parse_kicad_pcb(str(resolved))

    fanout_candidates: list[dict[str, Any]] = []
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
                    "recommended_tool": "qfn_fanout.py" if ("QFN" in upper_name or "QFP" in upper_name) else "bga_fanout.py",
                }
            )

    net_names = [net.name for net in pcb.nets.values() if net.name]
    return {
        "pcb_path": str(resolved),
        "total_nets": len(pcb.nets),
        "total_footprints": len(pcb.footprints),
        "total_segments": len(pcb.segments),
        "total_vias": len(pcb.vias),
        "total_zones": len(getattr(pcb, "zones", [])),
        "fresh_board": len(pcb.segments) == 0,
        "copper_layers": _detect_copper_layers(resolved),
        "zones": [
            {
                "net_id": getattr(zone, "net_id", None),
                "net_name": getattr(zone, "net_name", None),
                "layer": getattr(zone, "layer", None),
                "layers": getattr(zone, "layers", None),
            }
            for zone in getattr(pcb, "zones", [])
        ],
        "fanout_candidates": fanout_candidates,
        "high_speed_hints": _classify_speed(net_names),
        "footprints": [
            {
                "reference": ref,
                "footprint": footprint.footprint_name,
                "pad_count": len(footprint.pads),
                "layer": footprint.layer,
            }
            for ref, footprint in sorted(pcb.footprints.items())
        ],
    }


@mcp.tool(description="Report the Python environment, Rust router path, and whether grid_router is importable.")
def router_environment_status() -> dict[str, Any]:
    rust_module_path = RUST_ROUTER_ROOT / "grid_router.pyd"
    status: dict[str, Any] = {
        "python_executable": sys.executable,
        "project_root": str(PROJECT_ROOT),
        "mcp_root": str(MCP_ROOT),
        "embedded_tools_root": str(EMBEDDED_TOOLS_ROOT),
        "tools_root": str(TOOLS_ROOT),
        "tools_root_source": TOOLS_ROOT_SOURCE,
        "tools_root_exists": TOOLS_ROOT.exists(),
        "rust_router_root": str(RUST_ROUTER_ROOT),
        "rust_router_root_exists": RUST_ROUTER_ROOT.exists(),
        "grid_router_module_exists": rust_module_path.exists(),
    }

    if str(RUST_ROUTER_ROOT) not in sys.path:
        sys.path.insert(0, str(RUST_ROUTER_ROOT))

    try:
        import grid_router  # type: ignore

        status["grid_router_importable"] = True
        status["grid_router_version"] = getattr(grid_router, "__version__", "unknown")
        status["grid_router_file"] = getattr(grid_router, "__file__", None)
    except ImportError as exc:
        status["grid_router_importable"] = False
        status["grid_router_error"] = str(exc)

    return status


def _load_session(session_id: str) -> dict[str, Any]:
    return load_session(SESSION_ROOT, session_id)


def _save_session(session: dict[str, Any]) -> dict[str, Any]:
    return save_session(SESSION_ROOT, session)


def _record_artifact(session: dict[str, Any], bucket: str, value: str | None) -> None:
    if not value:
        return
    artifacts = session.setdefault("artifacts", {})
    items = artifacts.setdefault(bucket, [])
    if value not in items:
        items.append(value)


def _result_has_route_failures(result: dict[str, Any]) -> bool:
    summary = result.get("json_summary") or {}
    return bool(summary.get("failed_single") or summary.get("failed_multipoint"))


def _result_requires_attention(step_kind: str, result: dict[str, Any]) -> bool:
    if not result.get("success", False):
        return True
    if step_kind in {"route_single_ended", "route_differential_pairs"}:
        return _result_has_route_failures(result)
    return False


def _invoke_plan_step(
    step_kind: str,
    *,
    input_board: str,
    output_board: str | None,
    parameters: dict[str, Any] | None,
) -> dict[str, Any]:
    params = dict(parameters or {})
    timeout_seconds = int(params.pop("timeout_seconds", 900))

    if step_kind == "auto_place_footprints":
        return auto_place_footprints(
            pcb_path=input_board,
            output_path=output_board,
            **params,
        )
    if step_kind == "create_power_planes":
        return create_power_planes(
            input_pcb=input_board,
            output_pcb=output_board,
            timeout_seconds=timeout_seconds,
            **params,
        )
    if step_kind == "run_bga_fanout":
        return run_bga_fanout(
            pcb_path=input_board,
            output_path=output_board,
            timeout_seconds=timeout_seconds,
            **params,
        )
    if step_kind == "run_qfn_fanout":
        return run_qfn_fanout(
            pcb_path=input_board,
            output_path=output_board,
            timeout_seconds=timeout_seconds,
            **params,
        )
    if step_kind == "route_differential_pairs":
        return route_differential_pairs(
            input_pcb=input_board,
            output_pcb=output_board,
            timeout_seconds=timeout_seconds,
            **params,
        )
    if step_kind == "route_single_ended":
        return route_single_ended(
            input_pcb=input_board,
            output_pcb=output_board,
            timeout_seconds=timeout_seconds,
            **params,
        )
    if step_kind == "repair_disconnected_planes":
        return repair_disconnected_planes(
            input_pcb=input_board,
            output_pcb=output_board,
            timeout_seconds=timeout_seconds,
            **params,
        )
    if step_kind == "check_connectivity":
        return check_connectivity(
            pcb_path=input_board,
            timeout_seconds=timeout_seconds,
            **params,
        )
    if step_kind == "check_drc":
        return check_drc(
            pcb_path=input_board,
            timeout_seconds=timeout_seconds,
            **params,
        )
    if step_kind == "check_orphan_stubs":
        return check_orphan_stubs(
            input_pcb=input_board,
            timeout_seconds=timeout_seconds,
            **params,
        )
    raise ValueError(f"Unsupported plan step kind: {step_kind}")


def _summarize_coordinate_context(context: dict[str, Any] | None) -> dict[str, Any]:
    if not context:
        return {
            "available": False,
            "net_count": 0,
            "net_names": [],
        }

    nets = context.get("nets") or []
    return {
        "available": True,
        "pcb_path": context.get("pcb_path"),
        "board": context.get("board"),
        "defaults": context.get("defaults"),
        "net_count": len(nets),
        "net_names": [net.get("net_name") for net in nets if isinstance(net, dict) and net.get("net_name")],
        "per_net_counts": [
            {
                "net_name": net.get("net_name"),
                "pad_count": net.get("pad_count"),
                "segment_count": net.get("segment_count"),
                "via_count": net.get("via_count"),
                "stub_count": len(net.get("stubs") or []),
            }
            for net in nets
            if isinstance(net, dict)
        ],
        "schema_rules": ((context.get("schema_hint") or {}).get("rules") or []),
    }


def _summarize_placement_context(context: dict[str, Any] | None) -> dict[str, Any]:
    if not context:
        return {
            "available": False,
            "footprint_count": 0,
            "references": [],
        }

    footprints = context.get("footprints") or []
    return {
        "available": True,
        "pcb_path": context.get("pcb_path"),
        "board": context.get("board"),
        "placement_hints": context.get("placement_hints"),
        "footprint_count": len(footprints),
        "references": [item.get("reference") for item in footprints if isinstance(item, dict) and item.get("reference")],
        "roles": [
            {
                "reference": item.get("reference"),
                "role": item.get("component_role"),
                "net_roles": item.get("net_roles"),
                "connected_to": [entry.get("reference") for entry in (item.get("connected_components") or [])[:4] if isinstance(entry, dict)],
            }
            for item in footprints
            if isinstance(item, dict)
        ],
        "schema_rules": ((context.get("schema_hint") or {}).get("rules") or []),
    }


@mcp.tool(description="Create a persistent routing session so the LLM can analyze, plan, execute, and iterate on one board over multiple tool calls.")
def create_routing_session(
    board_path: str,
    session_name: str | None = None,
    output_dir: str | None = None,
    description: str | None = None,
    coordinate_mode: str = "algorithm_only",
    placement_mode: str = "auto",
) -> dict[str, Any]:
    resolved_board = str(_require_existing_file(board_path))
    resolved_output = str(_resolve_path(output_dir)) if output_dir else None
    session = create_session(
        SESSION_ROOT,
        board_path=resolved_board,
        output_dir=resolved_output,
        session_name=session_name,
        description=description,
        coordinate_mode=coordinate_mode,
        placement_mode=placement_mode,
    )
    add_note(session, "Routing session created.")
    session = _save_session(session)
    return session


@mcp.tool(description="List available routing sessions and their current status.")
def list_routing_sessions() -> dict[str, Any]:
    sessions = list_sessions(SESSION_ROOT)
    return {
        "session_count": len(sessions),
        "sessions": sessions,
    }


@mcp.tool(description="Fetch a routing session, including current board path, analysis snapshot, stored plan, and execution history.")
def get_routing_session(session_id: str) -> dict[str, Any]:
    return _load_session(session_id)


@mcp.tool(description="Analyze the current board state for an existing routing session and store a structured LLM-friendly analysis snapshot.")
def analyze_board_for_llm(session_id: str, board_path_override: str | None = None) -> dict[str, Any]:
    session = _load_session(session_id)
    board_path = str(_require_existing_file(board_path_override)) if board_path_override else session.get("working_board_path") or session["board_path"]
    analysis = analyze_board(board_path)
    session["analysis"] = analysis
    session["working_board_path"] = board_path
    session["status"] = "analyzed"
    add_note(session, f"Board analyzed from {board_path}.")
    session = _save_session(session)
    return {
        "session_id": session_id,
        "analysis": analysis,
        "working_board_path": session["working_board_path"],
    }


@mcp.tool(description="Generate a structured routing plan for a session. This is the first LLM-participation layer: objective and constraints become an explicit executable plan.")
def propose_routing_plan(
    session_id: str,
    objective: str = "autoroute",
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = _load_session(session_id)
    analysis = session.get("analysis") or analyze_board(session.get("working_board_path") or session["board_path"])
    session["analysis"] = analysis
    session["objective"] = objective
    session["constraints"] = constraints or {}
    if constraints and constraints.get("coordinate_mode"):
        session["coordinate_mode"] = constraints["coordinate_mode"]
    if constraints and constraints.get("placement_mode"):
        session["placement_mode"] = constraints["placement_mode"]
    plan = build_routing_plan(session, analysis, objective=objective, constraints=constraints)
    session["proposed_plan"] = plan
    session["status"] = "planned"
    add_note(session, f"Routing plan proposed with objective={objective}.")
    session = _save_session(session)
    return {
        "session_id": session_id,
        "working_board_path": session["working_board_path"],
        "analysis_snapshot": plan["analysis_snapshot"],
        "plan": plan,
    }


@mcp.tool(description="Automatically place footprints using board outline, component sizes, net roles, and connectivity heuristics. Best for fresh boards or boards whose footprints were reset to a common origin.")
def auto_place_footprints(
    pcb_path: str,
    output_path: str | None = None,
    references: list[str] | None = None,
    zero_only: bool = True,
    placement_gap: float = 1.0,
    board_margin: float = 0.25,
    grid_step: float = 0.25,
) -> dict[str, Any]:
    resolved_input = str(_require_existing_file(pcb_path))
    resolved_output = (
        str(_resolve_path(output_path))
        if output_path
        else str(Path(resolved_input).with_name(f"{Path(resolved_input).stem}_placed.kicad_pcb"))
    )
    return auto_place_footprints_heuristic(
        resolved_input,
        resolved_output,
        references=references,
        zero_only=zero_only,
        placement_gap=placement_gap,
        board_margin=board_margin,
        grid_step=grid_step,
    )


@mcp.tool(description="Build a structured context for LLM-authored footprint placement. Use this before asking the LLM to choose new footprint coordinates.")
def build_llm_placement_context(
    session_id: str,
    references: list[str] | None = None,
    include_full_context: bool = False,
) -> dict[str, Any]:
    session = _load_session(session_id)
    board_path = session.get("working_board_path") or session["board_path"]
    context = build_placement_context(board_path, references=references)
    session["placement_context"] = context
    if session.get("placement_mode") == "auto":
        session["placement_mode"] = "llm_placement"
    add_note(session, f"Placement context prepared from {board_path}.")
    session = _save_session(session)
    response = {
        "session_id": session_id,
        "working_board_path": board_path,
        "placement_mode": session.get("placement_mode"),
        "context_summary": _summarize_placement_context(context),
        "stored_in_session": True,
    }
    if include_full_context:
        response["context"] = context
    return response


@mcp.tool(description="Fetch the stored LLM placement context for a session. Prefer summary mode unless the LLM needs the full footprint graph and board geometry.")
def get_llm_placement_context(session_id: str, include_full_context: bool = False) -> dict[str, Any]:
    session = _load_session(session_id)
    context = session.get("placement_context")
    response = {
        "session_id": session_id,
        "working_board_path": session.get("working_board_path"),
        "placement_mode": session.get("placement_mode"),
        "context_summary": _summarize_placement_context(context),
        "stored_in_session": bool(context),
    }
    if include_full_context and context:
        response["context"] = context
    return response


@mcp.tool(description="Validate an LLM-authored footprint placement plan without modifying the PCB. The plan should place footprints inside the board outline with adequate spacing.")
def validate_llm_placement_plan(
    session_id: str,
    placement_plan: dict[str, Any],
    placement_gap: float = 1.0,
    board_margin: float = 0.25,
) -> dict[str, Any]:
    session = _load_session(session_id)
    board_path = session.get("working_board_path") or session["board_path"]
    validation = validate_placement_plan(
        board_path,
        placement_plan,
        placement_gap=placement_gap,
        board_margin=board_margin,
    )
    session["latest_placement_validation"] = validation
    add_note(session, f"Placement plan validated against {board_path}.")
    session = _save_session(session)
    return {
        "session_id": session_id,
        "working_board_path": board_path,
        "validation": validation,
    }


@mcp.tool(description="Apply an LLM-authored footprint placement plan to the current session board, then optionally refresh the stored analysis snapshot.")
def apply_llm_placement_plan(
    session_id: str,
    placement_plan: dict[str, Any],
    output_board: str | None = None,
    placement_gap: float = 1.0,
    board_margin: float = 0.25,
    refresh_analysis: bool = True,
) -> dict[str, Any]:
    session = _load_session(session_id)
    board_path = session.get("working_board_path") or session["board_path"]
    output_path = (
        str(_resolve_path(output_board))
        if output_board
        else str(Path(session["output_dir"]) / f"{Path(board_path).stem}_llm_place_{len(session.get('placement_history', [])) + 1}.kicad_pcb")
    )
    result = apply_footprint_placement_plan(
        board_path,
        output_path,
        placement_plan,
        placement_gap=placement_gap,
        board_margin=board_margin,
    )

    file_validation: dict[str, Any] | None = None
    if result.get("success"):
        file_validation = validate_kicad_pcb_file(result["output_path"], use_pcbnew_if_available=False)
        result["file_validation"] = file_validation

    history_entry = {
        "applied_at": utc_now_iso(),
        "input_board_path": board_path,
        "output_board_path": result.get("output_path"),
        "success": result.get("success", False),
        "validation": result.get("validation"),
        "file_validation": file_validation,
    }
    session.setdefault("placement_history", []).append(history_entry)
    session["latest_placement_validation"] = result.get("validation")

    if result.get("success"):
        session["working_board_path"] = result["output_path"]
        session["placement_mode"] = "llm_placement"
        session["status"] = "placed"
        _record_artifact(session, "boards", result["output_path"])
        if refresh_analysis:
            session["analysis"] = analyze_board(result["output_path"])
        add_note(session, f"Applied LLM placement plan to {result['output_path']}.")
    else:
        session["status"] = "placement-validation-failed"
        add_note(session, f"Rejected LLM placement plan for {board_path}.")

    session = _save_session(session)
    return {
        "session_id": session_id,
        "placement_mode": session.get("placement_mode"),
        "working_board_path": session.get("working_board_path"),
        "result": result,
    }


@mcp.tool(description="Automatically place footprints for the current session board and update the session working board. Use this as the default placement step before routing.")
def auto_place_session_footprints(
    session_id: str,
    output_board: str | None = None,
    references: list[str] | None = None,
    zero_only: bool = True,
    placement_gap: float = 1.0,
    board_margin: float = 0.25,
    grid_step: float = 0.25,
    refresh_analysis: bool = True,
) -> dict[str, Any]:
    session = _load_session(session_id)
    board_path = session.get("working_board_path") or session["board_path"]
    output_path = (
        str(_resolve_path(output_board))
        if output_board
        else str(Path(session["output_dir"]) / f"{Path(board_path).stem}_auto_place_{len(session.get('placement_history', [])) + 1}.kicad_pcb")
    )
    result = auto_place_footprints(
        pcb_path=board_path,
        output_path=output_path,
        references=references,
        zero_only=zero_only,
        placement_gap=placement_gap,
        board_margin=board_margin,
        grid_step=grid_step,
    )

    file_validation: dict[str, Any] | None = None
    if result.get("success") and Path(result["output_path"]).exists():
        file_validation = validate_kicad_pcb_file(result["output_path"], use_pcbnew_if_available=False)
        result["file_validation"] = file_validation

    history_entry = {
        "applied_at": utc_now_iso(),
        "input_board_path": board_path,
        "output_board_path": result.get("output_path"),
        "success": result.get("success", False),
        "validation": result.get("validation"),
        "strategy": result.get("strategy"),
        "placed_references": result.get("placed_references"),
        "file_validation": file_validation,
    }
    session.setdefault("placement_history", []).append(history_entry)
    session["latest_placement_validation"] = result.get("validation")

    if result.get("success") and Path(result["output_path"]).exists():
        session["working_board_path"] = result["output_path"]
        session["status"] = "placed"
        _record_artifact(session, "boards", result["output_path"])
        if refresh_analysis:
            session["analysis"] = analyze_board(result["output_path"])
        add_note(session, f"Automatically placed footprints into {result['output_path']}.")
    else:
        session["status"] = "placement-failed"
        add_note(session, f"Automatic footprint placement failed for {board_path}.")

    session = _save_session(session)
    return {
        "session_id": session_id,
        "working_board_path": session.get("working_board_path"),
        "result": result,
    }


@mcp.tool(description="Build a structured geometry context for LLM-authored coordinate routing. Use this before asking the LLM to output explicit track and via coordinates.")
def build_llm_coordinate_context(
    session_id: str,
    nets: list[str] | None = None,
    net_patterns: list[str] | None = None,
    max_pads_per_net: int = 12,
    max_segments_per_net: int = 20,
    max_vias_per_net: int = 12,
    max_stubs_per_net: int = 12,
    include_full_context: bool = False,
) -> dict[str, Any]:
    session = _load_session(session_id)
    board_path = session.get("working_board_path") or session["board_path"]
    context = build_coordinate_context(
        board_path,
        net_names=nets,
        net_patterns=net_patterns,
        max_pads_per_net=max_pads_per_net,
        max_segments_per_net=max_segments_per_net,
        max_vias_per_net=max_vias_per_net,
        max_stubs_per_net=max_stubs_per_net,
    )
    session["coordinate_context"] = context
    if session.get("coordinate_mode") == "algorithm_only":
        session["coordinate_mode"] = "llm_coordinates"
    add_note(session, f"Coordinate routing context prepared from {board_path}.")
    session = _save_session(session)
    response = {
        "session_id": session_id,
        "working_board_path": board_path,
        "coordinate_mode": session["coordinate_mode"],
        "context_summary": _summarize_coordinate_context(context),
        "stored_in_session": True,
    }
    if include_full_context:
        response["context"] = context
    return response


@mcp.tool(description="Fetch the stored coordinate-routing context for a session. Prefer summary mode unless the LLM truly needs the full geometry payload.")
def get_llm_coordinate_context(session_id: str, include_full_context: bool = False) -> dict[str, Any]:
    session = _load_session(session_id)
    context = session.get("coordinate_context")
    response = {
        "session_id": session_id,
        "working_board_path": session.get("working_board_path"),
        "coordinate_mode": session.get("coordinate_mode"),
        "context_summary": _summarize_coordinate_context(context),
        "stored_in_session": bool(context),
    }
    if include_full_context and context:
        response["context"] = context
    return response


@mcp.tool(description="Validate a KiCad PCB file for version-specific net syntax and parser loadability. Optionally uses pcbnew when available.")
def validate_kicad_pcb(
    pcb_path: str,
    use_pcbnew_if_available: bool = False,
) -> dict[str, Any]:
    resolved = _require_existing_file(pcb_path)
    return validate_kicad_pcb_file(resolved, use_pcbnew_if_available=use_pcbnew_if_available)


@mcp.tool(description="Validate an LLM-authored coordinate routing plan without modifying the PCB. The plan should be a list of routes with explicit point coordinates and layers.")
def validate_llm_coordinate_plan(
    session_id: str,
    coordinate_plan: dict[str, Any],
    endpoint_tolerance: float = 0.2,
    grid_tolerance: float = 0.01,
) -> dict[str, Any]:
    session = _load_session(session_id)
    board_path = session.get("working_board_path") or session["board_path"]
    validation = validate_coordinate_plan(
        board_path,
        coordinate_plan,
        endpoint_tolerance=endpoint_tolerance,
        grid_tolerance=grid_tolerance,
    )
    session["latest_coordinate_validation"] = validation
    add_note(session, f"Coordinate plan validated against {board_path}.")
    session = _save_session(session)
    return {
        "session_id": session_id,
        "working_board_path": board_path,
        "validation": validation,
    }


@mcp.tool(description="Apply an LLM-authored coordinate routing plan to the current session board, then optionally run connectivity, DRC, and orphan-stub checks.")
def apply_llm_coordinate_plan(
    session_id: str,
    coordinate_plan: dict[str, Any],
    output_board: str | None = None,
    endpoint_tolerance: float = 0.2,
    grid_tolerance: float = 0.01,
    run_checks: bool = True,
    clearance: float | None = None,
) -> dict[str, Any]:
    session = _load_session(session_id)
    board_path = session.get("working_board_path") or session["board_path"]
    output_path = (
        str(_resolve_path(output_board))
        if output_board
        else str(Path(session["output_dir"]) / f"{Path(board_path).stem}_llm_coords_{len(session.get('coordinate_history', [])) + 1}.kicad_pcb")
    )
    result = apply_coordinate_plan(
        board_path,
        output_path,
        coordinate_plan,
        endpoint_tolerance=endpoint_tolerance,
        grid_tolerance=grid_tolerance,
    )

    checks: dict[str, Any] = {}
    if result.get("success") and run_checks:
        target_nets = [
            route.get("net") or route.get("net_name")
            for route in (coordinate_plan.get("routes") or [])
            if isinstance(route, dict) and (route.get("net") or route.get("net_name"))
        ]
        drc_clearance = clearance
        if drc_clearance is None:
            drc_clearance = (session.get("constraints") or {}).get("clearance")
        checks["check_connectivity"] = check_connectivity(result["output_path"], nets=target_nets or None)
        checks["check_drc"] = check_drc(result["output_path"], clearance=drc_clearance) if drc_clearance is not None else check_drc(result["output_path"])
        checks["check_orphan_stubs"] = check_orphan_stubs(result["output_path"])
        for check_result in checks.values():
            _record_artifact(session, "logs", check_result.get("log_path"))
        session["latest_checks"] = {
            name: {
                "board_path": result["output_path"],
                "checked_at": utc_now_iso(),
                "result": check_result,
            }
            for name, check_result in checks.items()
        }

    history_entry = {
        "applied_at": utc_now_iso(),
        "input_board_path": board_path,
        "output_board_path": result.get("output_path"),
        "success": result.get("success", False),
        "track_count": result.get("track_count", 0),
        "via_count": result.get("via_count", 0),
        "validation": result.get("validation"),
        "checks": checks,
    }
    session.setdefault("coordinate_history", []).append(history_entry)
    session["latest_coordinate_validation"] = result.get("validation")

    if result.get("success"):
        session["working_board_path"] = result["output_path"]
        session["coordinate_mode"] = "llm_coordinates"
        session["status"] = "coordinate-routed"
        _record_artifact(session, "boards", result["output_path"])
        add_note(session, f"Applied LLM coordinate plan to {result['output_path']}.")
    else:
        session["status"] = "coordinate-validation-failed"
        add_note(session, f"Rejected LLM coordinate plan for {board_path}.")

    session = _save_session(session)
    return {
        "session_id": session_id,
        "coordinate_mode": session["coordinate_mode"],
        "working_board_path": session.get("working_board_path"),
        "result": result,
        "checks": checks,
    }


@mcp.tool(description="Run one supported KiCad routing CLI script with raw arguments. Use for uncommon flags not covered by dedicated tools.")
def run_routing_script(
    script_name: str,
    args: list[str] | None = None,
    timeout_seconds: int = 600,
    cwd: str | None = None,
) -> dict[str, Any]:
    return _run_script(script_name, args or [], timeout_seconds=timeout_seconds, cwd=cwd)


@mcp.tool(description="Build or clean the Rust router module via build_router.py.")
def build_rust_router(clean: bool = False, timeout_seconds: int = 900) -> dict[str, Any]:
    args: list[str] = []
    if clean:
        args.append("--clean")
    return _run_script(
        "build_router.py",
        args,
        timeout_seconds=timeout_seconds,
        cwd=str(TOOLS_ROOT),
        extra_env={"PYO3_PYTHON": sys.executable},
    )


@mcp.tool(description="Run list_nets.py to inspect board nets, power nets, diff pairs, or a component's pad mapping.")
def list_nets(
    pcb_path: str,
    component: str | None = None,
    pads: bool = False,
    diff_pairs: bool = False,
    power: bool = False,
    top: int = 10,
    pattern: str | None = None,
    extra_args: list[str] | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    args = [str(_require_existing_file(pcb_path))]
    _append_value(args, "--component", component)
    if pads:
        args.append("--pads")
    if diff_pairs:
        args.append("--diff-pairs")
    if power:
        args.append("--power")
    _append_value(args, "--top", top)
    _append_value(args, "--pattern", pattern)
    if extra_args:
        args.extend(extra_args)
    return _run_script("list_nets.py", args, timeout_seconds=timeout_seconds)


@mcp.tool(description="Run bga_fanout.py to create escape routing for BGA or PGA components.")
def run_bga_fanout(
    pcb_path: str,
    output_path: str | None = None,
    component: str | None = None,
    layers: list[str] | None = None,
    nets: list[str] | None = None,
    diff_pairs: list[str] | None = None,
    track_width: float | None = None,
    clearance: float | None = None,
    via_size: float | None = None,
    via_drill: float | None = None,
    diff_pair_gap: float | None = None,
    exit_margin: float | None = None,
    primary_escape: str | None = None,
    force_escape_direction: bool = False,
    rebalance_escape: bool = False,
    check_for_previous: bool = False,
    no_inner_top_layer: bool = False,
    extra_args: list[str] | None = None,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    args = [str(_require_existing_file(pcb_path))]
    _append_value(args, "--output", str(_resolve_path(output_path)) if output_path else None)
    _append_value(args, "--component", component)
    _append_values(args, "--layers", layers)
    _append_value(args, "--track-width", track_width)
    _append_value(args, "--clearance", clearance)
    _append_value(args, "--via-size", via_size)
    _append_value(args, "--via-drill", via_drill)
    _append_values(args, "--nets", nets)
    _append_values(args, "--diff-pairs", diff_pairs)
    _append_value(args, "--diff-pair-gap", diff_pair_gap)
    _append_value(args, "--exit-margin", exit_margin)
    _append_value(args, "--primary-escape", primary_escape)
    if force_escape_direction:
        args.append("--force-escape-direction")
    if rebalance_escape:
        args.append("--rebalance-escape")
    if check_for_previous:
        args.append("--check-for-previous")
    if no_inner_top_layer:
        args.append("--no-inner-top-layer")
    if extra_args:
        args.extend(extra_args)
    return _run_script("bga_fanout.py", args, timeout_seconds=timeout_seconds)


@mcp.tool(description="Run qfn_fanout.py to generate stub fanout for QFN or QFP style packages.")
def run_qfn_fanout(
    pcb_path: str,
    output_path: str | None = None,
    component: str | None = None,
    layer: str | None = None,
    width: float | None = None,
    extension: float | None = None,
    nets: list[str] | None = None,
    extra_args: list[str] | None = None,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    args = [str(_require_existing_file(pcb_path))]
    _append_value(args, "--output", str(_resolve_path(output_path)) if output_path else None)
    _append_value(args, "--component", component)
    _append_value(args, "--layer", layer)
    _append_value(args, "--width", width)
    _append_value(args, "--extension", extension)
    _append_values(args, "--nets", nets)
    if extra_args:
        args.extend(extra_args)
    return _run_script("qfn_fanout.py", args, timeout_seconds=timeout_seconds)


@mcp.tool(description="Run route.py for single-ended autorouting with common options and optional passthrough flags.")
def route_single_ended(
    input_pcb: str,
    output_pcb: str | None = None,
    nets: list[str] | None = None,
    component: str | None = None,
    ordering: str | None = None,
    direction: str | None = None,
    layers: list[str] | None = None,
    no_bga_zones: list[str] | None = None,
    track_width: float | None = None,
    impedance: float | None = None,
    clearance: float | None = None,
    via_size: float | None = None,
    via_drill: float | None = None,
    power_nets: list[str] | None = None,
    power_nets_widths: list[float] | None = None,
    grid_step: float | None = None,
    max_iterations: int | None = None,
    max_ripup: int | None = None,
    add_teardrops: bool = False,
    debug_lines: bool = False,
    verbose: bool = False,
    extra_args: list[str] | None = None,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    args = [str(_require_existing_file(input_pcb))]
    if output_pcb:
        args.append(str(_resolve_path(output_pcb)))
    _append_values(args, "--nets", nets)
    _append_value(args, "--component", component)
    _append_value(args, "--ordering", ordering)
    _append_value(args, "--direction", direction)
    _append_values(args, "--no-bga-zones", no_bga_zones)
    _append_values(args, "--layers", layers)
    _append_value(args, "--track-width", track_width)
    _append_value(args, "--impedance", impedance)
    _append_value(args, "--clearance", clearance)
    _append_value(args, "--via-size", via_size)
    _append_value(args, "--via-drill", via_drill)
    _append_values(args, "--power-nets", power_nets)
    _append_values(args, "--power-nets-widths", power_nets_widths)
    _append_value(args, "--grid-step", grid_step)
    _append_value(args, "--max-iterations", max_iterations)
    _append_value(args, "--max-ripup", max_ripup)
    if add_teardrops:
        args.append("--add-teardrops")
    if debug_lines:
        args.append("--debug-lines")
    if verbose:
        args.append("--verbose")
    if extra_args:
        args.extend(extra_args)
    return _run_script("route.py", args, timeout_seconds=timeout_seconds)


@mcp.tool(description="Run route_diff.py for differential pair routing with common options and optional passthrough flags.")
def route_differential_pairs(
    input_pcb: str,
    output_pcb: str | None = None,
    nets: list[str] | None = None,
    ordering: str | None = None,
    direction: str | None = None,
    layers: list[str] | None = None,
    no_bga_zones: list[str] | None = None,
    track_width: float | None = None,
    impedance: float | None = None,
    clearance: float | None = None,
    via_size: float | None = None,
    via_drill: float | None = None,
    diff_pair_gap: float | None = None,
    max_iterations: int | None = None,
    max_ripup: int | None = None,
    diff_pair_intra_match: bool = False,
    no_gnd_vias: bool = False,
    add_teardrops: bool = False,
    debug_lines: bool = False,
    verbose: bool = False,
    extra_args: list[str] | None = None,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    args = [str(_require_existing_file(input_pcb))]
    if output_pcb:
        args.append(str(_resolve_path(output_pcb)))
    _append_values(args, "--nets", nets)
    _append_value(args, "--ordering", ordering)
    _append_value(args, "--direction", direction)
    _append_values(args, "--no-bga-zones", no_bga_zones)
    _append_values(args, "--layers", layers)
    _append_value(args, "--track-width", track_width)
    _append_value(args, "--impedance", impedance)
    _append_value(args, "--clearance", clearance)
    _append_value(args, "--via-size", via_size)
    _append_value(args, "--via-drill", via_drill)
    _append_value(args, "--diff-pair-gap", diff_pair_gap)
    _append_value(args, "--max-iterations", max_iterations)
    _append_value(args, "--max-ripup", max_ripup)
    if diff_pair_intra_match:
        args.append("--diff-pair-intra-match")
    if no_gnd_vias:
        args.append("--no-gnd-vias")
    if add_teardrops:
        args.append("--add-teardrops")
    if debug_lines:
        args.append("--debug-lines")
    if verbose:
        args.append("--verbose")
    if extra_args:
        args.extend(extra_args)
    return _run_script("route_diff.py", args, timeout_seconds=timeout_seconds)


@mcp.tool(description="Run route_planes.py to create copper planes or add GND return vias.")
def create_power_planes(
    input_pcb: str,
    output_pcb: str | None = None,
    nets: list[str] | None = None,
    plane_layers: list[str] | None = None,
    layers: list[str] | None = None,
    track_width: float | None = None,
    clearance: float | None = None,
    zone_clearance: float | None = None,
    via_size: float | None = None,
    via_drill: float | None = None,
    grid_step: float | None = None,
    power_nets: list[str] | None = None,
    power_nets_widths: list[float] | None = None,
    add_gnd_vias: bool = False,
    gnd_via_net: str | None = None,
    gnd_via_distance: float | None = None,
    rip_blocker_nets: bool = False,
    reroute_ripped_nets: bool = False,
    add_teardrops: bool = False,
    debug_lines: bool = False,
    verbose: bool = False,
    extra_args: list[str] | None = None,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    args = [str(_require_existing_file(input_pcb))]
    if output_pcb:
        args.append(str(_resolve_path(output_pcb)))
    _append_values(args, "--nets", nets)
    _append_values(args, "--plane-layers", plane_layers)
    _append_values(args, "--layers", layers)
    _append_value(args, "--track-width", track_width)
    _append_value(args, "--clearance", clearance)
    _append_value(args, "--zone-clearance", zone_clearance)
    _append_value(args, "--via-size", via_size)
    _append_value(args, "--via-drill", via_drill)
    _append_value(args, "--grid-step", grid_step)
    _append_values(args, "--power-nets", power_nets)
    _append_values(args, "--power-nets-widths", power_nets_widths)
    if add_gnd_vias:
        args.append("--add-gnd-vias")
    _append_value(args, "--gnd-via-net", gnd_via_net)
    _append_value(args, "--gnd-via-distance", gnd_via_distance)
    if rip_blocker_nets:
        args.append("--rip-blocker-nets")
    if reroute_ripped_nets:
        args.append("--reroute-ripped-nets")
    if add_teardrops:
        args.append("--add-teardrops")
    if debug_lines:
        args.append("--debug-lines")
    if verbose:
        args.append("--verbose")
    if extra_args:
        args.extend(extra_args)
    return _run_script("route_planes.py", args, timeout_seconds=timeout_seconds)


@mcp.tool(description="Run route_disconnected_planes.py to reconnect isolated copper plane regions.")
def repair_disconnected_planes(
    input_pcb: str,
    output_pcb: str | None = None,
    nets: list[str] | None = None,
    plane_layers: list[str] | None = None,
    layers: list[str] | None = None,
    track_width: float | None = None,
    clearance: float | None = None,
    zone_clearance: float | None = None,
    via_size: float | None = None,
    via_drill: float | None = None,
    grid_step: float | None = None,
    max_iterations: int | None = None,
    dry_run: bool = False,
    debug_lines: bool = False,
    verbose: bool = False,
    extra_args: list[str] | None = None,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    args = [str(_require_existing_file(input_pcb))]
    if output_pcb:
        args.append(str(_resolve_path(output_pcb)))
    _append_values(args, "--nets", nets)
    _append_values(args, "--plane-layers", plane_layers)
    _append_values(args, "--layers", layers)
    _append_value(args, "--track-width", track_width)
    _append_value(args, "--clearance", clearance)
    _append_value(args, "--zone-clearance", zone_clearance)
    _append_value(args, "--via-size", via_size)
    _append_value(args, "--via-drill", via_drill)
    _append_value(args, "--grid-step", grid_step)
    _append_value(args, "--max-iterations", max_iterations)
    if dry_run:
        args.append("--dry-run")
    if debug_lines:
        args.append("--debug-lines")
    if verbose:
        args.append("--verbose")
    if extra_args:
        args.extend(extra_args)
    return _run_script("route_disconnected_planes.py", args, timeout_seconds=timeout_seconds)


@mcp.tool(description="Run check_connected.py to verify PCB connectivity after routing.")
def check_connectivity(
    pcb_path: str,
    nets: list[str] | None = None,
    component: str | None = None,
    tolerance: float | None = None,
    quiet: bool = False,
    verbose: bool = False,
    routed_only: bool = False,
    extra_args: list[str] | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    args = [str(_require_existing_file(pcb_path))]
    _append_values(args, "--nets", nets)
    _append_value(args, "--component", component)
    _append_value(args, "--tolerance", tolerance)
    if quiet:
        args.append("--quiet")
    if verbose:
        args.append("--verbose")
    if routed_only:
        args.append("--routed-only")
    if extra_args:
        args.extend(extra_args)
    return _run_script("check_connected.py", args, timeout_seconds=timeout_seconds)


@mcp.tool(description="Run check_drc.py to verify clearances and other DRC conditions.")
def check_drc(
    pcb_path: str,
    clearance: float | None = None,
    hole_to_hole_clearance: float | None = None,
    board_edge_clearance: float | None = None,
    clearance_margin: float | None = None,
    nets: list[str] | None = None,
    debug_lines: bool = False,
    quiet: bool = False,
    extra_args: list[str] | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    args = [str(_require_existing_file(pcb_path))]
    _append_value(args, "--clearance", clearance)
    _append_value(args, "--hole-to-hole-clearance", hole_to_hole_clearance)
    _append_value(args, "--board-edge-clearance", board_edge_clearance)
    _append_value(args, "--clearance-margin", clearance_margin)
    _append_values(args, "--nets", nets)
    if debug_lines:
        args.append("--debug-lines")
    if quiet:
        args.append("--quiet")
    if extra_args:
        args.extend(extra_args)
    return _run_script("check_drc.py", args, timeout_seconds=timeout_seconds)


@mcp.tool(description="Run check_orphan_stubs.py to detect dangling or orphaned trace stubs.")
def check_orphan_stubs(
    input_pcb: str,
    compare_file: str | None = None,
    net: str | None = None,
    layer: str | None = None,
    compare: bool = False,
    extra_args: list[str] | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    args = [str(_require_existing_file(input_pcb))]
    if compare_file:
        args.append(str(_require_existing_file(compare_file)))
    _append_value(args, "--net", net)
    _append_value(args, "--layer", layer)
    if compare:
        args.append("--compare")
    if extra_args:
        args.extend(extra_args)
    return _run_script("check_orphan_stubs.py", args, timeout_seconds=timeout_seconds)


@mcp.tool(description="Execute a stored routing plan step by step inside a routing session and persist the resulting board state, logs, and latest check results.")
def apply_routing_plan(
    session_id: str,
    plan: dict[str, Any] | None = None,
    stop_after_step: int | None = None,
    continue_on_error: bool = False,
) -> dict[str, Any]:
    session = _load_session(session_id)
    active_plan = plan or session.get("proposed_plan")
    if not active_plan:
        raise ValueError(f"Session {session_id} does not have a proposed plan to execute.")

    if plan is not None:
        session["proposed_plan"] = active_plan

    execution_id = f"exec_{uuid.uuid4().hex[:8]}"
    current_board = str(_require_existing_file(session.get("working_board_path") or session["board_path"]))
    execution = {
        "execution_id": execution_id,
        "plan_id": active_plan.get("plan_id"),
        "objective": active_plan.get("objective"),
        "started_at": utc_now_iso(),
        "completed_at": None,
        "status": "running",
        "initial_board_path": current_board,
        "final_board_path": current_board,
        "steps": [],
    }

    truncated = False
    failed = False

    try:
        for index, step in enumerate(active_plan.get("steps", []), start=1):
            if stop_after_step is not None and index > stop_after_step:
                truncated = True
                break

            step_kind = step["kind"]
            input_board = current_board
            output_board = step.get("output_board")
            parameters = dict(step.get("parameters") or {})

            step_record = {
                "index": index,
                "step_id": step.get("step_id"),
                "kind": step_kind,
                "reason": step.get("reason"),
                "input_board": input_board,
                "output_board": output_board,
                "parameters": parameters,
                "started_at": utc_now_iso(),
                "completed_at": None,
                "status": "running",
                "result": None,
            }

            result = _invoke_plan_step(
                step_kind,
                input_board=input_board,
                output_board=output_board,
                parameters=parameters,
            )

            step_record["result"] = result
            step_record["completed_at"] = utc_now_iso()
            step_record["status"] = "failed" if _result_requires_attention(step_kind, result) else "completed"
            execution["steps"].append(step_record)

            _record_artifact(session, "logs", result.get("log_path"))
            if output_board and Path(output_board).exists():
                _record_artifact(session, "boards", str(Path(output_board).resolve()))

            if step_kind in {"check_connectivity", "check_drc", "check_orphan_stubs"}:
                session.setdefault("latest_checks", {})[step_kind] = {
                    "board_path": input_board,
                    "checked_at": step_record["completed_at"],
                    "result": result,
                }

            if result.get("success") and output_board and Path(output_board).exists():
                current_board = str(Path(output_board).resolve())
                if step_kind == "auto_place_footprints":
                    session["analysis"] = analyze_board(current_board)

            if _result_requires_attention(step_kind, result):
                failed = True
                if not continue_on_error:
                    break
    except Exception as exc:
        failed = True
        execution["steps"].append(
            {
                "index": len(execution["steps"]) + 1,
                "step_id": "internal-error",
                "kind": "internal",
                "reason": "Unhandled exception during plan execution.",
                "input_board": current_board,
                "output_board": None,
                "parameters": {},
                "started_at": utc_now_iso(),
                "completed_at": utc_now_iso(),
                "status": "failed",
                "result": {
                    "success": False,
                    "error": str(exc),
                },
            }
        )
    finally:
        execution["completed_at"] = utc_now_iso()
        execution["final_board_path"] = current_board

        if failed:
            execution["status"] = "failed"
            session["status"] = "failed"
            add_note(session, f"Plan execution {execution_id} completed with failures.")
        elif truncated:
            execution["status"] = "partial"
            session["status"] = "partial"
            add_note(session, f"Plan execution {execution_id} stopped after step {stop_after_step}.")
        else:
            execution["status"] = "completed"
            session["status"] = "executed"
            add_note(session, f"Plan execution {execution_id} completed successfully.")

        session["working_board_path"] = current_board
        session.setdefault("execution_history", []).append(execution)
        session = _save_session(session)

    return {
        "session_id": session_id,
        "execution_id": execution_id,
        "status": execution["status"],
        "working_board_path": session["working_board_path"],
        "executed_steps": len(execution["steps"]),
        "plan_id": active_plan.get("plan_id"),
        "latest_checks": session.get("latest_checks", {}),
        "last_step": execution["steps"][-1] if execution["steps"] else None,
    }


@mcp.tool(description="Summarize the latest execution failures for a routing session so the LLM can decide how to retry or adjust the plan.")
def analyze_session_failures(session_id: str) -> dict[str, Any]:
    session = _load_session(session_id)
    summary = summarize_execution_failures(session)
    summary["working_board_path"] = session.get("working_board_path")
    summary["coordinate_mode"] = session.get("coordinate_mode")
    summary["placement_mode"] = session.get("placement_mode")
    return summary


@mcp.tool(description="Suggest the next routing actions for a session using stored analysis, current board state, and the latest execution outcomes.")
def suggest_next_routing_actions(session_id: str) -> dict[str, Any]:
    session = _load_session(session_id)
    summary = summarize_execution_failures(session)
    suggestions = list(summary.get("next_actions") or [])
    analysis = session.get("analysis") or {}
    placement_hints = analysis.get("placement_hints") or {}

    if not session.get("analysis"):
        suggestions.insert(
            0,
            {
                "action": "analyze-board",
                "reason": "This session has no stored analysis snapshot yet.",
            },
        )
    elif placement_hints.get("needs_placement") and not session.get("placement_history"):
        suggestions.insert(
            0,
            {
                "action": "auto-place-footprints",
                "reason": "The board analysis indicates zeroed or out-of-bounds footprints; place them before routing.",
                "references": placement_hints.get("suggested_refs"),
            },
        )
    elif not session.get("proposed_plan"):
        suggestions.insert(
            0,
            {
                "action": "propose-plan",
                "reason": "The board has analysis data but no executable routing plan yet.",
            },
        )

    if session.get("coordinate_mode") == "llm_coordinates":
        if not session.get("coordinate_context"):
            suggestions.insert(
                0,
                {
                    "action": "build-coordinate-context",
                    "reason": "LLM coordinate mode is active but no geometry context snapshot has been prepared yet.",
                },
            )
        if not session.get("coordinate_history"):
            suggestions.insert(
                1,
                {
                    "action": "validate-or-apply-coordinate-plan",
                    "reason": "No LLM-authored coordinate plan has been attempted in this session yet.",
                },
            )
    elif session.get("coordinate_mode") != "algorithm_only":
        suggestions.append(
            {
                "action": "review-coordinate-mode",
                "reason": "This session is marked for a future non-default coordinate workflow. The current MVP still executes with the algorithmic router.",
                "coordinate_mode": session.get("coordinate_mode"),
            }
        )

    if session.get("placement_mode") == "llm_placement":
        if not session.get("placement_context"):
            suggestions.insert(
                0,
                {
                    "action": "build-placement-context",
                    "reason": "LLM placement mode is active but no placement context snapshot has been prepared yet.",
                },
            )
        if not session.get("placement_history"):
            suggestions.insert(
                1,
                {
                    "action": "validate-or-apply-placement-plan",
                    "reason": "No LLM-authored placement plan has been attempted in this session yet.",
                },
            )

    return {
        "session_id": session_id,
        "status": session.get("status"),
        "working_board_path": session.get("working_board_path"),
        "coordinate_mode": session.get("coordinate_mode"),
        "placement_mode": session.get("placement_mode"),
        "latest_execution_summary": summary,
        "suggestions": suggestions,
    }


if __name__ == "__main__":
    # Keep stdio transport silent so MCP protocol frames are never polluted
    # by FastMCP banners or startup logs during sequential tool calls.
    mcp.run(transport="stdio", show_banner=False, log_level="error")
