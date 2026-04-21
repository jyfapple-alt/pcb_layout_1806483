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
3. `inspect_pcb`
4. `list_nets`
5. `create_power_planes` when planes are needed
6. `run_bga_fanout` or `run_qfn_fanout` when dense packages need escape routing
7. `route_differential_pairs` when diff pairs exist
8. `route_single_ended` for remaining nets
9. `repair_disconnected_planes` when planes were cut by routing
10. `check_connectivity`
11. `check_drc`
12. `check_orphan_stubs`

## Tool Mapping

- `inspect_pcb`
  Use for board counts, copper layers, zones, footprint list, fanout candidates, and basic high-speed hints.
- `router_environment_status`
  Use before routing starts or when imports fail.
- `build_rust_router`
  Use when `grid_router` is missing or outdated.
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

## Retry Heuristics

- For blocked routing near dense packages, add `--no-bga-zones`.
- For tough 2-layer boards, raise `--max-ripup` and `--max-iterations`.
- For wide power traces without a plane, pass `power_nets` and `power_nets_widths` to `route_single_ended`.
- When route logs include `JSON_SUMMARY`, inspect it first before retrying.
