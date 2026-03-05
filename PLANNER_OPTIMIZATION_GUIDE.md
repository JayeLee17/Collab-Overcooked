# Planner 计算优化指南

## 为什么5个智能体加载这么慢？

### 计算复杂度分析

Planner 需要预计算所有可能的联合状态组合：

**2个玩家时**：
- 联合状态数 = `valid_positions^2`
- 如果地图有 N 个有效位置，需要计算 `N^2` 个状态组合
- 例如：20个位置 → 400个组合

**5个玩家时**：
- 联合状态数 = `valid_positions^5`
- 如果地图有 N 个有效位置，需要计算 `N^5` 个状态组合
- 例如：20个位置 → 3,200,000 个组合！

### 实际计算量

对于你的 `multi_agent_map`：
- 地图大小：8×5 = 40 个格子
- 有效位置（非墙）：约 15-20 个
- **2个玩家**：20² = 400 个组合 → 几秒钟
- **5个玩家**：20⁵ = 3,200,000 个组合 → 几分钟到十几分钟

## 优化方案

### 方案1: 使用缓存（已实现）✅

**优点**：
- 第一次计算后，后续运行直接加载，秒级完成
- 无需修改代码

**使用方法**：
- 第一次运行：等待计算完成（5-15分钟）
- 后续运行：自动从 `multi_agent_map_am.pkl` 加载

**缓存文件位置**：
```
dependencies/overcooked_ai/overcooked_ai_py/data/planners/multi_agent_map_am.pkl
```

### 方案2: 减少计算参数

在 `main.py` 的 `make_agent_from_config` 函数中，可以减少 `counter_goals`：

```python
# 当前配置（计算所有counter位置）
mlam_params = {
    "start_orientations": False,
    "wait_allowed": True,
    "counter_goals": counter_locations,  # 所有counter位置
    "counter_drop": counter_locations,
    "counter_pickup": counter_locations,
    "same_motion_goals": True,
}

# 优化配置（只使用部分counter）
mlam_params = {
    "start_orientations": False,
    "wait_allowed": True,
    "counter_goals": counter_locations[:5],  # 只使用前5个counter
    "counter_drop": counter_locations[:3],   # 只使用前3个drop位置
    "counter_pickup": counter_locations[:3], # 只使用前3个pickup位置
    "same_motion_goals": True,
}
```

**注意**：这可能会影响智能体的路径规划质量

### 方案3: 延迟计算（Lazy Computation）

不预计算所有计划，而是在需要时动态计算。但这需要大幅修改代码。

### 方案4: 使用简化的 Planner（不推荐）

对于多智能体场景，可以考虑不使用 MediumLevelPlanner，而是使用更简单的规划方法。但这会失去很多功能。

## 推荐方案

**最佳实践**：

1. **第一次运行**：让程序完成计算（5-15分钟）
   - 程序会显示进度
   - 计算完成后自动保存缓存

2. **后续运行**：直接使用缓存
   - 加载速度：秒级
   - 无需等待

3. **如果必须重新计算**：
   - 可以在后台运行
   - 或者使用更强大的机器

## 检查缓存文件

```bash
# 检查缓存文件是否存在
ls -lh dependencies/overcooked_ai/overcooked_ai_py/data/planners/multi_agent_map_am.pkl

# 如果文件存在且较大（几MB到几十MB），说明缓存已生成
```

## 性能对比

| 玩家数 | 状态组合数（假设20个位置） | 首次计算时间 | 缓存加载时间 |
|--------|-------------------------|------------|------------|
| 2      | 400                     | ~5秒       | <1秒       |
| 3      | 8,000                   | ~30秒      | <1秒       |
| 4      | 160,000                 | ~5分钟     | <1秒       |
| 5      | 3,200,000               | ~10-15分钟 | <1秒       |

## 总结

**为什么5个智能体慢**：
- 计算复杂度是指数级的（N^5）
- 需要预计算大量状态组合
- 这是正常的，不是bug

**解决方案**：
- ✅ 使用缓存（已实现）
- ✅ 第一次运行后，后续都很快
- ⚠️ 如果必须优化，可以减少counter数量（可能影响质量）

**建议**：
- 让第一次计算完成
- 之后就可以快速运行了
- 缓存文件可以备份，避免重复计算
