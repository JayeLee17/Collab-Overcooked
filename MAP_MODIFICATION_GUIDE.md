# 地图修改指南

本文档说明如何修改 Overcooked 地图布局。

## 地图文件位置

地图文件位于：
```
dependencies/overcooked_ai/overcooked_ai_py/data/layouts/
```

当前项目中有：
- `cramped_room.layout` - 默认地图文件

## 地图文件格式

地图文件是 **JSON 格式**，主要包含以下字段：

### 1. `grid` 字段（必需）
地图网格，使用字符串表示，每行用换行符分隔。

### 2. 地图符号含义

| 符号 | 含义 | 说明 |
|------|------|------|
| `X` | 柜台 (Counter) | 可以放置物品的台面 |
| `P` | 锅 (Pot) | 用于烹饪，操作类型：cook |
| `O` | 烤箱 (Oven) | 用于烘烤，操作类型：bake |
| `C` | 切菜板 (Chopping Board) | 用于切菜，操作类型：cut |
| `B` | 搅拌机 (Blender) | 用于搅拌，操作类型：stir |
| `D` | 盘子供应处 (Dish Dispenser) | 获取盘子的地方 |
| `S` | 服务台 (Serving Location) | 交付完成菜品的地方 |
| `I` | 食材供应处 (Ingredients Dispenser) | 获取食材的地方 |
| `W` | **水槽 (Water/Sink)** | ⚠️ **当前代码中未完全支持，需要添加** |
| ` ` (空格) | 空地 (Empty Floor) | 玩家可以行走的空地 |
| `1-9` | 玩家起始位置 | 数字表示玩家编号（1=玩家0，2=玩家1，以此类推） |

### 3. 其他重要字段

- `utensils`: 定义各种厨具及其符号和操作类型
- `utensil_agent0` / `utensil_agent1`: 定义每个智能体可以使用的厨具
- `recipes`: 定义各种食谱和制作方法
- `ingredients`: 可用食材列表
- `need_dish`: 哪些菜品需要盘子

## 您想要的地图

```
XXXXXPXX
X3I4X1 X
W C2X  X
X D X 5O
XXXBXSXX
```

### 地图分析

- **尺寸**: 8列 × 5行
- **玩家位置**:
  - 玩家1 (数字1): 第2行，第6列
  - 玩家2 (数字2): 第3行，第3列
  - 玩家3 (数字3): 第2行，第2列
  - 玩家4 (数字4): 第2行，第3列
  - 玩家5 (数字5): 第4行，第7列
- **厨具**:
  - P (锅): 第1行，第6列
  - C (切菜板): 第3行，第2列
  - O (烤箱): 第4行，第8列
  - B (搅拌机): 第5行，第4列
- **功能区域**:
  - I (食材): 第2行，第3列
  - D (盘子): 第4行，第3列
  - S (服务台): 第5行，第6列
  - W (水槽): 第3行，第1列 ⚠️ **需要添加支持**

## 修改步骤

### 步骤1: 创建新的地图文件

在 `dependencies/overcooked_ai/overcooked_ai_py/data/layouts/` 目录下创建新文件，例如 `custom_map.layout`

### 步骤2: 编写地图 JSON

参考 `cramped_room.layout` 的格式，创建如下内容：

```json
{
    "grid": """XXXXXPXX
X3I4X1 X
W C2X  X
X D X 5O
XXXBXSXX""",
    "start_order_list": ["apple"],
    "order_probability": {
        "onion_soup": 1
    },
    "utensils": {
        "pot": {"symbol": "P", "operation": "cook"},
        "chopping_board": {"symbol": "C", "operation": "cut"},
        "oven": {"symbol": "O", "operation": "bake"},
        "blender": {"symbol": "B", "operation": "stir"},
        "water": {"symbol": "W", "operation": "wash"},  // 需要添加
        "none": {"symbol": "N", "operation": "none"}
    },
    "utensil_agent0": {
        "pot": "cook",
        "oven": "bake"
    },
    "utensil_agent1": {
        "chopping_board": "cut",
        "blender": "stir"
    },
    // ... 其他配置（参考 cramped_room.layout）
}
```

### 步骤3: 添加 W 符号支持（如果需要）

如果 `W` 符号在代码中不被支持，需要修改以下文件：

#### 3.1 修改 `overcooked_mdp.py`

**文件**: `dependencies/overcooked_ai/overcooked_ai_py/mdp/overcooked_mdp.py`

**位置1**: 第1478行 - `_assert_valid_grid` 方法

**修改前**:
```python
def is_not_free(c):
    return c in 'XOPDSTICB'  # Add ingredient grid
```

**修改后**:
```python
def is_not_free(c):
    return c in 'XOPDSTICBW'  # Add ingredient grid and water
```

**位置2**: 第1496行 - 验证字符有效性

**修改前**:
```python
assert all(c in 'XOPCDSTIB123456789 ' for c in all_elements), 'Invalid character in grid'
```

**修改后**:
```python
assert all(c in 'XOPCDSTIBW123456789 ' for c in all_elements), 'Invalid character in grid'
```

