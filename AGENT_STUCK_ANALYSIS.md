# Agent 卡住问题分析

## 🔍 问题现象

从 `output_test_fix.log` 分析，发现两个关键问题：

### 1. A1 (Assistant) 卡在 `pickup(egg, ingredient_dispenser)`

**日志位置**: 行323-332, 974-978, 1566-1570

```
[DEBUG A1] Motion goal ((1, 1), (1, 0)) filtered:
  Start: ((3, 2), (0, -1))
  Goal: ((1, 1), (1, 0))
  is_valid_motion_goal: True
  positions_are_connected: False
```

**问题**: A1 在 (3,2)，尝试到达 (1,1) 访问 I(2,1)，但 `positions_are_connected` 返回 `False`。

### 2. A3 (Assistant) 卡在 `pickup(carrot, ingredient_dispenser)`

**日志位置**: 行490-494, 979-983

```
[DEBUG A3] Motion goal ((1, 1), (1, 0)) filtered:
  Start: ((3, 1), (-1, 0))
  Goal: ((1, 1), (1, 0))
  is_valid_motion_goal: True
  positions_are_connected: False
```

**问题**: A3 在 (3,1)，尝试到达 (1,1) 访问 I(2,1)，但 `positions_are_connected` 返回 `False`。

## 🎯 根本原因

### 核心问题：`positions_are_connected` 使用预计算的静态图

**代码位置**:
- `dependencies/overcooked_ai/overcooked_ai_py/planning/planners.py` 第144-145行
- `dependencies/overcooked_ai/overcooked_ai_py/planning/search.py` 第442-446行

```python
# planners.py
def positions_are_connected(self, start_pos_and_or, goal_pos_and_or):
    return self.graph_problem.are_in_same_cc(start_pos_and_or, goal_pos_and_or)

# search.py
def are_in_same_cc(self, node1, node2):
    node1_cc_index = [i for i, cc in enumerate(self.connected_components) if node1 in cc]
    node2_cc_index = [i for i, cc in enumerate(self.connected_components) if node2 in cc]
    assert len(node1_cc_index) == len(node2_cc_index) == 1
    return node1_cc_index[0] == node2_cc_index[0]
```

**问题分析**:
1. `graph_problem.connected_components` 是在 `MotionPlanner` 初始化时**预计算**的
2. 这个预计算的图只考虑**静态地形**（地图上的 'X', ' ', 'I', 'C' 等）
3. **不考虑动态的agent位置**
4. 即使我们修改了 `find_path` 不阻挡其他agent，`positions_are_connected` 仍然基于静态图判断

### 地图布局问题

```
XXXXXPX
X3I4X1X    <- I(2,1), A3起始在(1,1)='3', A4起始在(3,1)='4', A1起始在(5,1)='1'
W C2X X    <- C(2,2), A2起始在(3,2)='2', A1当前位置(3,2)
X D X5O    <- A5起始在(5,3)='5'
XXXBXSX
```

**关键发现**:
- (1,1) 位置在地图上是 '3'（A3的起始位置），不是可通行的 ' ' 位置
- 预计算的连通分量图认为 (1,1) 是不可通行的，所以 (3,2) 和 (1,1) 不在同一个连通分量中
- 即使 A3 已经移动到 (3,1)，预计算的图仍然认为 (1,1) 不可通行

## 📍 代码定位

### 1. Motion Goal 过滤逻辑

**文件**: `collab_overcooked/agents/collab.py`

**位置**: 第3340-3355行

```python
def find_motion_goals(self, state):
    # ... (获取 motion_goals) ...
    
    # Filter motion_goals by checking if they are reachable from current position
    valid_motion_goals = []
    for mg in motion_goals:
        is_valid = self.mlam.mp.is_valid_motion_start_goal_pair(
            player.pos_and_or, mg
        )
        if is_valid:
            valid_motion_goals.append(mg)
        # Debug: Print why motion goals are filtered for pickup from ingredient_dispenser
        elif "pickup" in self.parse_action and "dispenser" in self.parse_action_params[1] and self.actor == "assistant":
            print(f"[DEBUG A{self.agent_index}] Motion goal {mg} filtered:")
            print(f"  Start: {player.pos_and_or}")
            print(f"  Goal: {mg}")
            print(f"  is_valid_motion_goal: {self.mlam.mp.is_valid_motion_goal(mg)}")
            print(f"  positions_are_connected: {self.mlam.mp.positions_are_connected(player.pos_and_or, mg)}")
    
    return valid_motion_goals
```

**问题**: `is_valid_motion_start_goal_pair` 内部调用 `positions_are_connected`，使用预计算的静态图。

### 2. `is_valid_motion_start_goal_pair` 检查

**文件**: `dependencies/overcooked_ai/overcooked_ai_py/planning/planners.py`

**位置**: 第110-115行

```python
def is_valid_motion_start_goal_pair(self, start_pos_and_or, goal_pos_and_or, debug=False):
    if not self.is_valid_motion_goal(goal_pos_and_or):
        return False
    if not self.positions_are_connected(start_pos_and_or, goal_pos_and_or):  # <-- 这里
        return False
    return True
```

### 3. `positions_are_connected` 实现

**文件**: `dependencies/overcooked_ai/overcooked_ai_py/planning/planners.py`

**位置**: 第144-145行

```python
def positions_are_connected(self, start_pos_and_or, goal_pos_and_or):
    return self.graph_problem.are_in_same_cc(start_pos_and_or, goal_pos_and_or)
```

**问题**: `graph_problem` 是预计算的，只考虑静态地形。

### 4. 连通分量计算

**文件**: `dependencies/overcooked_ai/overcooked_ai_py/planning/search.py`

**位置**: 第434-440行

```python
def _get_connected_components(self):
    num_ccs, cc_labels = scipy.sparse.csgraph.connected_components(self.sparse_adjacency_matrix)
    connected_components = [set() for _ in range(num_ccs)]
    for node_index, cc_index in enumerate(cc_labels):
        node = self._decoder[node_index]
        connected_components[cc_index].add(node)
    return connected_components
```

**问题**: 这个计算在初始化时完成，基于静态地图，不考虑agent位置。

## 🔧 解决方案

### 方案1：动态检查连通性（推荐）

修改 `positions_are_connected` 或 `is_valid_motion_start_goal_pair`，使用 `real_time_planner` 或 `find_path` 来动态检查连通性，而不是依赖预计算的图。

**修改位置**: `collab_overcooked/agents/collab.py` 第3342行

```python
# 当前代码
is_valid = self.mlam.mp.is_valid_motion_start_goal_pair(
    player.pos_and_or, mg
)

# 修改为：使用 real_time_planner 动态检查
try:
    action_plan, plan_cost = self.real_time_planner(
        player.pos_and_or, mg, state
    )
    is_valid = (action_plan is not None and plan_cost < np.inf)
except:
    is_valid = False
```

### 方案2：修改地图布局

将 (1,1) 位置改为可通行的 ' '，但这会改变地图设计。

### 方案3：使用其他可访问位置

检查是否有其他位置可以访问 I(2,1)，而不是 (1,1)。

## 📝 建议

**立即行动**: 采用方案1，修改 `find_motion_goals` 中的连通性检查，使用动态路径规划而不是预计算的图。

**代码修改位置**:
1. `collab_overcooked/agents/collab.py` 第3340-3355行：`find_motion_goals` 方法
2. 将 `is_valid_motion_start_goal_pair` 替换为动态路径规划检查

这样可以正确处理agent位置变化和地图动态性。
