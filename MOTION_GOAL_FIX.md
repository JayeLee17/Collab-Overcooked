# Motion Goal 过滤修复

## 🎯 问题

A1 和 A3 尝试访问 `ingredient_dispenser` 时，motion goal `((1, 1), (1, 0))` 被过滤掉，因为 `positions_are_connected: False`。

## 🔍 根本原因

1. **预计算的连通分量图**：`positions_are_connected` 使用预计算的静态图，只考虑地图初始化时的地形
2. **Agent 起始位置**：虽然地图解析时会将数字（agent起始位置）替换为 ' '，但预计算的连通分量图可能没有正确包含这些位置，或者因为其他原因认为它们不连通
3. **动态性缺失**：预计算的图不考虑当前 agent 的实际位置和动态变化

## ✅ 解决方案

修改 `find_motion_goals` 方法，使用**动态路径规划**替代预计算的连通性检查。

### 修改位置

**文件**: `collab_overcooked/agents/collab.py`

**方法**: `find_motion_goals()` (第3339-3355行)

### 修改内容

**之前**:
```python
is_valid = self.mlam.mp.is_valid_motion_start_goal_pair(
    player.pos_and_or, mg
)
```

**之后**:
```python
# First check if the goal itself is valid (facing terrain feature, etc.)
if not self.mlam.mp.is_valid_motion_goal(mg):
    continue

# Use dynamic pathfinding to check if the goal is reachable
try:
    action_plan, plan_cost = self.real_time_planner(
        player.pos_and_or, mg, state
    )
    is_valid = (action_plan is not None and plan_cost < np.inf)
except Exception as e:
    # Fallback to static check if dynamic planning fails
    is_valid = self.mlam.mp.is_valid_motion_start_goal_pair(
        player.pos_and_or, mg
    )
```

## 📊 预期效果

### A1 (Assistant) 在 (3,2)

**之前**: 尝试到达 (1,1) 访问 I(2,1)，但 `positions_are_connected` 返回 False

**之后**: 
- 使用 `real_time_planner` 动态检查 (3,2) 到 (3,1) 的路径
- (3,1) 可以访问 I(2,1)
- Motion goal `((3, 1), (1, 0))` 会被接受

### A3 (Assistant) 在 (3,1)

**之前**: 尝试到达 (1,1) 访问 I(2,1)，但 `positions_are_connected` 返回 False

**之后**:
- 使用 `real_time_planner` 动态检查 (3,1) 到 I(2,1) 的路径
- (3,1) 可以直接访问 I(2,1)
- Motion goal 会被接受

### A3 移动到 (3,2) 访问 C(2,2)

**之后**:
- 使用 `real_time_planner` 动态检查 (3,1) 到 (3,2) 的路径
- (3,2) 可以访问 C(2,2)
- Motion goal 会被接受

## 🔄 优势

1. **动态性**：考虑当前 agent 位置和状态
2. **准确性**：使用实际路径规划，而不是预计算的图
3. **灵活性**：可以处理 agent 起始位置等特殊情况
4. **向后兼容**：如果动态规划失败，回退到静态检查

## 📝 注意事项

- `real_time_planner` 已经支持 `block_other_agent=False`，允许 agent 共享位置
- 动态路径规划可能比静态检查稍慢，但更准确
- Debug 输出会显示动态路径规划的结果，便于调试

## 🎯 Agent 碰撞规则

**重要规则**: 每一个时刻不管同区域的agent处于什么位置，该位置其他agent都可以正常通行。

这意味着：
- **Agent之间不会物理碰撞**
- **多个agent可以同时站在同一个位置**
- **路径规划时不会将其他agent的位置视为障碍物**

所有路径规划都通过 `real_time_planner` 进行，该函数已经设置了 `block_other_agent=False`，确保遵循这个规则。

详细说明请参考 `AGENT_COLLISION_RULE.md`。
