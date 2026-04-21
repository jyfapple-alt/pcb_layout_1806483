---
name: compute-kicad-route-coordinates
description: Compute explicit track and via coordinates for local `.kicad_pcb` files when Codex should route by emitting structured points instead of relying only on the autorouter. Use when the task needs LLM-authored coordinate plans, manual cleanup of a few nets, short critical routes, or hybrid workflows that combine MCP geometry context with direct coordinate output.
---

# Compute KiCad Route Coordinates

Use this skill when the model should output explicit routing coordinates instead of only calling the autorouter. The preferred workflow is to keep the route small, structured, and validated:

1. Start or reuse a routing session in `mcp/pcb_routing_server.py`.
2. Call `build_llm_coordinate_context` for the specific nets you want to route.
3. Read [references/coordinate-schema.md](references/coordinate-schema.md) before generating any coordinate plan.
4. Read [references/algorithm-heuristics.md](references/algorithm-heuristics.md) when you need routing heuristics distilled from the embedded algorithm scripts.
5. Generate a `coordinate_plan` with explicit `points`.
6. Call `validate_llm_coordinate_plan` before `apply_llm_coordinate_plan`.
7. If validation or DRC fails, tighten the coordinate plan or fall back to the algorithmic router for that net.

## When To Use This

- Use it for a few nets, not for the entire board by default.
- Prefer it for short critical connections, cleanup edits, constrained hand-tuned routes, or hybrid sessions where the LLM handles only the difficult final geometry.
- Avoid it for dense BGA escape routing, long buses, or large unrouted boards unless the user explicitly wants coordinate-level control.

## Core Rules

- Anchor the first and last point to an existing same-net pad, via, segment, or stub.
- Keep coordinates on the routing grid when possible.
- Use repeated XY with a different `layer` to request a via.
- Keep consecutive same-layer points distinct. Zero-length same-layer segments are invalid.
- Minimize vias and unnecessary bends.
- Always validate before applying.

## Notes

- The current MCP coordinate tools are: `build_llm_coordinate_context`, `validate_llm_coordinate_plan`, and `apply_llm_coordinate_plan`.
- These tools work inside the existing routing session flow, so coordinate work can coexist with algorithmic planning.
- The embedded runtime that informed this skill lives under `mcp/kicad_routing_tools/`, but you usually should rely on the distilled rules here instead of re-reading the raw scripts unless the route is unusual.