#### 3.2 添加 W 符号的处理逻辑

如果 `W` 需要特殊功能（例如清洗），可能需要在以下位置添加处理：

- `_get_terrain_type_pos_dict()` 方法：确保 W 被正确识别为地形类型
- `get_counter_locations()` 方法：如果 W 可以作为柜台使用
- 其他与地形交互相关的方法

### 步骤4: 更新配置文件

在 `configs/default.yaml` 或您的配置文件中，将 `layout` 字段改为新地图名称：

```yaml
environment:
  layout: "custom_map"  # 对应 custom_map.layout 文件（不需要 .layout 后缀）
```

### 步骤5: 验证地图

运行程序测试新地图：

```bash
python -m collab_overcooked.main --layout custom_map --order boiled_egg
```

## 注意事项

### 1. 地图边界规则
- **边界必须是不可通行区域**：地图的上下左右边界必须是 `X`, `P`, `O`, `S`, `D`, `T`, `I`, `C`, `B` 或 `W` 之一
- **不能是空格**：边界不能是空地 ` `，否则会报错

### 2. 必需元素
- **至少需要1个玩家**：地图中必须有数字 `1`（玩家0的起始位置）
- **至少需要1个 D**：必须有盘子供应处
- **至少需要1个 S**：必须有服务台
- **至少需要1个 P**：必须有锅
- **至少需要1个 O/T/I**：必须有食材供应处（O=洋葱，T=番茄，I=通用食材）

### 3. 玩家编号规则
- 玩家编号必须从 `1` 开始连续：如果有3个玩家，必须是 `1`, `2`, `3`
- 不能跳过数字：不能只有 `1` 和 `3` 而没有 `2`

### 4. 地图尺寸
- 地图可以是任意矩形尺寸
- 每行的长度必须相同（不能是锯齿状）

### 5. 多智能体支持
- 您的地图有5个玩家位置（1, 2, 3, 4, 5），这意味着可以支持最多5个智能体
- 确保在配置文件中配置了相应数量的智能体

## 完整示例

创建一个名为 `multi_agent_map.layout` 的文件：

```json
{
    "grid": """XXXXXPXX
X3I4X1 X
W C2X  X
X D X 5O
XXXBXSXX""",
    "start_order_list": ["apple"],
    "order_probability": {
        "onion_soup": 1
    },
    "utensils": {
        "pot": {"symbol": "P", "operation": "cook"},
        "chopping_board": {"symbol": "C", "operation": "cut"},
        "oven": {"symbol": "O", "operation": "bake"},
        "blender": {"symbol": "B", "operation": "stir"},
        "water": {"symbol": "W", "operation": "wash"},
        "none": {"symbol": "N", "operation": "none"}
    },
    "utensil_agent0": {
        "pot": "cook",
        "oven": "bake"
    },
    "utensil_agent1": {
        "chopping_board": "cut",
        "blender": "stir"
    },
    "utensil_agent2": {
        "pot": "cook",
        "chopping_board": "cut"
    },
    "utensil_agent3": {
        "oven": "bake",
        "blender": "stir"
    },
    "utensil_agent4": {
        "pot": "cook",
        "blender": "stir"
    },
    "cook_time": 3,
    "num_items_for_soup": 3,
    "delivery_reward": 20,
    "rew_shaping_params": null,
    "ingredients": ["apple", "carrot", "onion", "potato", "tofu", "bell_pepper", "sweet_potato", "egg", "mushroom", "pumpkin", "corn", "green_bean", "lentil", "eggplant", "chickpea", "zucchini", "beef", "peanut", "chicken", "spinach", "coconut", "broccoli", "bean", "cauliflower", "pea", "romaine_lettuce", "tomato", "taro", "green_pea"],
    "recipes": {
        // ... 复制 cramped_room.layout 中的 recipes 配置
    },
    "need_dish": {
        // ... 复制 cramped_room.layout 中的 need_dish 配置
    }
}
```

## 调试技巧

1. **检查地图格式**：确保 JSON 格式正确，可以使用在线 JSON 验证器
2. **检查边界**：确保所有边界都不是空格
3. **检查玩家编号**：确保玩家编号连续且从1开始
4. **检查必需元素**：确保有 D、S、P 和至少一个食材供应处
5. **运行测试**：先用简单的配置测试，确认地图加载正常

## 相关代码文件

- **地图加载**: `dependencies/overcooked_ai/overcooked_ai_py/data/layouts/__init__.py`
- **地图解析**: `dependencies/overcooked_ai/overcooked_ai_py/mdp/overcooked_mdp.py` 的 `from_layout_name()` 和 `from_grid()` 方法
- **地图验证**: `dependencies/overcooked_ai/overcooked_ai_py/mdp/overcooked_mdp.py` 的 `_assert_valid_grid()` 方法
- **地形处理**: `dependencies/overcooked_ai/overcooked_ai_py/mdp/overcooked_mdp.py` 的 `_get_terrain_type_pos_dict()` 方法