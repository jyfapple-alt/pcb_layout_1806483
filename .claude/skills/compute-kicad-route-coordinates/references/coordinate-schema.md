# Coordinate Schema

## Required Shape

`coordinate_plan` should look like this:

```json
{
  "default_track_width": 0.1,
  "default_via_size": 0.3,
  "default_via_drill": 0.2,
  "grid_step": 0.1,
  "routes": [
    {
      "net": "/EN",
      "track_width": 0.15,
      "points": [
        {"x": 40.5, "y": 22.4, "layer": "F.Cu"},
        {"x": 42.1, "y": 22.4, "layer": "F.Cu"},
        {"x": 42.7, "y": 23.0, "layer": "F.Cu"},
        {"x": 42.7, "y": 24.6, "layer": "F.Cu"},
        {"x": 42.7, "y": 24.6, "layer": "B.Cu"},
        {"x": 44.3, "y": 24.6, "layer": "B.Cu"},
        {"x": 44.9, "y": 24.0, "layer": "B.Cu"},
        {"x": 45.5, "y": 24.0, "layer": "B.Cu"}
      ]
    }
  ]
}
```

## Semantics

- `default_track_width`, `default_via_size`, `default_via_drill`
  Used when a route does not override them.
- `grid_step`
  The preferred coordinate snap. The current embedded router defaults to `0.1 mm`.
- `routes`
  Each route targets one net.
- `net`
  Net name from the PCB.
- `points`
  Ordered polyline points.

## Bend Geometry

- Keep every same-layer corner obtuse. Prefer a 45-degree direction change, which creates a 135-degree internal corner.
- Do not encode a same-layer right-angle L-corner such as:
  - `(x1, y1) -> (x2, y1) -> (x2, y2)`
- Prefer a chamfered transition such as:
  - `(x1, y1) -> (x_mid, y1) -> (x_mid + d, y1 + d) -> (x_mid + d, y2)`
- When possible, keep the diagonal chamfer at equal `|dx|` and `|dy|` so it lands on a clean 45-degree slope.

## Via Encoding

- A via is created when two consecutive points keep the same XY but change `layer`.
- Example:
  - `{"x": 12.0, "y": 10.0, "layer": "F.Cu"}`
  - `{"x": 12.0, "y": 10.0, "layer": "B.Cu"}`

## Validation Expectations

- The first and last point should touch an existing same-net conductor.
- Every point should stay on a board copper layer.
- Same-layer consecutive points must not be identical.
- Layer changes should happen at the same XY location.
- Same-layer bends must stay obtuse. The validator rejects 90-degree or sharper turns.
- Off-grid points may still validate, but they are weaker candidates and should be avoided unless necessary.

## Good First Targets

- One or two short nets.
- Small cleanup routes after an algorithmic pass.
- A single detour around a blocking area.

## Bad First Targets

- Full-board autorouting.
- Large fanout regions.
- Differential pair bundles unless you are explicitly length-matching them by hand.
