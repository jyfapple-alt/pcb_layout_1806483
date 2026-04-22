# Algorithm Heuristics

This file distills the coordinate-relevant ideas from the embedded routing runtime under `mcp/kicad_routing_tools/`.

## Source Files

- `routing_config.py`
- `extract_pcb_geometry.py`
- `geometry_utils.py`
- `single_ended_routing.py`
- `connectivity.py`
- `check_drc.py`
- `kicad_writer.py`

## Defaults To Borrow

- Grid: `0.1 mm`
- Base track width: `0.1 mm`
- Clearance: `0.1 mm`
- Via size: `0.3 mm`
- Via drill: `0.2 mm`

If the user or session constraints specify different values, follow those instead.

## Anchor Selection

The algorithmic router starts from real conductors, not free-floating points. Mimic that:

- Prefer pad centers as route endpoints.
- Existing vias can be reused as anchors.
- Existing same-net segments count as valid anchors, including mid-segment touches.
- Stub ends are useful for connecting into partially routed nets.

## Shape Heuristics

- Prefer short routes built from straight runs plus 45-degree chamfers.
- Do not use same-layer Manhattan L-corners. Split any orthogonal direction change into two 45-degree turns so the copper corner stays obtuse.
- Use diagonal segments when they replace a right angle or avoid congestion cleanly.
- When the grid allows it, keep diagonal chamfers at equal `|dx|` and `|dy|` for clean 45-degree geometry.
- Avoid acute corners, U-turns, and zig-zagging.
- Reduce bend count before optimizing anything else.
- Use fewer vias unless the layer switch removes a strong blockage.

## Layer Switch Rules

- Put vias exactly at the point where the layer changes.
- Do not move XY during the layer switch step.
- Avoid placing vias too close to pads, existing vias, or board edges.

## Clearance Mindset

The embedded DRC logic effectively reasons in terms of:

- track half-width + clearance against pads
- via radius + clearance against pads
- segment-to-segment and via-to-via distance on shared copper layers

When hand-computing coordinates, leave visibly comfortable spacing instead of trying to hit the minimum.

## When To Stop

If the route requires:

- multiple dense escape decisions
- repeated rip-up behavior
- many interacting vias
- wide power detours across crowded copper

stop forcing coordinate output and fall back to the algorithmic router or a hybrid flow.
