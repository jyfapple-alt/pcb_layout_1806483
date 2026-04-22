---
name: place-kicad-footprints-mcp
description: Infer and apply KiCad footprint placement through the local PCB routing MCP in this repository. Use when a `.kicad_pcb` board needs component layout before routing, especially when footprints were reset to `(0,0)`, drifted outside the board outline, or need a fresh engineering-style arrangement derived from board geometry, footprint size, net roles, and component connectivity rather than memorized coordinates.
---

# Place KiCad Footprints MCP

Use the local MCP server in `mcp/pcb_routing_server.py` for footprint placement before routing. Prefer this skill when the board is unrouted, footprints have been zeroed, or the next step should be "place first, route second".

Do not memorize coordinates from a known board and do not encode board-specific answers into the workflow. Always infer placement from the current board state:

- board outline and usable area
- footprint size and orientation
- footprint origin semantics: KiCad `at` is the footprint origin, not necessarily the package center
- connector, IC, inductor, capacitor, resistor roles
- shared nets and power-flow direction
- keepout intent from cutouts or edge proximity

## Recommended Flow

1. Start or reuse a routing session with `create_routing_session`.
2. Call `analyze_board_for_llm`.
3. Check `analysis.placement_hints`.
4. If `needs_placement` is true, use `auto_place_session_footprints` for the default heuristic flow.
5. If you need model-authored placement decisions, use `build_llm_placement_context`, then `validate_llm_placement_plan`, then `apply_llm_placement_plan`.
6. Re-run `analyze_board_for_llm` after placement if later routing decisions depend on the new geometry.
7. Continue with the routing skill or the coordinate-routing skill.

Read [references/tool-reference.md](references/tool-reference.md) when you need the exact tool mapping.
Read [references/placement-heuristics.md](references/placement-heuristics.md) when you need generic placement reasoning patterns.

## Default Strategy

- Prefer `auto_place_session_footprints` for fresh boards and zeroed footprints.
- Treat the footprint origin and the physical envelope as separate things. A valid placement must keep the full envelope inside the board, not just the origin point.
- Treat connectors as edge candidates.
- Place power ICs near the center of the power path, not on an arbitrary edge.
- Keep inductors, input capacitors, and output capacitors close to the IC pins they serve.
- Keep feedback or control parts near the control pins and away from the noisy switching loop.
- When a footprint is elongated or anchored at one end, reason about rotation together with position.
- Leave routing channels between larger footprints instead of packing everything tightly.

## LLM Placement Mode

Use `build_llm_placement_context` when the heuristic placer is not enough or when the user wants the model to choose coordinates explicitly.

- Generate a `placement_plan` with `placements`.
- Each placement should include `reference`, `x`, `y`, and optional `rotation`.
- For edge parts and long THT parts, do not leave rotation implicit. Choose it deliberately.
- Validate before apply.
- Base every placement on the context graph, not on remembered coordinates from a prior run.

## Notes

- The heuristic placer is intentionally generic: it reasons from nets, roles, and geometry instead of special-casing any one board.
- `auto_place_session_footprints` updates the session working board and can refresh stored analysis.
- Use this skill before `route-kicad-pcb-mcp` or `compute-kicad-route-coordinates` when `placement_hints.needs_placement` is true.
