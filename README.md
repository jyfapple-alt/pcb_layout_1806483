# auto_routing README

这份 README 主要做两件事：

1. 用通俗的话解释这个仓库里暴露出来的 MCP tools 是干什么的。
2. 把每个 tool 的入参、返回值、参数意义讲清楚。
3. 顺带把 `.claude/skills` 里的技能做一遍“人话解读”。

这份说明是根据下面这些源码整理出来的，不是凭空猜的：

- `mcp/pcb_routing_server.py`
- `mcp/route_analysis.py`
- `mcp/route_planner.py`
- `mcp/footprint_placement.py`
- `mcp/coordinate_routing.py`
- `mcp/routing_session_store.py`
- `.claude/skills/*/SKILL.md`

---

## 1. 这个仓库的 MCP 到底是什么

这个项目里真正对外暴露 MCP tools 的入口是：

- `mcp/pcb_routing_server.py`

它用的是 `FastMCP`，名字叫：

- `pcb-routing-mcp`

你可以把它理解成一个“会操作 KiCad PCB 的本地工具服务器”。  
LLM 不直接手搓 PCB 文件，而是通过这些 tool 去做：

- 看板子结构
- 分析哪些器件要先 fanout
- 自动放置 footprint
- 生成 routing plan
- 执行自动布线
- 让 LLM 输出坐标形式的走线计划
- 校验 DRC / 连通性 / 悬空 stub

仓库里还有很多 Python 文件，但不是每个 Python 函数都算 MCP tool。  
真正的 MCP tool，是那些被 `@mcp.tool(...)` 装饰过的函数。

当前一共暴露了 **33 个 MCP tool**。

---

## 2. 整体结构怎么理解

可以把这些 tool 分成 5 组：

- **基础检查类**
  看板子、看环境、看文件是否合法。
- **Session 工作流类**
  把一次布线任务做成一个“会话”，方便多次调用之间保留上下文。
- **Footprint 放置类**
  给元件做自动摆放，或者让 LLM 自己出摆放方案。
- **坐标级布线类**
  给 LLM 一份几何上下文，让它直接输出走线点坐标。
- **底层脚本封装类**
  直接封装 `mcp/kicad_routing_tools` 里的 CLI 脚本，例如 fanout、route、DRC 检查等。

---

## 3. 通用规则

在看每个 tool 之前，先记住几个通用规则，后面会反复出现。

### 3.1 路径怎么处理

- 凡是 `pcb_path`、`input_pcb`、`board_path` 这类输入文件参数，都会先检查文件是否存在。
- 相对路径会按**仓库根目录**来解析，不是按当前 shell 所在目录。
- 输出路径参数比如 `output_path`、`output_pcb`、`output_board`，如果传相对路径，也会按仓库根目录解析。

### 3.2 日志放哪里

底层脚本类 tool 会把输出写进：

- `mcp/logs/`

返回里通常会带一个：

- `log_path`

如果自动布线失败，优先看这个日志路径最省事。

### 3.3 Session 放哪里

路由会话会保存在：

- `mcp/routing_sessions/<session_id>/`

每个 session 里会保存：

- 当前工作板文件
- 分析结果
- 路由计划
- 执行历史
- 最近几次校验结果
- 放置/坐标上下文
- 产物路径和日志路径

### 3.4 底层脚本封装的通用返回结构

下面这些 tool 都走统一的 `_run_script(...)` 返回格式：

- `run_routing_script`
- `build_rust_router`
- `list_nets`
- `run_bga_fanout`
- `run_qfn_fanout`
- `route_single_ended`
- `route_differential_pairs`
- `create_power_planes`
- `repair_disconnected_planes`
- `check_connectivity`
- `check_drc`
- `check_orphan_stubs`

它们的返回字段基本一致：

- `success`
  是否成功，底层进程退出码为 0 时通常为 `true`。
- `script`
  实际调用的脚本名，例如 `route.py`。
- `command`
  实际执行的命令数组。
- `cwd`
  运行脚本时的工作目录。
- `returncode`
  底层进程退出码。超时场景会是 `null`。
- `duration_seconds`
  运行耗时。
- `stdout_tail`
  标准输出的最后一段内容。
- `stderr_tail`
  标准错误的最后一段内容。
- `log_path`
  完整日志文件路径。
- `json_summary`
  如果脚本输出了 `JSON_SUMMARY:` 行，这里会解析成 JSON；否则为 `null`。
- `timed_out`
  只有超时返回时才会出现，值为 `true`。

一句话理解：  
这类 tool 负责“跑脚本”，所以重点看 `success`、`log_path`、`json_summary`。

---

## 4. 几个高频数据结构先看懂

后面的 tool 会反复返回这些结构。

### 4.1 Session 对象

`create_routing_session` 和 `get_routing_session` 返回的是完整 session 对象，核心字段有：

- `session_id`
  会话 ID，例如 `rs_xxxxx`。
- `session_name`
  会话显示名。
- `description`
  会话说明。
- `board_path`
  原始板文件路径。
- `working_board_path`
  当前正在操作的板文件路径。后续每做一步，通常都是它在变化。
- `coordinate_mode`
  坐标模式。当前最有意义的是：
  - `algorithm_only`：主要靠算法路由器
  - `llm_coordinates`：允许 LLM 直接给坐标走线
- `placement_mode`
  放置模式。当前最常见的是：
  - `auto`
  - `llm_placement`
- `status`
  当前状态，例如 `created`、`analyzed`、`planned`、`placed`、`executed` 等。
- `created_at` / `updated_at`
  创建和更新时间。
- `session_dir`
  这个 session 自己的目录。
- `output_dir`
  这个 session 默认的输出目录。
- `analysis`
  上一次结构化分析结果。
- `objective`
  这次规划的目标，比如 `autoroute`。
- `constraints`
  规划时使用的约束。
- `proposed_plan`
  上一次生成的 routing plan。
- `execution_history`
  历史执行记录。
- `latest_checks`
  最近一次连通性 / DRC / orphan stub 检查结果。
- `placement_context`
  LLM 放置上下文。
- `latest_placement_validation`
  最近一次摆放方案校验结果。
- `placement_history`
  摆放历史。
- `coordinate_context`
  LLM 坐标布线上下文。
- `latest_coordinate_validation`
  最近一次坐标计划校验结果。
- `coordinate_history`
  坐标布线历史。
- `artifacts`
  产物路径集合，至少包含 `boards` 和 `logs`。
- `notes`
  会话过程中的注释日志。

### 4.2 `analysis` 结构

`analyze_board_for_llm` 返回的核心是 `analysis`，里面常见字段有：

