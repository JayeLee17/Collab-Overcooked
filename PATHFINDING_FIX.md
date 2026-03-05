# 路径规划修复：允许 Agent 共享位置

## 🎯 问题描述

A1 和 A3 互相阻挡在 I(2,1) 和 C(2,2) 前面，导致路径规划不成功。用户希望实现 A1 和 A3 可以同时在 (3,1) 或 (3,2) 的逻辑。

## 🔍 根本原因

在 `find_path` 函数中，第125行将其他 agent 的位置标记为障碍物：
```python
mtx[other_pos_and_or[0][1]][other_pos_and_or[0][0]] = 'B'
```

这导致：
- A1 尝试到达 (3,1) 访问 I(2,1) 时，如果 A3 在 (3,1)，路径规划会失败
- A3 尝试到达 (3,2) 访问 C(2,2) 时，如果 A1 在 (3,2)，路径规划会失败

## ✅ 修复方案

### 1. 修改 `find_path` 函数

**文件**: `dependencies/overcooked_ai/overcooked_ai_py/planning/search.py`

**修改**:
- 添加 `block_other_agent` 参数（默认 `True`，保持向后兼容）
- 当 `block_other_agent=False` 时，不将其他 agent 的位置标记为障碍物

**代码**:
```python
def find_path(start_pos_and_or, other_pos_and_or, goal, terrain_mtx, block_other_agent=True):  
    """
    Find path from start to goal using BFS.
    
    Args:
        start_pos_and_or: Starting position and orientation
        other_pos_and_or: Other agent's position and orientation (for blocking)
        goal: Goal position and orientation
        terrain_mtx: Terrain matrix
        block_other_agent: If True, block other agent's position. If False, allow agents to share positions.
    """
    # ... (existing code) ...
    
    # Only block other agent's position if block_other_agent is True
    # This allows multiple agents to share the same position (no collision)
    if block_other_agent:
        mtx[other_pos_and_or[0][1]][other_pos_and_or[0][0]] = 'B'
```

### 2. 修改 `real_time_planner` 方法

**文件**: `collab_overcooked/agents/collab.py`

**修改**:
- 在调用 `find_path` 时传递 `block_other_agent=False`
- 允许 agent 共享位置，不互相阻挡

**代码**:
```python
def real_time_planner(self, start_pos_and_or, goal, state):
    # ... (existing code) ...
    
    # Pass block_other_agent=False to allow agents to share positions (no collision)
    # This allows A1 and A3 to simultaneously stand at (3,1) or (3,2) to access I(2,1) and C(2,2)
    action_plan, plan_cost = find_path(
        start_pos_and_or, other_pos_and_or, goal, terrain_matrix, block_other_agent=False
    )
    
    return action_plan, plan_cost
```

## 📊 地图布局分析

```
XXXXXPX
X3I4X1X    <- I(2,1), A3 在 (1,1), A4 在 (3,1), A1 在 (5,1)
W C2X X    <- C(2,2), A2 在 (3,2)
X D X5O    <- A5 在 (5,3)
XXXBXSX
```

**关键位置**:
- I(2,1): ingredient_dispenser
- C(2,2): chopping_board
- (3,1): 可以访问 I(2,1) 的位置
- (3,2): 可以访问 C(2,2) 的位置

**修复后的行为**:
- A1 可以到达 (3,1) 访问 I(2,1)，即使 A3 也在 (3,1)
- A3 可以到达 (3,2) 访问 C(2,2)，即使 A1 也在 (3,2)
- Agent 之间不会互相阻挡路径规划

## 🔄 向后兼容性

- `find_path` 的 `block_other_agent` 参数默认为 `True`
- 其他调用 `find_path` 的代码（如 `agent.py`）不需要修改
- 只有 `collab.py` 中的 `real_time_planner` 传递 `False`

## ✅ 预期效果

1. **A1 不再卡在 pickup(egg)**:
   - A1 可以到达 (3,1) 访问 I(2,1)
   - 即使 A3 也在 (3,1)，路径规划仍然成功

2. **A3 不再卡在 put_obj_in_utensil**:
   - A3 可以到达 (3,2) 访问 C(2,2)
   - 即使 A1 也在 (3,2)，路径规划仍然成功

3. **任务可以正常进行**:
   - Task 0 (boiled_egg): A1 可以成功取 egg
   - Task 1 (baked_carrot_soup): A3 可以成功处理 carrot

## 📝 注意事项

- Agent 仍然不会物理碰撞（这是游戏规则）
- 路径规划不再将其他 agent 的位置视为障碍物
- 多个 agent 可以共享同一个位置进行交互
