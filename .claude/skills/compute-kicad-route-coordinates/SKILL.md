---
name: compute-kicad-route-coordinates
description: Compute explicit track and via coordinates for local `.kicad_pcb` files when Codex should route by emitting structured points instead of relying only on the autorouter. Use when the task needs LLM-authored coordinate plans, manual cleanup of a few nets, short critical routes, or hybrid workflows that combine MCP geometry context with direct coordinate output.
---

# Compute KiCad Route Coordinates

Use this skill when the model should output explicit routing coordinates instead of only calling the autorouter. The preferred workflow is to keep the route small, structured, and validated:

1. Start or reuse a routing session in `mcp/pcb_routing_server.py`.
2. Call `build_llm_coordinate_context` for the specific nets you want to route.
3. If the summary is not enough, call `get_llm_coordinate_context(include_full_context=true)` to inspect the stored geometry payload.
4. Read [references/coordinate-schema.md](references/coordinate-schema.md) before generating any coordinate plan.
5. Read [references/algorithm-heuristics.md](references/algorithm-heuristics.md) when you need routing heuristics distilled from the embedded algorithm scripts.
6. Generate a `coordinate_plan` with explicit `points`.
7. Call `validate_llm_coordinate_plan` before `apply_llm_coordinate_plan`.
8. After applying, inspect `file_validation` in the tool result or call `validate_kicad_pcb` if you need an explicit file-level sanity check.
9. If validation, file-format checks, or DRC fails, tighten the coordinate plan or fall back to the algorithmic router for that net.

## When To Use This

- Use it for a few nets, not for the entire board by default.
- Prefer it for short critical connections, cleanup edits, constrained hand-tuned routes, or hybrid sessions where the LLM handles only the difficult final geometry.
- Avoid it for dense BGA escape routing, long buses, or large unrouted boards unless the user explicitly wants coordinate-level control.

## Automatic Trace Width Selection

Before generating any `coordinate_plan`, classify every net by its electrical role and assign `track_width` accordingly. Do **not** use the default width for all nets — always reason per net.

### Step 1 — Classify Each Net

| Class | Identification signals | Examples |
|-------|----------------------|---------|
| **Power input** | Net name contains VIN, VCC, VDD, VBAT, PWR; connected to regulator input pin (pintype `power_in`) or input connector pad | `/VIN`, `VCC_3V3` |
| **Switching node** | Connected to inductor pad + IC switch-output pin (pinfunction `SW`, `LX`, `PHASE`) | `Net-(U1-SW)` |
| **Power output** | Net name contains VOUT, VREG, VBUS; connected to output capacitor + load connector | `/VOUT` |
| **Ground return** | Net name is `GND`, `AGND`, `PGND`, or `VSS`; F.Cu trace segments only (zone handles the rest) | `GND` F.Cu stub |
| **Control / signal** | EN, FB, SYNC, ADJ, SCL, SDA, GPIO, or any net with pad_count ≤ 2 between IC signal pin and connector | `/EN`, `/FB` |
| **High-speed / diff pair** | Flagged by `high_speed_hints` or name contains DP/DM/TX/RX | treat separately |

Use `build_llm_coordinate_context` pad data (`pinfunction`, `pintype`, net name, connected component types) to confirm the class. When in doubt, treat as **Power output** (safer to be wider).

### Step 2 — Pick Track Width

Use IPC-2221 external-layer values (1 oz copper, 10 °C rise) as the baseline:

| Current estimate | Min width | Recommended width |
|-----------------|-----------|------------------|
| < 0.1 A (signal) | 0.1 mm | **0.15 mm** |
| 0.1 – 0.5 A | 0.15 mm | **0.25 mm** |
| 0.5 – 1.0 A | 0.3 mm | **0.5 mm** |
| 1.0 – 2.0 A | 0.5 mm | **0.8 mm** |
| 2.0 – 3.0 A | 0.8 mm | **1.2 mm** |
| > 3.0 A | 1.2 mm | **1.5 mm+** |

### Step 3 — Apply Per-Net Rules

Apply these rules in order; the first match wins:

1. **Switching node** (`SW`, `LX`, `PHASE`): use **same width as power input**, because peak inductor current flows here.
2. **Power input / output**: estimate load current from the output IC rating or connector spec; pick from the table above.
3. **GND F.Cu stub** (trace from SMD-only GND pad to nearest GND via): match the width of the power nets it returns.
4. **EN / FB / SYNC** (single IC signal pin → connector or resistor): **0.15 mm** regardless of pad count.
5. **Differential pairs**: match impedance target; do not widen arbitrarily.
6. **Unknown / unclassified**: use **0.25 mm** as a safe default.

### Step 4 — Quick-Reference for Common Topologies

**Switching regulator (SOT-23-5 / SOT-23-8, ≤ 1 A class):**
- `/VIN`, `Net-(U1-SW)`, `/VOUT`, `GND` stub → **0.5 mm**
- `/EN`, `/FB`, `/ADJ` → **0.15 mm**

**LDO regulator (≤ 1 A):**
- VIN, VOUT, GND stub → **0.5 mm**
- ADJ/EN → **0.15 mm**

**µC / logic board (signal-only, no high-current rails):**
- VCC, GND → **0.3 mm**
- GPIO, UART, SPI, I²C → **0.15 mm**

**Power module (> 3 A):**
- Power rails → **1.5 mm+**; use flood fill / zone for GND instead of traces.

## Core Rules

- Anchor the first and last point to an existing same-net pad, via, segment, or stub.
- Keep coordinates on the routing grid when possible.
- Use repeated XY with a different `layer` to request a via.
- Keep consecutive same-layer points distinct. Zero-length same-layer segments are invalid.
- Minimize vias and unnecessary bends.
- Always validate before applying.
- **Set `track_width` per route in the coordinate plan — never rely on `default_track_width` alone.**

## Notes

- The current MCP coordinate tools are: `build_llm_coordinate_context`, `get_llm_coordinate_context`, `validate_llm_coordinate_plan`, and `apply_llm_coordinate_plan`.
- These tools work inside the existing routing session flow, so coordinate work can coexist with algorithmic planning.
- The embedded runtime that informed this skill lives under `mcp/kicad_routing_tools/`, but you usually should rely on the distilled rules here instead of re-reading the raw scripts unless the route is unusual.