- `pcb_path`
- `board`
  - `total_nets`
  - `named_nets`
  - `total_footprints`
  - `total_segments`
  - `total_vias`
  - `total_zones`
  - `fresh_board`
  - `copper_layers`
  - `board_bounds`
- `zones`
  每个 zone 的 `net_id`、`net_name`、`layer`、`layers`
- `nets`
  每个命名网络的 `net_id`、`name`、`pad_count`
- `ground_nets`
- `power_nets`
- `differential_pairs`
- `fanout_candidates`
  候选密脚器件，包含 `reference`、`footprint`、`pad_count`、`recommended_tool`
- `unrouted_named_nets`
- `high_speed_hints`
  包含 `highest_tier` 和 `matched_nets`
- `placement_hints`
  包含：
  - `footprints_at_origin`
  - `footprints_outside_board`
  - `collapsed_groups`
  - `suggested_refs`
  - `needs_placement`
- `planning_hints`
  包含：
  - `has_existing_ground_zone`
  - `has_diff_pairs`
  - `needs_fanout`
  - `needs_plane_repair_after_routing`
  - `suggested_routing_layers`
  - `suggested_power_nets_for_wide_traces`

### 4.3 `plan` 结构

`propose_routing_plan` 里的 `plan` 长这样：

- `plan_id`
- `generated_at`
- `objective`
- `coordinate_mode`
- `constraints`
- `analysis_snapshot`
  这是分析结果的浓缩版，不是全量 `analysis`
- `steps`
  每一步都是：
  - `step_id`
  - `kind`
  - `reason`
  - `input_board`
  - `output_board`
  - `parameters`

### 4.4 `placement_plan` 结构

LLM 摆放方案需要长这样：

```json
{
  "grid_step": 0.25,
  "placements": [
    {
      "reference": "U1",
      "x": 40.0,
      "y": 42.0,
      "rotation": 0.0
    }
  ]
}
```

字段含义：

- `grid_step`
  建议摆放网格。
- `placements`
  摆放列表。
- `reference`
  器件位号。
- `x` / `y`
  KiCad footprint 原点坐标，不一定是器件物理中心。
- `rotation`
  角度，单位度。

### 4.5 放置校验结果 `validation`

放置类校验结果常见字段：

- `valid`
- `pcb_path`
- `placement_gap`
- `board_margin`
- `placement_count`
- `errors`
- `warnings`
- `placements`
  每个 placement 的摘要，通常包含 `reference`、`x`、`y`、`rotation`、`width`、`height`

### 4.6 `coordinate_plan` 结构

