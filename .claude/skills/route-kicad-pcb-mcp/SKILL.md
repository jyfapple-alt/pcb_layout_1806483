---
name: route-kicad-pcb-mcp
description: Analyze, plan, and autoroute KiCad PCB files through the local FastMCP server in this repository. Use when Codex needs to work on `.kicad_pcb` boards here by inspecting board structure, building the Rust router, creating plane zones, running BGA or QFN fanout, routing single-ended or differential nets, repairing disconnected planes, or verifying DRC/connectivity/orphan stubs through the MCP tools exposed by `mcp/pcb_routing_server.py`.
---

# Route KiCad PCB MCP

Use the local PCB routing MCP server in `mcp/pcb_routing_server.py` for board analysis and autorouting. The server now carries its own embedded routing runtime under `mcp/kicad_routing_tools`, so it does not need `test/KiCadRoutingTools` at runtime. Prefer these MCP tools over ad-hoc shell commands so the workflow stays inside the `auto_routing` conda environment and every run has consistent logs.

## Quick Start

1. Ensure the local MCP server is available. If it is not already attached, start it with `conda run -n auto_routing python mcp/pcb_routing_server.py` or `powershell -File mcp/start_pcb_routing_mcp.ps1`.
2. Call `router_environment_status` first. If `grid_router_importable` is false, call `build_rust_router`.
3. Call `inspect_pcb` to collect the board summary, copper layers, zone information, and likely fanout candidates.
4. Call `list_nets` with `diff_pairs=true` and `power=true` to detect differential pairs and power or ground nets.
5. Choose the routing steps in this order when applicable:
   - `create_power_planes`
   - `run_bga_fanout` or `run_qfn_fanout`
   - `route_differential_pairs`
   - `route_single_ended`
   - `repair_disconnected_planes`
6. Always finish with `check_connectivity`, `check_drc`, and `check_orphan_stubs`.

Read [references/tool-reference.md](references/tool-reference.md) when you need the tool-by-tool mapping or example decision rules.

## Workflow

### Analyze First

- Use `inspect_pcb` to report nets, footprints, existing segments, vias, zones, and copper layers.
- Use `list_nets` for differential-pair detection and power-net analysis.
- Treat boards with `total_segments == 0` as effectively unrouted unless the user says otherwise.

### Choose the Smallest Necessary Flow

- If the board already has the needed GND or power zone, do not recreate it unless the user asks.
- If `inspect_pcb` flags BGA, PGA, QFN, or QFP parts, fanout before general routing.
- Route differential pairs before single-ended signals.
- Use `create_power_planes` for dense GND or power nets, or when the board already depends on plane-based connectivity.
- Use `route_single_ended` with `power_nets` and `power_nets_widths` when wide traces are simpler than new planes.

### Execute with Dedicated Tools First

- Prefer the dedicated MCP tools over raw passthrough.
- Use `run_routing_script` only for uncommon flags not surfaced by the dedicated tools.
- Pass repo-relative or absolute file paths. The server resolves repo-relative paths from the project root.
- Prefer stepwise output files like `board_step1.kicad_pcb`, `board_step2.kicad_pcb`, and keep the original board untouched unless the user explicitly wants overwrite behavior.

### Verify and Retry

- Always run `check_connectivity`, `check_drc`, and `check_orphan_stubs` after routing.
- Inspect `json_summary`, `stdout_tail`, `stderr_tail`, and `log_path` from routing tools before deciding whether a retry is needed.
- For difficult boards, retry with more aggressive flags through `extra_args` or `run_routing_script`, especially `--no-bga-zones`, higher `--max-ripup`, or higher `--max-iterations`.
- If the routing succeeded, summarize the final output board file and the three verification results.

## Notes

- This skill assumes the MCP server runs from the `auto_routing` conda environment.
- The server uses the embedded runtime in `mcp/kicad_routing_tools` by default. Only set `PCB_ROUTING_TOOLS_ROOT` when you intentionally want to override that runtime.
- `build_rust_router` already sets `PYO3_PYTHON` to the active interpreter, so use it instead of calling `cargo build` directly.
- Keep log paths from tool results when a route fails. They are the fastest way to inspect long autorouter output without flooding context.
