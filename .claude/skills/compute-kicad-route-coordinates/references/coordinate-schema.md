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
      "track_width": 0.1,
      "points": [
        {"x": 40.5, "y": 22.4, "layer": "F.Cu"},
        {"x": 43.2, "y": 22.4, "layer": "F.Cu"},
        {"x": 43.2, "y": 22.4, "layer": "B.Cu"},
        {"x": 45.8, "y": 22.4, "layer": "B.Cu"}
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
- Off-grid points may still validate, but they are weaker candidates and should be avoided unless necessary.

## Good First Targets

- One or two short nets.
- Small cleanup routes after an algorithmic pass.
- A single detour around a blocking area.

## Bad First Targets

- Full-board autorouting.
- Large fanout regions.
- Differential pair bundles unless you are explicitly length-matching them by hand.
