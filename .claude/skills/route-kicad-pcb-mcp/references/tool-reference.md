# Tool Reference

## Server

- Server file: `mcp/pcb_routing_server.py`
- Windows launcher: `mcp/start_pcb_routing_mcp.ps1`
- Preferred runtime: `conda` environment `auto_routing`
- Embedded tool runtime: `mcp/kicad_routing_tools`
- Optional override: environment variable `PCB_ROUTING_TOOLS_ROOT`

## Recommended Order

1. `router_environment_status`
2. `build_rust_router` if the Rust module is missing
3. `create_routing_session`
4. `analyze_board_for_llm`
5. `propose_routing_plan`
6. `apply_routing_plan`
7. `analyze_session_failures`
8. `suggest_next_routing_actions`
9. `inspect_pcb` or `list_nets` only when extra ad-hoc detail is needed

## Tool Mapping

- `inspect_pcb`
  Use for board counts, copper layers, zones, footprint list, fanout candidates, and basic high-speed hints.
- `router_environment_status`
  Use before routing starts or when imports fail.
- `build_rust_router`
  Use when `grid_router` is missing or outdated.
- `create_routing_session`
  Use to create persistent board-routing context that survives multiple MCP calls.
- `get_routing_session`
  Use to inspect the current working board path, stored analysis, plan, and execution history.
- `list_routing_sessions`
  Use to enumerate resumable routing sessions.
- `analyze_board_for_llm`
  Use as the primary structured analysis entry point for the session flow.
- `propose_routing_plan`
  Use to turn objective and constraints into an executable step list.
- `apply_routing_plan`
  Use to execute the current session plan and persist the updated working board plus all log paths.
- `analyze_session_failures`
  Use to summarize blocked steps, failed nets, and retry-relevant details from the latest execution.
- `suggest_next_routing_actions`
  Use to get the next recovery or continuation suggestions for the current session.
- `build_llm_coordinate_context`
  Use when the LLM should compute explicit routing coordinates from structured geometry instead of relying only on the autorouter. This now returns a summary by default and stores the full geometry in the session.
- `get_llm_coordinate_context`
  Use to fetch the stored coordinate context. Set `include_full_context=true` only when the summary is insufficient.
- `validate_llm_coordinate_plan`
  Use to validate a structured coordinate plan before changing the PCB file.
- `apply_llm_coordinate_plan`
  Use to apply a validated coordinate plan, update the session working board, and run post-apply checks. The result now includes file-format validation and automatic syntax repair for version-specific net encoding mismatches.
- `validate_kicad_pcb`
  Use to validate a `.kicad_pcb` file for version-specific net syntax and parser loadability after any generated edit.
- `list_nets`
  Use for diff-pair detection, power-net inspection, or component pad-to-net listings.
- `create_power_planes`
  Wraps `route_planes.py` for GND/VCC zones and optional GND return vias.
- `run_bga_fanout`
  Wraps `bga_fanout.py` for BGA or PGA escape routing.
- `run_qfn_fanout`
  Wraps `qfn_fanout.py` for QFN or QFP perimeter pad fanout.
- `route_differential_pairs`
  Wraps `route_diff.py`.
- `route_single_ended`
  Wraps `route.py`.
- `repair_disconnected_planes`
  Wraps `route_disconnected_planes.py`.
- `check_connectivity`
  Wraps `check_connected.py`.
- `check_drc`
  Wraps `check_drc.py`.
- `check_orphan_stubs`
  Wraps `check_orphan_stubs.py`.
- `run_routing_script`
  Use only when a dedicated tool lacks a needed flag. Limit it to scripts already supported by the server.

## Coordinate Modes

- `algorithm_only`
  Current MVP mode. The LLM decides plan intent and routing constraints, while the embedded router computes coordinates.
- Future modes
  Reserve session metadata for later workflows such as asking the user before coordinate generation, hybrid coordinate hints, or LLM-suggested coordinates.

## Retry Heuristics

- For blocked routing near dense packages, add `--no-bga-zones`.
- For tough 2-layer boards, raise `--max-ripup` and `--max-iterations`.
- For wide power traces without a plane, pass `power_nets` and `power_nets_widths` to `route_single_ended`.
- When route logs include `JSON_SUMMARY`, inspect it first before retrying.
