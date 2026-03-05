# 地图坐标系统说明

## 📐 坐标系统

**坐标格式**: `(列, 行)` 或 `(x, y)`
- **列 (x)**: 从左到右，从 **0** 开始
- **行 (y)**: 从上到下，从 **0** 开始
- **示例**: `(0,0)` 是左上角第一个字符

## 🗺️ 地图可视化

```
列:  0  1  2  3  4  5  6  7
行0: X  X  X  X  X  P  X  X
行1: X  3  I  4  X  1     X
行2: W     C  2  X        X
行3: X     D     X  5  O
行4: X  X  X  B  X  S  X  X
```

## 📍 关键位置

### Agent 起始位置

| Agent | 地图符号 | 坐标 (x,y) | 说明 |
|-------|---------|-----------|------|
| P0 | `1` | (5,1) | Chef (右区) |
| P1 | `2` | (3,2) | Assistant (中区) |
| P2 | `3` | (1,1) | Assistant (左区) |
| P3 | `4` | (3,1) | Dishwasher (中区) |
| P4 | `5` | (6,3) | Chef (右区) |

### 设施位置

| 设施 | 地图符号 | 坐标 (x,y) | 说明 |
|------|---------|-----------|------|
| Pot | `P` | (5,0) | 锅 (右区上方) |
| Ingredient Dispenser | `I` | (2,1) | 食材分发器 (中区) |
| Water Sink | `W` | (0,2) | 水槽 (左区) |
| Chopping Board | `C` | (2,2) | 切菜板 (中区) |
| Dish Dispenser | `D` | (2,3) | 盘子分发器 (中区) |
| Oven | `O` | (7,3) | 烤箱 (右区) |
| Blender | `B` | (3,4) | 搅拌机 (中区) |
| Serving Location | `S` | (5,4) | 服务台 (右区) |

### 可通行位置（空格）

- `(1,2)` - 水槽旁边
- `(3,3)` - 盘子分发器旁边
- `(5,2)` - 中间通道
- `(6,2)` - 中间通道
- `(6,1)` - 右区通道
- `(5,3)` - 烤箱旁边

## 🔄 交互机制

### Agent 如何与设施交互？

Agent **不能直接站在设施上**，必须：
1. 站在设施**旁边**的可通行位置（空格）
2. **面向**设施的方向
3. 执行 `INTERACT` 动作

### 示例：P1 如何与 I(2,1) 交互？

```
地图:
Row 1: X  3  I  4  X  1     X
Row 2: W     C  2  X        X
```

**I(2,1) 的位置**: `(2,1)`

**P1 可以站在的位置**:
- `(3,1)` - 面向 **WEST** (向左) ← **这是 P1 当前尝试的**
- `(2,0)` - 面向 **SOUTH** (向下) - 但这是墙，不可行
- `(1,1)` - 面向 **EAST** (向右) - 但 P2 在那里

**P1 当前位置**: `(3,2)`
**目标位置**: `(3,1)` - 需要向上移动 1 步
**目标方向**: `WEST` - 面向 I(2,1)

### 示例：P0 如何与 P(5,0) 交互？

```
地图:
Row 0: X  X  X  X  X  P  X  X
Row 1: X  3  I  4  X  1     X
```

**P(5,0) 的位置**: `(5,0)`

**P0 可以站在的位置**:
- `(5,1)` - 面向 **NORTH** (向上) ← P0 在这里
- `(4,0)` - 面向 **EAST** (向右) - 但这是墙
- `(6,0)` - 面向 **WEST** (向左) - 但这是墙

**P0 当前位置**: `(5,1)`
**目标位置**: `(5,1)` - 已经在正确位置
**目标方向**: `NORTH` - 面向 P(5,0)

## 🧭 方向系统

```
      NORTH (上)
         ↑
         |
WEST ← --+-- → EAST
(左)     |      (右)
         ↓
      SOUTH (下)
```

在代码中：
- `NORTH` = `Direction.NORTH` = 向上移动
- `SOUTH` = `Direction.SOUTH` = 向下移动
- `EAST` = `Direction.EAST` = 向右移动
- `WEST` = `Direction.WEST` = 向左移动

## 📊 位置访问示例

### 检查位置类型

```python
# 在代码中访问位置
mdp = OvercookedGridworld.from_layout_name("multi_agent_map")
terrain_type = mdp.get_terrain_type_at_pos((2, 1))  # 返回 'I'
terrain_type = mdp.get_terrain_type_at_pos((3, 1))  # 返回 ' ' (空格，可通行)
terrain_type = mdp.get_terrain_type_at_pos((0, 0))  # 返回 'X' (墙)
```

### 获取设施位置列表

```python
# 获取所有 ingredient_dispenser 位置
ingredient_locs = mdp.get_ingredient_dispenser_locations()  # [(2, 1)]

# 获取所有 dish_dispenser 位置
dish_locs = mdp.get_dish_dispenser_locations()  # [(2, 3)]

# 获取所有 pot 位置
pot_locs = mdp.get_pot_locations()  # [(5, 0)]
```

## 🎯 实际应用：P1 的路径规划问题

### 问题描述

P1 在 `(3,2)`，需要移动到 `(3,1)` 才能面向 I(2,1) 交互。

### 路径分析

```
当前位置: (3,2)
目标位置: (3,1)
移动方向: 向上 (NORTH)
目标方向: WEST (面向 I(2,1))
```

### 为什么可能失败？

1. **连通性检查**: `positions_are_connected((3,2, ?), (3,1, WEST))` 可能返回 `False`
2. **motion_goals_for_pos**: `motion_goals_for_pos[(2,1)]` 可能不包含 `(3,1, WEST)`
3. **障碍物**: 虽然我们移除了障碍物标记，但路径规划器可能还有其他检查

### 调试方法

查看调试输出：
```bash
grep -A5 "\[DEBUG P1\]" output_debug.log
```

这会显示：
- P1 的 motion_goals 列表
- 哪些 motion_goals 被过滤了
- 为什么被过滤（`is_valid_motion_goal` 或 `positions_are_connected` 返回 False）

## 📝 总结

- **坐标系统**: `(列, 行)` = `(x, y)`，从 0 开始
- **交互规则**: Agent 必须站在设施旁边的可通行位置，面向设施
- **方向**: NORTH(上), SOUTH(下), EAST(右), WEST(左)
- **调试**: 使用调试输出查看路径规划问题
