# Tool Reference

## Recommended Order

1. `create_routing_session`
2. `analyze_board_for_llm`
3. `auto_place_session_footprints` for the default path
4. `analyze_board_for_llm` again if you need refreshed geometry-aware routing decisions
5. Continue with routing tools

## Placement Tools

- `auto_place_footprints`
  Use for one-shot placement on a board file outside the session workflow.
- `auto_place_session_footprints`
  Use as the default session-aware placement step before routing.
- `build_llm_placement_context`
  Use when the LLM should reason about footprint coordinates explicitly.
- `get_llm_placement_context`
  Use to fetch the stored placement context.
- `validate_llm_placement_plan`
  Use to reject overlaps or out-of-board placements before modifying the file.
- `apply_llm_placement_plan`
  Use to commit an LLM-authored placement plan and advance the session board state.

## Routing Hand-off

- After placement, use `analyze_board_for_llm` again if the next routing decision needs fresh geometry.
- For algorithmic routing, continue with `propose_routing_plan` then `apply_routing_plan`.
- For explicit LLM coordinate routing, continue with `build_llm_coordinate_context`.
