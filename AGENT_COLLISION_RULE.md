# Agent 碰撞规则说明

## 🎯 核心规则

**每一个时刻不管同区域的agent处于什么位置，该位置其他agent都可以正常通行。**

这意味着：
1. **Agent之间不会物理碰撞**
2. **多个agent可以同时站在同一个位置**
3. **路径规划时不应该将其他agent的位置视为障碍物**

## ✅ 实现位置

### 1. `real_time_planner` 方法

**文件**: `collab_overcooked/agents/collab.py`

**位置**: 第3448-3470行

```python
def real_time_planner(self, start_pos_and_or, goal, state):
    terrain_matrix = {
        "matrix": copy.deepcopy(self.mlam.mdp.terrain_mtx),
        "height": len(self.mlam.mdp.terrain_mtx),
        "width": len(self.mlam.mdp.terrain_mtx[0]),
    }
    # Support multiple agents: use first other agent for pathfinding
    # Note: Agents don't collide, so we don't block other agents' positions
    # This allows A1 and A3 to simultaneously stand at (3,1) or (3,2) to access I(2,1) and C(2,2)
    num_players = len(state.players_pos_and_or)
    if num_players <= 2:
        other_pos_and_or = state.players_pos_and_or[1 - self.agent_index]
    else:
        # For multi-agent, just use the first other agent for find_path
        # (find_path will handle that one agent, but we don't block others since agents don't collide)
        other_indices = [i for i in range(num_players) if i != self.agent_index]
        other_pos_and_or = state.players_pos_and_or[other_indices[0]]
    # Pass block_other_agent=False to allow agents to share positions (no collision)
    action_plan, plan_cost = find_path(
        start_pos_and_or, other_pos_and_or, goal, terrain_matrix, block_other_agent=False
    )
    
    return action_plan, plan_cost
```

**关键**: `block_other_agent=False` 确保其他agent的位置不会被标记为障碍物。

### 2. `find_path` 函数

**文件**: `dependencies/overcooked_ai/overcooked_ai_py/planning/search.py`

**位置**: 第108-137行

```python
def find_path(start_pos_and_or, other_pos_and_or, goal, terrain_mtx, block_other_agent=True):  
    """
    Find path from start to goal using BFS.
    
    Args:
        block_other_agent: If True, block other agent's position. If False, allow agents to share positions.
    """
    # ... (setup code) ...
    
    # Only block other agent's position if block_other_agent is True
    # This allows multiple agents to share the same position (no collision)
    if block_other_agent:
        mtx[other_pos_and_or[0][1]][other_pos_and_or[0][0]] = 'B'
    
    # ... (BFS pathfinding) ...
```

**关键**: 只有当 `block_other_agent=True` 时，才会将其他agent的位置标记为障碍物。

### 3. `find_motion_goals` 方法

**文件**: `collab_overcooked/agents/collab.py`

**位置**: 第3339-3379行

```python
def find_motion_goals(self, state):
    # ... (获取 motion_goals) ...
    
    # Use dynamic pathfinding to check if the goal is reachable
    # This is more accurate than pre-computed connectivity graph
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

**关键**: 使用 `real_time_planner` 进行动态路径规划，该函数已经设置了 `block_other_agent=False`。

### 4. `get_lowest_cost_action_and_goal_new` 方法

**文件**: `collab_overcooked/agents/collab.py`

**位置**: 第3419-3446行

```python
def get_lowest_cost_action_and_goal_new(
    self, start_pos_and_or, motion_goals, state
):
    min_cost = np.inf
    best_action, best_goal = None, None
    for goal in motion_goals:
        action_plan, plan_cost = self.real_time_planner(
            start_pos_and_or, goal, state
        )
        if plan_cost < min_cost:
            best_action = action_plan
            min_cost = plan_cost
            best_goal = goal
    # ... (fallback logic) ...
```

**关键**: 使用 `real_time_planner`，因此也遵循 `block_other_agent=False` 规则。

## 📊 实际效果

### 场景1: A1 和 A3 同时访问 I(2,1)

- **A1 在 (3,2)** → 移动到 **(3,1)** → 访问 I(2,1) ✅
- **A3 在 (3,1)** → 直接访问 I(2,1) ✅
- **结果**: A1 和 A3 可以同时站在 (3,1)，不会互相阻挡

### 场景2: A3 移动到 (3,2) 访问 C(2,2)

- **A3 在 (3,1)** → 移动到 **(3,2)** → 访问 C(2,2) ✅
- **A1 可能在 (3,2)** → 不影响 A3 的移动 ✅
- **结果**: A3 可以移动到 A1 所在的位置，不会发生碰撞

### 场景3: 多个Agent共享位置

- **任意数量的agent** 可以同时站在同一个位置
- **路径规划** 不会因为其他agent的存在而失败
- **交互** 仍然正常进行（每个agent独立交互）

## 🔍 验证点

1. ✅ `real_time_planner` 传递 `block_other_agent=False`
2. ✅ `find_path` 支持 `block_other_agent` 参数
3. ✅ `find_motion_goals` 使用 `real_time_planner` 进行动态检查
4. ✅ `get_lowest_cost_action_and_goal_new` 使用 `real_time_planner`

## 📝 注意事项

1. **向后兼容**: `find_path` 的 `block_other_agent` 参数默认为 `True`，保持向后兼容
2. **性能**: 不阻挡其他agent可能使路径规划稍快（更少的障碍物）
3. **逻辑一致性**: 所有路径规划都通过 `real_time_planner`，确保一致性

## 🎯 设计意图

这个规则的设计意图是：
- **简化协作**: Agent可以更容易地到达目标位置
- **提高效率**: 减少因路径阻塞导致的等待
- **符合实际**: 在厨房环境中，多个人员可以共享空间
