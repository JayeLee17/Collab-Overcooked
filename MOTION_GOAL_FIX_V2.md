# Motion Goal 修复 V2 - 考虑 Agent 可移动范围

## 🎯 问题描述

根据用户反馈，agent 有可移动范围限制：
- **A1 和 A3**: (3,1) 到 (4,3) - 即 x 在 [3,4]，y 在 [1,3]
- **A2**: (1,1) 到 (1,3) - 即 x=1，y 在 [1,3]
- **A0 和 A4**: (6,1) 到 (7,3) - 即 x 在 [6,7]，y 在 [1,3]

**I(2,1) 的位置**: (2,1)

**问题**：
1. `_get_ml_actions_for_positions` 会为 I(2,1) 生成所有可能的 motion goals，包括：
   - `((1,1), (1,0))` - 站在 (1,1) 面向右（但这是 A2 的区域，A1/A3 不能到达）
   - `((3,1), (-1,0))` - 站在 (3,1) 面向左（这是 A1/A3 的区域，✅ 正确）

2. 当前代码会尝试让 A1 到达 (1,1)，但 (1,1) 不在 A1 的可移动范围内，导致路径规划失败。

3. **关键要求**：I(2,1) 周围的位置如果有其他 agent，不应该成为其他 agent 前往的阻碍（因为 `block_other_agent=False`）。

## ✅ 修复方案

### 修改 `pickup_obj_actions` 和 `go_to_utensil_actions`

**文件**: `dependencies/overcooked_ai/overcooked_ai_py/planning/planners.py`

**修改内容**：
1. 在生成所有可能的 motion goals 后，**过滤**掉那些目标位置不在 `visitable_cur` 中的 motion goals
2. `visitable_cur` 是通过 `get_visitable_positions` 计算的，它只返回从 agent 当前位置可达的 `' '` 位置
3. 这确保了只有 agent 可以实际到达的位置才会被用作 motion goals

**关键代码**：
```python
# Get all possible motion goals for the object locations
all_motion_goals = self._get_ml_actions_for_positions(separate_states if len(separate_states) > 0 else obj_locations)

# Filter motion goals: only keep those where the goal position is in visitable_cur
# This ensures that motion goals are only generated for positions the agent can actually reach
# Note: Even if other agents are at those positions, they don't block pathfinding (block_other_agent=False)
filtered_motion_goals = []
for mg in all_motion_goals:
    mg_pos = mg[0]  # motion goal position
    if mg_pos in visitable_cur:
        filtered_motion_goals.append(mg)

return filtered_motion_goals
```

## 📊 预期效果

### A1 (Assistant) 在 (4,2)

**之前**:
- `_get_ml_actions_for_positions` 生成 `((1,1), (1,0))` 和 `((3,1), (-1,0))`
- 代码尝试让 A1 到达 (1,1)，但 (1,1) 不在 A1 的可移动范围内
- `visitable_cur` 不包含 (1,1)，但代码仍然尝试使用这个 motion goal
- 路径规划失败：`plan_cost=inf, plan=None`

**之后**:
- `_get_ml_actions_for_positions` 仍然生成 `((1,1), (1,0))` 和 `((3,1), (-1,0))`
- 但 `pickup_obj_actions` 会过滤掉 `((1,1), (1,0))`，因为 (1,1) 不在 `visitable_cur` 中
- 只返回 `((3,1), (-1,0))`，这是 A1 可以到达的位置
- 路径规划成功：A1 从 (4,2) 移动到 (3,1)，面向左，访问 I(2,1)

### A3 (Assistant) 在 (3,1)

**之前**:
- 类似问题，尝试到达 (1,1) 但失败

**之后**:
- 只返回 `((3,1), (-1,0))`（A3 已经在 (3,1)，只需要转向）
- 或者如果 A3 在 (4,1)，可以移动到 (3,1)

## 🔍 关键理解

1. **`get_visitable_positions` 的作用**：
   - 从 agent 当前位置开始 BFS，只返回可通行的 `' '` 位置
   - 这自然考虑了 agent 的可移动范围（因为 BFS 只能到达连通的位置）
   - 如果 (1,1) 不在 A1 的可移动范围内，`visitable_cur` 就不会包含 (1,1)

2. **`block_other_agent=False` 的作用**：
   - 即使其他 agent 在 (3,1)，`find_path` 也不会将其标记为障碍物
   - 所以 A1 和 A3 可以同时站在 (3,1) 访问 I(2,1)
   - 这满足了用户的要求："I(2,1) 周围的位置如果有其他 agent，不应该成为其他 agent 前往的阻碍"

3. **过滤逻辑的必要性**：
   - `_get_ml_actions_for_positions` 不知道 agent 的可移动范围
   - 它只是为每个地形特征生成所有可能的 motion goals
   - 需要 `pickup_obj_actions` 和 `go_to_utensil_actions` 来过滤，确保只返回 agent 可以到达的 motion goals

## 📝 修改的文件

1. `dependencies/overcooked_ai/overcooked_ai_py/planning/planners.py`
   - `pickup_obj_actions` 方法
   - `go_to_utensil_actions` 方法

## ✅ 验证

修复后，应该看到：
1. A1 和 A3 可以成功到达 (3,1) 访问 I(2,1)
2. 不会尝试到达 (1,1)（因为不在可移动范围内）
3. 即使 A2 在 (1,1)，也不会阻碍 A1 和 A3 到达 (3,1)
