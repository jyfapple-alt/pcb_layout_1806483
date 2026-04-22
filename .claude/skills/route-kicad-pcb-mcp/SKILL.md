---
name: route-kicad-pcb-mcp
description: Analyze, place, plan, and route KiCad PCB files through the local FastMCP server in this repository. Use when Codex needs to work on `.kicad_pcb` boards here by inspecting board structure, placing zeroed footprints, building the Rust router, creating plane zones, running BGA or QFN fanout, routing single-ended or differential nets, repairing disconnected planes, or verifying DRC/connectivity/orphan stubs through the MCP tools exposed by `mcp/pcb_routing_server.py`.
---

# Route KiCad PCB MCP

Use the local PCB routing MCP server in `mcp/pcb_routing_server.py` for board analysis, footprint placement, and routing. The server carries its own embedded routing runtime under `mcp/kicad_routing_tools`, so it does not need `test/KiCadRoutingTools` at runtime. Prefer these MCP tools over ad-hoc shell commands so the workflow stays inside the `auto_routing` conda environment and every run has consistent logs.

This skill supports a session-based workflow so the LLM can participate more deeply than simple one-shot script calls. The recommended flow is:

`create_routing_session -> analyze_board_for_llm -> auto_place_session_footprints (when needed) -> analyze_board_for_llm -> propose_routing_plan -> apply_routing_plan -> analyze_session_failures -> suggest_next_routing_actions`

## Quick Start

1. Ensure the local MCP server is available. If it is not already attached, start it with `conda run -n auto_routing python mcp/pcb_routing_server.py` or `powershell -File mcp/start_pcb_routing_mcp.ps1`.
2. Call `router_environment_status` first. If `grid_router_importable` is false, call `build_rust_router`.
3. Create a session with `create_routing_session`.
4. Call `analyze_board_for_llm` to store a structured board snapshot in the session.
5. If `analysis.placement_hints.needs_placement` is true, call `auto_place_session_footprints`, or use the LLM placement tools if the user wants explicit placement control.
6. Re-run `analyze_board_for_llm` after placement when later routing decisions should use the new geometry.
7. Call `propose_routing_plan` with the current objective and routing constraints.
8. Execute the stored plan with `apply_routing_plan`.
9. Review `analyze_session_failures` and `suggest_next_routing_actions` before retrying or tightening constraints.

Read [references/tool-reference.md](references/tool-reference.md) when you need the tool-by-tool mapping or example decision rules.

## Workflow

### Analyze First

- Use `analyze_board_for_llm` as the default analysis entry point for a session.
- Use `inspect_pcb` and `list_nets` when you need extra ad-hoc detail beyond the stored session analysis.
- Treat boards with `total_segments == 0` as effectively unrouted unless the user says otherwise.
- Treat `analysis.placement_hints.needs_placement` as a strong signal that placement should happen before routing.

### Build an Explicit Plan

- Use `propose_routing_plan` to turn the current objective and constraints into an executable step list.
- Treat the plan as the first deep LLM participation layer. The model should decide intent, ordering, constraints, and retry posture. The embedded router still computes the actual legal geometry.
- If footprints were reset to `(0,0)` or sit outside the board, place them before fanout and routing.
- During placement, treat KiCad footprint coordinates as origin anchors rather than package centers, and rotate elongated parts deliberately.
- If the board already has the needed GND or power zone, prefer keeping it instead of recreating it.
- If the analysis flags BGA, PGA, QFN, or QFP parts, fanout before general routing.
- Route differential pairs before single-ended signals.
- Use the new placement tools or the `place-kicad-footprints-mcp` skill instead of hardcoding coordinates from a known board.
- Use `coordinate_mode="algorithm_only"` for the default scripted flow.
- Use `coordinate_mode="llm_coordinates"` together with `build_llm_coordinate_context`, `validate_llm_coordinate_plan`, and `apply_llm_coordinate_plan` when the model should emit explicit route coordinates for a small set of nets.
- Use `placement_mode="auto"` to let the planner inject automatic placement when analysis shows the footprints need it.
- Use `build_llm_placement_context`, `validate_llm_placement_plan`, and `apply_llm_placement_plan` when the model should choose footprint coordinates explicitly.
- For explicit coordinate work, also use the project skill `compute-kicad-route-coordinates`.
- In `llm_coordinates` mode, require professional PCB corner geometry: same-layer bends must stay obtuse (`> 90 degrees` internal angle), and the preferred pattern is a 45-degree chamfer that creates a 135-degree corner instead of an orthogonal L-turn.
- When using `llm_coordinates` mode, always apply the automatic trace-width rules defined in `compute-kicad-route-coordinates`: classify every net (power input, switching node, power output, GND stub, signal) from the `build_llm_coordinate_context` pad data, then set `track_width` per route accordingly. Never use a single default width for all nets.
- When using `llm_coordinates` mode, never emit a same-layer `horizontal -> vertical` or `vertical -> horizontal` corner in the coordinate plan. Rewrite those turns as straight + 45-degree diagonal + straight before validation.

### Execute Through the Session

- Prefer `apply_routing_plan` as the default execution entry point. It persists the working board, logs, and latest verification results into the session.
- Prefer `auto_place_session_footprints` as the default placement entry point when footprints need a fresh layout.
- Use the dedicated routing tools directly only when you need manual intervention or a nonstandard recovery step.
- Use `run_routing_script` only for uncommon flags not surfaced by the dedicated tools.
- Pass repo-relative or absolute file paths. The server resolves repo-relative paths from the project root.
- Prefer stepwise output files and keep the original board untouched unless the user explicitly wants overwrite behavior.

### Verify and Retry

- `apply_routing_plan` already runs the verification steps included in the stored plan.
- Use `analyze_session_failures` to summarize failed routing steps, failed nets, and log locations.
- Use `suggest_next_routing_actions` to ask the MCP for the next retry posture before changing constraints manually.
- If placement fails, switch to the LLM placement context and validate an explicit `placement_plan` before retrying.
- For difficult boards, retry with more aggressive flags, especially `--no-bga-zones`, higher `--max-ripup`, or higher `--max-iterations`.
- If the routing succeeded, summarize the final working board file and the three verification results.

## Notes

- This skill assumes the MCP server runs from the `auto_routing` conda environment.
- The server uses the embedded runtime in `mcp/kicad_routing_tools` by default. Only set `PCB_ROUTING_TOOLS_ROOT` when you intentionally want to override that runtime.
- `build_rust_router` already sets `PYO3_PYTHON` to the active interpreter, so use it instead of calling `cargo build` directly.
- Session artifacts are stored under `mcp/routing_sessions/<session_id>/`.
- Keep log paths from tool results when a route fails. They are the fastest way to inspect long autorouter output without flooding context.
