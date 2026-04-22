# Placement Heuristics

Use these generic rules when reasoning about footprint placement:

- Treat the KiCad footprint origin as an anchor point, not as the center of the physical body.
- Validate the full footprint envelope, especially for THT, axial, and edge-mounted connectors.
- Infer the dominant flow from connectors, power nets, and the strongest shared-net graph.
- Keep connectors near board edges, not in the middle of the routing area.
- Keep the main IC near the center of the functional cluster it controls.
- Keep high-current loops short: input cap to IC, IC to inductor, inductor to output cap.
- Keep feedback and enable circuitry away from the switching node.
- For long or asymmetric footprints, choose rotation together with position. The best coordinate with the wrong angle is still a bad placement.
- Preserve clearance for routing channels; a valid non-overlapping layout can still be poor if it leaves no copper paths.
- When information is ambiguous, choose the placement that shortens the strongest shared nets and keeps noisy power paths compact.