LLM 坐标走线计划需要长这样：

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
        {"x": 44.3, "y": 24.6, "layer": "B.Cu"}
      ]
    }
  ]
}
```

字段含义：

- `default_track_width` / `default_via_size` / `default_via_drill`
  默认线宽和过孔参数。
- `grid_step`
  推荐坐标栅格。
- `routes`
  一组要新加的线路。
- `net`
  网络名。
- `track_width`
  该条 route 自己的线宽。
- `points`
  走线路径点。

注意：

- 相邻两个点 XY 相同但 layer 不同，表示“这里插一个 via”。
- 同层转角不能是直角或更尖，推荐 45 度斜切形成 135 度内角。

### 4.7 坐标校验结果 `validation`

坐标布线校验结果常见字段：

- `valid`
- `pcb_path`
- `defaults`
- `route_summaries`
- `tracks`
- `vias`
- `errors`
- `warnings`
- `normalized_plan`

### 4.8 文件合法性校验 `file_validation`

`validate_kicad_pcb` 以及坐标/摆放 apply 成功后附带的文件校验，核心字段有：

- `valid`
- `pcb_path`
- `kicad_version`
- `net_syntax_mode`
  - `numeric`
  - `name-only`
- `parse_ok`
- `parse_error`
- `pcbnew`
  - `available`
  - `ok`
  - `error`
- `issues`
  常见问题码：
  - `named-net-syntax-in-kicad9`
  - `numeric-net-syntax-in-kicad10`
  - `parser-error`
  - `pcbnew-load-error`

---

## 5. 每个 MCP tool 是干什么的

下面按类别逐个解释。

---

## 5.1 基础检查类

### `inspect_pcb`

**作用**  
快速看一块 `.kicad_pcb` 的整体情况，适合“先摸底”。

**参数**

- `pcb_path` (`str`, 必填)
  要检查的板文件路径。

**返回**

- `pcb_path`
  解析后的绝对路径。
- `total_nets`
- `total_footprints`
- `total_segments`
- `total_vias`
- `total_zones`
- `fresh_board`
  如果没有 track segment，会认为是“新板”。
- `copper_layers`
  板上铜层名列表。
- `zones`
  每个 zone 的摘要。
- `fanout_candidates`
  可能需要先做 fanout 的器件。
- `high_speed_hints`
  高速网络提示。
- `footprints`
  每个 footprint 的简要列表。

**补充说明**

- 这里的 `fanout_candidates[].recommended_tool` 返回的是脚本名：
  - `qfn_fanout.py`
  - `bga_fanout.py`
- 在 session 分析里，类似字段会返回 MCP tool 名：
  - `run_qfn_fanout`
  - `run_bga_fanout`

---

### `router_environment_status`

**作用**  
检查当前 Python 环境、嵌入式工具目录、Rust router 模块是否可用。

**参数**

- 无参数。

**返回**

- `python_executable`
  当前 Python 解释器。
- `project_root`
- `mcp_root`
- `embedded_tools_root`
- `tools_root`
  当前实际使用的工具目录。
- `tools_root_source`
  来源，通常是 `embedded` 或 `env`。
- `tools_root_exists`
- `rust_router_root`
- `rust_router_root_exists`
- `grid_router_module_exists`
- `grid_router_importable`
  能不能 import `grid_router`。
- `grid_router_version`
  仅 import 成功时返回。
- `grid_router_file`
  仅 import 成功时返回。
- `grid_router_error`
  import 失败时返回错误信息。

---

### `validate_kicad_pcb`

**作用**  
检查一个 `.kicad_pcb` 文件在当前 KiCad 版本语法下是不是合法，并尝试解析它。

**参数**

- `pcb_path` (`str`, 必填)
  要校验的板文件。
- `use_pcbnew_if_available` (`bool`, 默认 `false`)
  如果环境里有 `pcbnew`，是否顺带用 `pcbnew.LoadBoard(...)` 再做一次加载测试。

**返回**

- 返回结构见上面的 `file_validation`。

---

## 5.2 Session 工作流类

### `create_routing_session`

**作用**  
创建一个路由会话。后面分析、规划、执行都可以围绕这个 session 来做。

**参数**

- `board_path` (`str`, 必填)
  原始板文件路径。
- `session_name` (`str | None`, 默认 `None`)
  会话名称；不传就用自动生成的 `session_id`。
- `output_dir` (`str | None`, 默认 `None`)
  产物输出目录；不传就写到 session 自己的 `artifacts` 目录。
- `description` (`str | None`, 默认 `None`)
  这次会话的说明文字。
- `coordinate_mode` (`str`, 默认 `"algorithm_only"`)
  坐标模式。一般先用 `algorithm_only`。
- `placement_mode` (`str`, 默认 `"auto"`)
  放置模式。通常 `auto` 就够用。

**返回**

- 返回完整 `session` 对象。

---

### `list_routing_sessions`

**作用**  
列出当前所有可恢复的 routing session。

**参数**

- 无参数。

**返回**

- `session_count`
  会话数量。
- `sessions`
  每个元素是 session 摘要，包含：
  - `session_id`
  - `session_name`
  - `status`
  - `board_path`
  - `working_board_path`
  - `coordinate_mode`
  - `placement_mode`
  - `updated_at`
  - `created_at`

---

### `get_routing_session`

**作用**  
取回某个 session 的完整状态。

**参数**

- `session_id` (`str`, 必填)
  目标会话 ID。

**返回**

- 返回完整 `session` 对象。

---

### `analyze_board_for_llm`

**作用**  
对 session 当前板子做结构化分析，并把结果存进 session。

**参数**

- `session_id` (`str`, 必填)
  目标会话 ID。
- `board_path_override` (`str | None`, 默认 `None`)
  如果想临时指定另外一个板文件来分析，可以传这个；不传就分析 `working_board_path`。

**返回**

- `session_id`
- `analysis`
  结构见前面的 `analysis` 说明。
- `working_board_path`
  本次分析对应的工作板路径。

---

### `propose_routing_plan`

**作用**  
把“目标 + 约束”转换成一个可执行 routing plan。

**参数**

- `session_id` (`str`, 必填)
  目标会话 ID。
- `objective` (`str`, 默认 `"autoroute"`)
  目标描述，比如 `autoroute`、`cleanup`、`route power first` 之类。
- `constraints` (`dict | None`, 默认 `None`)
  路由约束字典。支持的主要键有：
  - `placement_mode`
    是否自动插入放置步骤。常见值：
    - `auto`
    - `force`
  - `place_zero_only`
    自动放置时是否只处理原点/越界器件。
  - `placement_gap`
    footprint 间距。
  - `board_margin`
    与板边保持的 margin。
  - `placement_grid_step`
    放置网格步长。
  - `route_mode`
    路由模式；源码里会影响默认的 `max_iterations` / `max_ripup`。
  - `route_diff_pairs_first`
    是否先布差分对。
  - `prefer_existing_zones`
    是否优先保留已有铜皮/zone。
  - `use_power_planes`
    是否显式要求建 plane；`null` 表示让系统自己判断。
  - `coordinate_mode`
    路由模式，常见为 `algorithm_only` 或 `llm_coordinates`。
  - `layers`
    指定允许使用的铜层。
  - `track_width`
    默认线宽。
  - `clearance`
    默认间距。
  - `power_track_width`
    电源网络推荐宽线宽。
  - `diff_pair_gap`
    差分对间距。
  - `max_iterations`
    底层路由算法最大迭代次数。
  - `max_ripup`
    rip-up 重试强度。
  - `no_bga_zones`
    指定禁用 BGA zone 的器件/区域。
  - `notes`
    备注文本。

**返回**

- `session_id`
- `working_board_path`
- `analysis_snapshot`
  `plan` 内嵌分析摘要的快捷访问。
- `plan`
  完整 plan 结构，见上面的 `plan` 说明。

---

### `apply_routing_plan`

**作用**  
按顺序执行一个 routing plan，并把结果写回 session。

**参数**

- `session_id` (`str`, 必填)
  目标会话 ID。
- `plan` (`dict | None`, 默认 `None`)
  如果传了，就执行这个 plan；不传就执行 session 里保存的 `proposed_plan`。
- `stop_after_step` (`int | None`, 默认 `None`)
  执行到第几步就停，适合分步调试。
- `continue_on_error` (`bool`, 默认 `false`)
  某一步失败后是否继续往后执行。

**返回**

- `session_id`
- `execution_id`
  本次执行 ID。
- `status`
  `completed`、`partial`、`failed` 等。
- `working_board_path`
  执行完成后当前工作板。
- `executed_steps`
  实际执行了多少步。
- `plan_id`
- `latest_checks`
  最近一次各类检查结果。
- `last_step`
  最后一步执行记录，包含：
  - `index`
  - `step_id`
  - `kind`
  - `reason`
  - `input_board`
  - `output_board`
  - `parameters`
  - `started_at`
  - `completed_at`
  - `status`
  - `result`

---

### `analyze_session_failures`

**作用**  
把最近一次 plan 执行失败的原因总结出来，方便下一步修复。

**参数**

- `session_id` (`str`, 必填)
  目标会话 ID。

**返回**

- `session_id`
- `status`
  通常是：
  - `no-execution-history`
  - `healthy`
  - `needs-attention`
- `latest_execution_id`
  最近一次执行 ID。
- `failed_steps`
  每个失败步骤包含：
  - `step_id`
  - `kind`
  - `reason`
  - `success`
  - `returncode`
  - `failed_single`
  - `failed_multipoint`
  - `stdout_tail`
  - `stderr_tail`
  - `log_path`
- `next_actions`
  系统建议的下一步动作。
- `working_board_path`
- `coordinate_mode`
- `placement_mode`

---

### `suggest_next_routing_actions`

**作用**  
基于 session 当前状态，给出“下一步建议做什么”。

**参数**

- `session_id` (`str`, 必填)
  目标会话 ID。

**返回**

- `session_id`
- `status`
- `working_board_path`
- `coordinate_mode`
- `placement_mode`
- `latest_execution_summary`
  本质上就是一份失败/执行总结。
- `suggestions`
  建议动作列表。每个元素一般有：
  - `action`
  - `reason`
  - 以及可选的 `references`、`coordinate_mode` 等补充信息

---

## 5.3 Footprint 放置类

### `auto_place_footprints`

**作用**  
对一块板文件直接做一次启发式自动摆放，不依赖 session。

**参数**

- `pcb_path` (`str`, 必填)
  输入板文件路径。
- `output_path` (`str | None`, 默认 `None`)
  输出板文件路径。不传时会自动生成一个 `<原文件名>_placed.kicad_pcb`。
- `references` (`list[str] | None`, 默认 `None`)
  只摆放这些指定位号；不传就按规则自动挑。
- `zero_only` (`bool`, 默认 `true`)
  是否只处理“明显需要重摆”的器件，比如在原点、越界、严重重叠分组。
- `placement_gap` (`float`, 默认 `1.0`)
  footprint 之间预留间距，单位 mm。
- `board_margin` (`float`, 默认 `0.25`)
  离板边预留的 margin，单位 mm。
- `grid_step` (`float`, 默认 `0.25`)
  自动摆放时的吸附网格。

**返回**

这个 tool 的返回比普通脚本丰富，核心字段有：

- `success`
- `input_path`
- `output_path`
- `placement_plan`
  这次自动生成的摆放方案。
- `validation`
  放置校验结果。
- `placement_count`
  仅成功写出时会有。
- `board_summary`
  仅成功写出时会有，包含 `total_footprints`、`total_segments`、`total_vias`。
- `placed_references`
  实际参与自动放置的器件位号。
- `skipped_references`
  没有被自动放置的位号。
- `strategy`
  当前是 `heuristic_connectivity`。
- `reasoning`
  每个器件为什么放在那个位置的简要说明。
- `flow`
  推断的主布局流向，例如 `left_to_right`。
- `error`
  如果失败会给出错误信息。

---

### `build_llm_placement_context`

**作用**  
构建给 LLM 用的摆放上下文，让模型自己决定器件坐标。

**参数**

- `session_id` (`str`, 必填)
  目标会话 ID。
- `references` (`list[str] | None`, 默认 `None`)
  只给这些位号构建上下文。
- `include_full_context` (`bool`, 默认 `false`)
  是否直接把完整上下文也放到返回里；不传时只返回 summary，并把完整内容保存在 session。

**返回**

- `session_id`
- `working_board_path`
- `placement_mode`
  如果原来是 `auto`，这里通常会切到 `llm_placement`。
- `context_summary`
  摘要字段包括：
  - `available`
  - `pcb_path`
  - `board`
  - `placement_hints`
  - `footprint_count`
  - `references`
  - `roles`
  - `schema_rules`
- `stored_in_session`
  是否已保存进 session。
- `context`
  只有 `include_full_context=true` 时才返回。完整上下文包含：
  - `pcb_path`
  - `board`
  - `placement_hints`
  - `footprints`
  - `connections`
  - `schema_hint`

---

### `get_llm_placement_context`

**作用**  
从 session 里把之前保存的 placement context 读出来。

**参数**

- `session_id` (`str`, 必填)
  目标会话 ID。
- `include_full_context` (`bool`, 默认 `false`)
  是否把完整上下文一起返回。

**返回**

- `session_id`
- `working_board_path`
- `placement_mode`
- `context_summary`
- `stored_in_session`
- `context`
  只有 `include_full_context=true` 且 session 里确实有上下文时返回。

---

### `validate_llm_placement_plan`

**作用**  
检查一份 LLM 摆放方案是否合法，但不改板文件。

**参数**

- `session_id` (`str`, 必填)
  目标会话 ID。
- `placement_plan` (`dict`, 必填)
  摆放方案，结构见前面的 `placement_plan`。
- `placement_gap` (`float`, 默认 `1.0`)
  校验时要求的最小间距。
- `board_margin` (`float`, 默认 `0.25`)
  校验时离板边的 margin。

**返回**

- `session_id`
- `working_board_path`
- `validation`
  放置校验结果，结构见前文。

---

### `apply_llm_placement_plan`

**作用**  
把一份 LLM 给出的摆放方案真正写进板文件，并更新 session。

**参数**

- `session_id` (`str`, 必填)
  目标会话 ID。
- `placement_plan` (`dict`, 必填)
  摆放方案。
- `output_board` (`str | None`, 默认 `None`)
  输出板路径。不传时会自动在 session 输出目录里生成文件。
- `placement_gap` (`float`, 默认 `1.0`)
- `board_margin` (`float`, 默认 `0.25`)
- `refresh_analysis` (`bool`, 默认 `true`)
  成功后是否重新分析板子。

**返回**

- `session_id`
- `placement_mode`
- `working_board_path`
- `result`
  `result` 的核心字段有：
  - `success`
  - `input_path`
  - `output_path`
  - `validation`
  - `placement_count`
  - `board_summary`
  - `file_validation`
  - `error`

---

### `auto_place_session_footprints`

**作用**  
在 session 里对当前工作板做自动摆放。这通常是实际流程里更推荐的放置入口。

**参数**

- `session_id` (`str`, 必填)
  目标会话 ID。
- `output_board` (`str | None`, 默认 `None`)
  输出板路径；不传时自动生成。
- `references` (`list[str] | None`, 默认 `None`)
  指定只摆某些器件。
- `zero_only` (`bool`, 默认 `true`)
  是否只处理明显需要重摆的器件。
- `placement_gap` (`float`, 默认 `1.0`)
- `board_margin` (`float`, 默认 `0.25`)
- `grid_step` (`float`, 默认 `0.25`)
- `refresh_analysis` (`bool`, 默认 `true`)
  成功后是否自动刷新 session 的 `analysis`。

**返回**

- `session_id`
- `working_board_path`
- `result`
  里面基本就是 `auto_place_footprints` 的结果，再额外附带：
  - `file_validation`

---

## 5.4 坐标级布线类

### `build_llm_coordinate_context`

**作用**  
构建给 LLM 用的“几何布线上下文”，让模型按点坐标输出走线。

**参数**

- `session_id` (`str`, 必填)
  目标会话 ID。
- `nets` (`list[str] | None`, 默认 `None`)
  精确指定要处理的网络名。
- `net_patterns` (`list[str] | None`, 默认 `None`)
  通配符模式，底层用 `fnmatch` 匹配，比如 `USB*`。
- `max_pads_per_net` (`int`, 默认 `12`)
  每个网络最多返回多少 pad，防止上下文爆炸。
- `max_segments_per_net` (`int`, 默认 `20`)
- `max_vias_per_net` (`int`, 默认 `12`)
- `max_stubs_per_net` (`int`, 默认 `12`)
- `include_full_context` (`bool`, 默认 `false`)
  是否把完整上下文直接返回。

**返回**

- `session_id`
- `working_board_path`
- `coordinate_mode`
  如果之前是 `algorithm_only`，这里通常会切到 `llm_coordinates`。
- `context_summary`
  摘要字段包括：
  - `available`
  - `pcb_path`
  - `board`
  - `defaults`
  - `net_count`
  - `net_names`
  - `per_net_counts`
  - `schema_rules`
- `stored_in_session`
- `context`
  仅 `include_full_context=true` 时返回。完整上下文包含：
  - `pcb_path`
  - `board`
  - `defaults`
  - `nets`
  - `schema_hint`

---

### `get_llm_coordinate_context`

**作用**  
把 session 里已经保存的坐标布线上下文取出来。

**参数**

- `session_id` (`str`, 必填)
  目标会话 ID。
- `include_full_context` (`bool`, 默认 `false`)
  是否把完整上下文一起返回。

**返回**

- `session_id`
- `working_board_path`
- `coordinate_mode`
- `context_summary`
- `stored_in_session`
- `context`
  仅 `include_full_context=true` 且上下文存在时返回。

---

### `validate_llm_coordinate_plan`

**作用**  
检查一份 LLM 坐标走线计划是否合法，但不修改板文件。

**参数**

- `session_id` (`str`, 必填)
  目标会话 ID。
- `coordinate_plan` (`dict`, 必填)
  坐标走线方案，结构见前文。
- `endpoint_tolerance` (`float`, 默认 `0.2`)
  端点贴合到已有 pad/via/segment/stub 的容差，单位 mm。
- `grid_tolerance` (`float`, 默认 `0.01`)
  允许偏离 grid 的容差。

**返回**

- `session_id`
- `working_board_path`
- `validation`
  坐标校验结果，结构见前文。

---

### `apply_llm_coordinate_plan`

**作用**  
把 LLM 输出的坐标走线计划真正写入板文件，并可选地立刻做检查。

**参数**

- `session_id` (`str`, 必填)
  目标会话 ID。
- `coordinate_plan` (`dict`, 必填)
  坐标走线方案。
- `output_board` (`str | None`, 默认 `None`)
  输出板路径；不传时自动生成。
- `endpoint_tolerance` (`float`, 默认 `0.2`)
- `grid_tolerance` (`float`, 默认 `0.01`)
- `run_checks` (`bool`, 默认 `true`)
  成功后是否自动跑连通性、DRC、orphan stub 检查。
- `clearance` (`float | None`, 默认 `None`)
  如果要自动做 DRC，这里可以显式指定 clearance；不传就尝试从 session 的 `constraints` 里拿。

**返回**

- `session_id`
- `coordinate_mode`
- `working_board_path`
- `result`
  核心字段有：
  - `success`
  - `pcb_path`
  - `output_path`
  - `track_count`
  - `via_count`
  - `validation`
  - `file_validation`
  - `auto_repair`
- `checks`
  如果 `run_checks=true` 且写板成功，会包含：
  - `check_connectivity`
  - `check_drc`
  - `check_orphan_stubs`
  这三个检查结果都走统一 `_run_script` 结构。

**补充说明**

- 这个 tool 会自动尝试修复 KiCad 9/10 之间的 net 语法差异。
- 修复结果会体现在 `result.auto_repair` 里。

---

## 5.5 底层脚本封装类

### `run_routing_script`

**作用**  
直接运行一个受支持的底层脚本。适合“现成 dedicated tool 没暴露某个 flag”的场景。

**参数**

- `script_name` (`str`, 必填)
  允许的脚本名只有这些：
  - `build_router.py`
  - `list_nets.py`
  - `bga_fanout.py`
  - `qfn_fanout.py`
  - `route.py`
  - `route_diff.py`
  - `route_planes.py`
  - `route_disconnected_planes.py`
  - `check_connected.py`
  - `check_drc.py`
  - `check_orphan_stubs.py`
- `args` (`list[str] | None`, 默认 `None`)
  原始命令行参数列表。
- `timeout_seconds` (`int`, 默认 `600`)
  超时时间。
- `cwd` (`str | None`, 默认 `None`)
  运行目录；不传时由服务端决定默认目录。

**返回**

- 统一 `_run_script` 结构。

---

### `build_rust_router`

**作用**  
编译或清理 Rust router 模块。

**参数**

- `clean` (`bool`, 默认 `false`)
  是否执行清理模式，相当于给底层 `build_router.py` 传 `--clean`。
- `timeout_seconds` (`int`, 默认 `900`)
  编译超时。

**返回**

- 统一 `_run_script` 结构。

---

### `list_nets`

**作用**  
查看板上的网络信息，可以做 component pad mapping、差分对探测、电源网筛选等。

**参数**

- `pcb_path` (`str`, 必填)
  板文件路径。
- `component` (`str | None`, 默认 `None`)
  只看某个元件。
- `pads` (`bool`, 默认 `false`)
  是否展开 pad 映射。
- `diff_pairs` (`bool`, 默认 `false`)
  是否重点看差分对。
- `power` (`bool`, 默认 `false`)
  是否重点看电源网。
- `top` (`int`, 默认 `10`)
  返回前多少项。
- `pattern` (`str | None`, 默认 `None`)
  网络名模式过滤。
- `extra_args` (`list[str] | None`, 默认 `None`)
  额外原始参数。
- `timeout_seconds` (`int`, 默认 `120`)

**返回**

- 统一 `_run_script` 结构。

---

### `run_bga_fanout`

**作用**  
对 BGA / PGA 器件做 escape routing。

**参数**

- `pcb_path` (`str`, 必填)
  输入板文件。
- `output_path` (`str | None`, 默认 `None`)
  输出板文件。
- `component` (`str | None`, 默认 `None`)
  指定只对某个器件做 fanout。
- `layers` (`list[str] | None`, 默认 `None`)
  fanout 可使用的层。
- `nets` (`list[str] | None`, 默认 `None`)
  只处理这些网络。
- `diff_pairs` (`list[str] | None`, 默认 `None`)
  差分对网络列表。
- `track_width` (`float | None`, 默认 `None`)
- `clearance` (`float | None`, 默认 `None`)
- `via_size` (`float | None`, 默认 `None`)
- `via_drill` (`float | None`, 默认 `None`)
- `diff_pair_gap` (`float | None`, 默认 `None`)
- `exit_margin` (`float | None`, 默认 `None`)
  fanout 出口留量。
- `primary_escape` (`str | None`, 默认 `None`)
  首选逃线方向/策略，对应底层 `--primary-escape`。
- `force_escape_direction` (`bool`, 默认 `false`)
  是否强制使用指定逃线方向。
- `rebalance_escape` (`bool`, 默认 `false`)
  是否启用 escape 重平衡。
- `check_for_previous` (`bool`, 默认 `false`)
  是否检查之前已有的 fanout 痕迹。
- `no_inner_top_layer` (`bool`, 默认 `false`)
  是否禁用某个顶部内层参与 fanout。
- `extra_args` (`list[str] | None`, 默认 `None`)
- `timeout_seconds` (`int`, 默认 `300`)

**返回**

- 统一 `_run_script` 结构。

---

### `run_qfn_fanout`

**作用**  
给 QFN / QFP 这类四周出脚封装做 stub fanout。

**参数**

- `pcb_path` (`str`, 必填)
- `output_path` (`str | None`, 默认 `None`)
- `component` (`str | None`, 默认 `None`)
- `layer` (`str | None`, 默认 `None`)
  fanout 所在层。
- `width` (`float | None`, 默认 `None`)
  fanout 线宽。
- `extension` (`float | None`, 默认 `None`)
  fanout stub 延伸长度。
- `nets` (`list[str] | None`, 默认 `None`)
- `extra_args` (`list[str] | None`, 默认 `None`)
- `timeout_seconds` (`int`, 默认 `300`)

**返回**

- 统一 `_run_script` 结构。

---

### `route_single_ended`

**作用**  
执行单端网络自动布线，对应底层 `route.py`。

**参数**

- `input_pcb` (`str`, 必填)
  输入板文件。
- `output_pcb` (`str | None`, 默认 `None`)
  输出板文件。
- `nets` (`list[str] | None`, 默认 `None`)
  只布这些网络；常见也会传 `["*"]` 表示全部。
- `component` (`str | None`, 默认 `None`)
  只围绕某个元件相关网络。
- `ordering` (`str | None`, 默认 `None`)
  网络处理顺序策略。
- `direction` (`str | None`, 默认 `None`)
  主布线方向偏好。
- `layers` (`list[str] | None`, 默认 `None`)
  允许使用的层。
- `no_bga_zones` (`list[str] | None`, 默认 `None`)
  哪些器件/区域不要建 BGA zones。
- `track_width` (`float | None`, 默认 `None`)
- `impedance` (`float | None`, 默认 `None`)
- `clearance` (`float | None`, 默认 `None`)
- `via_size` (`float | None`, 默认 `None`)
- `via_drill` (`float | None`, 默认 `None`)
- `power_nets` (`list[str] | None`, 默认 `None`)
  哪些网络按电源网处理。
- `power_nets_widths` (`list[float] | None`, 默认 `None`)
  与 `power_nets` 一一对应的宽线宽。
- `grid_step` (`float | None`, 默认 `None`)
- `max_iterations` (`int | None`, 默认 `None`)
- `max_ripup` (`int | None`, 默认 `None`)
- `add_teardrops` (`bool`, 默认 `false`)
- `debug_lines` (`bool`, 默认 `false`)
- `verbose` (`bool`, 默认 `false`)
- `extra_args` (`list[str] | None`, 默认 `None`)
- `timeout_seconds` (`int`, 默认 `900`)

**返回**

- 统一 `_run_script` 结构。

---

### `route_differential_pairs`

**作用**  
执行差分对自动布线，对应底层 `route_diff.py`。

**参数**

- `input_pcb` (`str`, 必填)
- `output_pcb` (`str | None`, 默认 `None`)
- `nets` (`list[str] | None`, 默认 `None`)
  差分对网络名列表。
- `ordering` (`str | None`, 默认 `None`)
- `direction` (`str | None`, 默认 `None`)
- `layers` (`list[str] | None`, 默认 `None`)
- `no_bga_zones` (`list[str] | None`, 默认 `None`)
- `track_width` (`float | None`, 默认 `None`)
- `impedance` (`float | None`, 默认 `None`)
- `clearance` (`float | None`, 默认 `None`)
- `via_size` (`float | None`, 默认 `None`)
- `via_drill` (`float | None`, 默认 `None`)
- `diff_pair_gap` (`float | None`, 默认 `None`)
- `max_iterations` (`int | None`, 默认 `None`)
- `max_ripup` (`int | None`, 默认 `None`)
- `diff_pair_intra_match` (`bool`, 默认 `false`)
  是否启用对内匹配。
- `no_gnd_vias` (`bool`, 默认 `false`)
  是否禁用自动 GND via。
- `add_teardrops` (`bool`, 默认 `false`)
- `debug_lines` (`bool`, 默认 `false`)
- `verbose` (`bool`, 默认 `false`)
- `extra_args` (`list[str] | None`, 默认 `None`)
- `timeout_seconds` (`int`, 默认 `900`)

**返回**

- 统一 `_run_script` 结构。

---

### `create_power_planes`

**作用**  
创建铜皮 plane，或者给 GND 加回流 via，对应底层 `route_planes.py`。

**参数**

- `input_pcb` (`str`, 必填)
- `output_pcb` (`str | None`, 默认 `None`)
- `nets` (`list[str] | None`, 默认 `None`)
  主要 plane 的网络。
- `plane_layers` (`list[str] | None`, 默认 `None`)
  plane 生成在哪些层。
- `layers` (`list[str] | None`, 默认 `None`)
  工具允许操作的层。
- `track_width` (`float | None`, 默认 `None`)
- `clearance` (`float | None`, 默认 `None`)
- `zone_clearance` (`float | None`, 默认 `None`)
  zone 自己的 clearance。
- `via_size` (`float | None`, 默认 `None`)
- `via_drill` (`float | None`, 默认 `None`)
- `grid_step` (`float | None`, 默认 `None`)
- `power_nets` (`list[str] | None`, 默认 `None`)
  作为宽线电源网处理的网络。
- `power_nets_widths` (`list[float] | None`, 默认 `None`)
- `add_gnd_vias` (`bool`, 默认 `false`)
  是否添加回流 GND vias。
- `gnd_via_net` (`str | None`, 默认 `None`)
  GND via 所属网络。
- `gnd_via_distance` (`float | None`, 默认 `None`)
  GND via 间距。
- `rip_blocker_nets` (`bool`, 默认 `false`)
  是否 rip 掉阻挡 plane 的网络。
- `reroute_ripped_nets` (`bool`, 默认 `false`)
  被 rip 掉后是否重布。
- `add_teardrops` (`bool`, 默认 `false`)
- `debug_lines` (`bool`, 默认 `false`)
- `verbose` (`bool`, 默认 `false`)
- `extra_args` (`list[str] | None`, 默认 `None`)
- `timeout_seconds` (`int`, 默认 `900`)

**返回**

- 统一 `_run_script` 结构。

---

### `repair_disconnected_planes`

**作用**  
修复被切碎或断开的铜皮区域，对应底层 `route_disconnected_planes.py`。

**参数**

- `input_pcb` (`str`, 必填)
- `output_pcb` (`str | None`, 默认 `None`)
- `nets` (`list[str] | None`, 默认 `None`)
- `plane_layers` (`list[str] | None`, 默认 `None`)
- `layers` (`list[str] | None`, 默认 `None`)
- `track_width` (`float | None`, 默认 `None`)
- `clearance` (`float | None`, 默认 `None`)
- `zone_clearance` (`float | None`, 默认 `None`)
- `via_size` (`float | None`, 默认 `None`)
- `via_drill` (`float | None`, 默认 `None`)
- `grid_step` (`float | None`, 默认 `None`)
- `max_iterations` (`int | None`, 默认 `None`)
- `dry_run` (`bool`, 默认 `false`)
  只模拟，不实际写结果。
- `debug_lines` (`bool`, 默认 `false`)
- `verbose` (`bool`, 默认 `false`)
- `extra_args` (`list[str] | None`, 默认 `None`)
- `timeout_seconds` (`int`, 默认 `900`)

**返回**

- 统一 `_run_script` 结构。

---

### `check_connectivity`

**作用**  
检查网络是否真正连通，对应底层 `check_connected.py`。

**参数**

- `pcb_path` (`str`, 必填)
- `nets` (`list[str] | None`, 默认 `None`)
  只检查这些网络。
- `component` (`str | None`, 默认 `None`)
  只看某个器件相关的连通性。
- `tolerance` (`float | None`, 默认 `None`)
  连通判断容差。
- `quiet` (`bool`, 默认 `false`)
- `verbose` (`bool`, 默认 `false`)
- `routed_only` (`bool`, 默认 `false`)
  只检查已经布过的部分。
- `extra_args` (`list[str] | None`, 默认 `None`)
- `timeout_seconds` (`int`, 默认 `120`)

**返回**

- 统一 `_run_script` 结构。

---

### `check_drc`

**作用**  
做 DRC / clearance 相关检查，对应底层 `check_drc.py`。

**参数**

- `pcb_path` (`str`, 必填)
- `clearance` (`float | None`, 默认 `None`)
- `hole_to_hole_clearance` (`float | None`, 默认 `None`)
- `board_edge_clearance` (`float | None`, 默认 `None`)
- `clearance_margin` (`float | None`, 默认 `None`)
- `nets` (`list[str] | None`, 默认 `None`)
  只检查这些网络。
- `debug_lines` (`bool`, 默认 `false`)
- `quiet` (`bool`, 默认 `false`)
- `extra_args` (`list[str] | None`, 默认 `None`)
- `timeout_seconds` (`int`, 默认 `120`)

**返回**

- 统一 `_run_script` 结构。

---

### `check_orphan_stubs`

**作用**  
检测悬空的 trace stub，对应底层 `check_orphan_stubs.py`。

**参数**

- `input_pcb` (`str`, 必填)
- `compare_file` (`str | None`, 默认 `None`)
  对比参考板文件。
- `net` (`str | None`, 默认 `None`)
  只查某个网络。
- `layer` (`str | None`, 默认 `None`)
  只查某层。
- `compare` (`bool`, 默认 `false`)
  是否启用对比模式。
- `extra_args` (`list[str] | None`, 默认 `None`)
- `timeout_seconds` (`int`, 默认 `120`)

**返回**

- 统一 `_run_script` 结构。

---

## 6. 推荐工作流

如果你只是想“让系统自己把板子分析、规划、布线尽量走通”，推荐顺序是：

1. `router_environment_status`
2. 如果 `grid_router_importable=false`，调用 `build_rust_router`
3. `create_routing_session`
4. `analyze_board_for_llm`
5. 如果 `analysis.placement_hints.needs_placement=true`，调用 `auto_place_session_footprints`
6. 再次 `analyze_board_for_llm`
7. `propose_routing_plan`
8. `apply_routing_plan`
9. `analyze_session_failures`
10. `suggest_next_routing_actions`

如果你只想“摆元件，不布线”，主线是：

1. `create_routing_session`
2. `analyze_board_for_llm`
3. `auto_place_session_footprints`

如果你想“让 LLM 亲自给坐标走线”，主线是：

1. `create_routing_session`
2. `analyze_board_for_llm`
3. 必要时先做放置
4. `build_llm_coordinate_context`
5. `validate_llm_coordinate_plan`
6. `apply_llm_coordinate_plan`

---

## 7. `.claude/skills` 里面的技能解读

这个目录里现在能看到 5 个技能：

- `route-kicad-pcb-mcp`
- `place-kicad-footprints-mcp`
- `compute-kicad-route-coordinates`
- `find-skills`
- `skill-creator`

前 3 个是本项目最相关的 PCB 技能。  
后 2 个更像“通用技能”，不是专门给 KiCad 项目写的。

---

### `route-kicad-pcb-mcp`

**一句话理解**  
这是“总控技能”。如果目标是把一块 KiCad 板从分析一路推进到布线、检查、重试，它就是主技能。

**它主要负责什么**

- 调用本地 MCP server，而不是随便跑 shell
- 用 session 工作流把整件事串起来
- 先分析、后放置、再规划、再执行
- 失败了以后，再读失败摘要并决定下一步

**它推荐的主流程**

```text
create_routing_session
-> analyze_board_for_llm
-> auto_place_session_footprints（如果需要）
-> analyze_board_for_llm
-> propose_routing_plan
-> apply_routing_plan
-> analyze_session_failures
-> suggest_next_routing_actions
```

**这个技能强调的重点**

- 先分析，不要一上来就乱布线
- 如果 footprint 在 `(0,0)` 或者出板边，先摆放
- 有 BGA / QFN / QFP 先 fanout
- 差分对先于单端线
- 默认优先用 session 流程，不鼓励直接乱调底层脚本
- 失败后优先看日志和失败摘要，不要盲改参数

**适合什么时候用**

- 你想“整体推进一块板”
- 你希望让模型自动决定顺序和策略
- 你想保留过程上下文和历史记录

**不太适合什么时候用**

- 你只想单独摆几个器件
- 你只想手工微调 1~2 根关键线的坐标

---

### `place-kicad-footprints-mcp`

**一句话理解**  
这是“先摆元件”的专项技能。

**它主要负责什么**

- 判断当前板是不是应该先摆元件
- 调 `auto_place_session_footprints` 做默认自动摆放
- 如果自动摆放不够，再切到 `build_llm_placement_context -> validate -> apply`

**它的核心理念**

- 不要死记某块板的旧坐标
- 摆放要根据当前板形、器件尺寸、网络角色、连接关系实时推断
- KiCad 的 `(at x y)` 是 footprint 原点，不一定是封装中心
- 连接器尽量靠边
- 电源 IC 要处在电源流的关键位置
- 电感、电容、反馈件要围绕它服务的 IC 引脚放
- 布局不能只“不重叠”，还要给后续布线留通道

**适合什么时候用**

- 板子还是 fresh board
- 一堆 footprint 都堆在 `(0,0)`
- footprint 跑到板框外面去了
- 你明确想“先放置，再布线”

**它和 `route-kicad-pcb-mcp` 的关系**

- 它更专注于 placement
- 一般可以看作前置步骤
- 摆完以后，再回到 `route-kicad-pcb-mcp`

---

### `compute-kicad-route-coordinates`

**一句话理解**  
这是“让 LLM 直接出走线点坐标”的专项技能。

**它主要负责什么**

- 为少量关键网络构建坐标级几何上下文
- 让模型输出结构化 `coordinate_plan`
- 强调走线几何质量，而不是只求连上

**它特别强调的规则**

- 只适合少量网络，不适合整板大规模 autoroute
- 起点终点要贴到真实导体上
- 同层转角必须是钝角
- 不允许 `horizontal -> vertical` 直接拐 90 度
- 该插 via 的地方要用“同 XY、不同 layer”的点对表示
- 每条网络要单独选线宽，不要全板一刀切

**它最重要的额外思想**

它不只是让你“给坐标”，还要求你先按电气角色给网络分类：

- 电源输入
- 开关节点
- 电源输出
- 地回流 stub
- 控制/普通信号
- 高速/差分

然后按网络角色选不同线宽。

**适合什么时候用**

- 只想手工修 1~2 根关键线
- 自动布线大体完成了，剩下一点点难线
- 想让模型直接表达明确几何，而不是只给策略

**不太适合什么时候用**

- BGA 密集逃线
- 很长的总线
- 整板完全未布线

---

### `find-skills`

**一句话理解**  
这是一个“去技能市场里找现成技能”的通用技能，不是 PCB 专项技能。

**它主要负责什么**

- 当用户说“有没有 skill 能做 X”时，去搜索技能
- 用 `npx skills find ...` 查找
- 告诉用户技能来源和安装命令
- 安装前必须提醒安全风险并征得确认

**它的使用场景**

- 你想扩展 Codex/Claude 的能力
- 你怀疑社区里已经有人做了现成技能

**这个技能的重点**

- 第三方 skill 本质上是代码，要提醒安全风险
- 不能静默安装
- 要先给用户看来源链接

---

### `skill-creator`

**一句话理解**  
这是一个“帮你创建、测试、迭代技能”的通用技能。

**它主要负责什么**

- 帮用户定义 skill 的目标、触发条件、输出格式
- 写 `SKILL.md`
- 设计测试 prompt
- 跑评估、看结果、收集反馈
- 迭代优化 skill
- 最后再优化 description，让 skill 更容易被正确触发

**它的工作方法**

- 先搞清楚 skill 要做什么
- 再写草稿
- 再做测试
- 再根据反馈改
- 一轮一轮迭代

**这个技能特别适合**

- 你想把某个固定工作流沉淀成一个 skill
- 你已经有 skill 初稿，但想做系统化打磨
- 你想给 skill 做 benchmark / eval

**和本项目的关系**

- 它不是路由技能本身
- 更像“造技能的工具”
- 如果以后你想把这个仓库里的某套 routing / placement 流程再封装成新的 skill，它会很有用

---

## 8. 这些技能之间怎么搭配

最常见的搭配关系可以这么理解：

- **`route-kicad-pcb-mcp`**
  主流程技能，负责整体推进。
- **`place-kicad-footprints-mcp`**
  前置专项技能，先把元件摆明白。
- **`compute-kicad-route-coordinates`**
  后期精修技能，只处理少量需要人工几何控制的网络。
- **`find-skills`**
  去外部找更多能力。
- **`skill-creator`**
  自己造新技能或改现有技能。

一句话版本：

- 想“整体跑通”就用 `route-kicad-pcb-mcp`
- 想“先摆元件”就用 `place-kicad-footprints-mcp`
- 想“手工级坐标微调”就用 `compute-kicad-route-coordinates`

---

## 9. 最后给一个快速索引

如果你只是想记住每个 tool 最像什么，可以看这个速查表：

- `inspect_pcb`
  看板子体检报告
- `router_environment_status`
  看环境有没有准备好
- `create_routing_session`
  开一个任务会话
- `list_routing_sessions`
  看有哪些任务会话
- `get_routing_session`
  看某个任务会话详情
- `analyze_board_for_llm`
  给模型做结构化分析
- `propose_routing_plan`
  生成执行计划
- `apply_routing_plan`
  跑计划
- `analyze_session_failures`
  总结失败原因
- `suggest_next_routing_actions`
  建议下一步
- `auto_place_footprints`
  单次自动摆件
- `build_llm_placement_context`
  给模型准备摆件上下文
- `get_llm_placement_context`
  读取摆件上下文
- `validate_llm_placement_plan`
  检查摆件方案
- `apply_llm_placement_plan`
  应用摆件方案
- `auto_place_session_footprints`
  在 session 里自动摆件
- `build_llm_coordinate_context`
  给模型准备坐标走线上下文
- `get_llm_coordinate_context`
  读取坐标上下文
- `validate_kicad_pcb`
  检查板文件是否合法
- `validate_llm_coordinate_plan`
  检查坐标走线方案
- `apply_llm_coordinate_plan`
  应用坐标走线方案
- `run_routing_script`
  直接跑底层脚本
- `build_rust_router`
  编译 Rust router
- `list_nets`
  看网络信息
- `run_bga_fanout`
  做 BGA fanout
- `run_qfn_fanout`
  做 QFN/QFP fanout
- `route_single_ended`
  单端自动布线
- `route_differential_pairs`
  差分对自动布线
- `create_power_planes`
  建 plane / 加地回流 via
- `repair_disconnected_planes`
  修断开的 plane
- `check_connectivity`
  查连通性
- `check_drc`
  查 DRC
- `check_orphan_stubs`
  查悬空 stub

---

## 10. 结论

这个仓库不是“一个单一自动布线脚本”，而是把 PCB 自动处理拆成了三层：

- **分析层**
  看懂板子现在是什么状态。
- **计划层**
  决定接下来先做什么、后做什么。
- **执行层**
  真的去放置、fanout、布线、校验。

`.claude/skills` 则是在这三层外面又加了一层“工作流指导”：

- 哪些时候该先放置
- 哪些时候该走 session 流程
- 哪些时候该让 LLM 直接给坐标
- 哪些时候该重试、该调参、该回退

如果你后面还想继续，我可以在这份 README 的基础上再帮你补两样东西：

1. 给每个 tool 加一段“典型调用示例”。
2. 另外整理一份“从零开始路由一块板的实战教程”。
