# Planner 缓存更新指南

## 🎯 问题

更新地图布局后，planner 缓存文件（`.pkl`）可能仍包含旧地图的 mdp 对象，导致运行时仍使用旧地图。

## 🔍 问题分析

### 1. Planner 缓存机制

**文件**: `dependencies/overcooked_ai/overcooked_ai_py/planning/planners.py`

**逻辑** (第1147-1149行):
```python
if mlp.ml_action_manager.params != mlp_params or mlp.mdp != mdp:
    print("Mlp with different params or mdp found, computing from scratch")
    return MediumLevelPlanner.compute_mlp(filename, mdp, mlp_params)
```

**比较方法**: `OvercookedGridworld.__eq__` (第572-580行)
```python
def __eq__(self, other):
    return np.array_equal(self.terrain_mtx, other.terrain_mtx) and \
            self.start_player_positions == other.start_player_positions and \
            # ... 其他字段 ...
```

### 2. 可能的问题

1. **缓存文件包含旧 mdp 对象**: 即使删除了 `.pkl` 文件，如果重新计算时地图文件还没更新，会保存旧地图
2. **mdp 对象比较可能不准确**: 虽然比较 `terrain_mtx`，但可能因为格式问题导致比较失败
3. **地图文件格式问题**: grid 字符串中的前导空格可能导致解析问题

## ✅ 解决方案

### 方法1：删除缓存文件（推荐）

```bash
cd /Users/lijiayi/Desktop/毕业设计/code/Collab-Overcooked
rm dependencies/overcooked_ai/overcooked_ai_py/data/planners/multi_agent_map_am.pkl
```

**优点**: 简单直接，确保使用新地图

### 方法2：设置 force_compute=True

**文件**: `collab_overcooked/main.py`

**位置**: 第91-93行

**修改**:
```python
mlam = MediumLevelPlanner.from_pickle_or_compute(
    mdp, mlam_params, force_compute=True  # 强制重新计算
)
```

**注意**: 计算完成后记得改回 `False` 以使用缓存

### 方法3：修复地图文件格式

确保地图文件中的 grid 字符串格式正确，每行没有不必要的前导空格。

## 📝 操作步骤

### 步骤1：确认地图文件已更新

检查 `multi_agent_map.layout` 文件，确认 grid 部分是正确的：

```json
"grid":  """XXXXXXPXX
            X3I 4X 1X
            W C 2X  X
            X D  X5 O
            XXXBXXSXX""",
```

### 步骤2：删除旧缓存

```bash
rm dependencies/overcooked_ai/overcooked_ai_py/data/planners/multi_agent_map_am.pkl
```

### 步骤3：运行实验

正常运行实验，系统会自动检测到文件不存在，并基于新地图重新计算：

```bash
python -m collab_overcooked.main --config_path configs/default.yaml
```

### 步骤4：验证

运行时应该看到：
- `Recomputing planner due to: [Errno 2] No such file or directory`
- 或者 `Mlp with different params or mdp found, computing from scratch`

## 🔧 地图文件格式检查

### 当前格式问题

地图文件中的 grid 字符串包含前导空格：
```
"grid":  """XXXXXXPXX
            X3I 4X 1X    <- 这行有前导空格
            W C 2X  X    <- 这行有前导空格
            ...
```

### 代码处理

`overcooked_mdp.py` 第622行会 `strip()` 每行：
```python
grid = [layout_row.strip() for layout_row in grid.split("\n")]
```

所以前导空格会被自动去除，**这不是问题**。

## ⚠️ 注意事项

1. **计算时间**: 5个agent重新计算可能需要几分钟
2. **验证**: 运行后检查日志中的地图显示，确认是8列（新地图）而不是7列（旧地图）
3. **缓存位置**: 确保有写入权限到 `data/planners/` 目录

## 🚀 快速命令

```bash
cd /Users/lijiayi/Desktop/毕业设计/code/Collab-Overcooked && \
rm -f dependencies/overcooked_ai/overcooked_ai_py/data/planners/multi_agent_map_am.pkl && \
echo "✅ 已删除 planner 缓存，下次运行将基于新地图重新计算"
```
