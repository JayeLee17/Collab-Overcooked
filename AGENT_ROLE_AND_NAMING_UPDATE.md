# Agent 角色和命名更新总结

## ✅ 已完成的修改

### 1. Agent 角色配置修正

**配置文件**: `configs/default.yaml`

| Agent Index | 地图符号 | 角色 | 说明 |
|------------|---------|------|------|
| A0 | `1` | Chef | 右区 Chef |
| A1 | `2` | Assistant | 中区 Assistant |
| A2 | `3` | Dishwasher | 左区 Dishwasher |
| A3 | `4` | Assistant | 中区 Assistant |
| A4 | `5` | Chef | 右区 Chef |

**修改内容**:
- `agent_2.role`: `Assistant` → `Dishwasher`
- `agent_3.role`: `Dishwasher` → `Assistant`

### 2. Agent 代称从 P0-P4 改为 A0-A4

**修改的文件**:
- `collab_overcooked/agents/collab.py`
- `collab_overcooked/main.py`

**修改内容**:
- `name()` 方法: `"Player " + str(self.agent_index)` → `"A" + str(self.agent_index)`
- 所有输出中的 `P{agent_index}` → `A{agent_index}`
- 所有输出中的 `Player {agent_index}` → `A{agent_index}`

**示例**:
- `P0` → `A0`
- `P1` → `A1`
- `P2` → `A2`
- `P3` → `A3`
- `P4` → `A4`

### 3. 第一个时间步任务分配扫描

**文件**: `collab_overcooked/main.py`

**功能**:
- 在第一个时间步（`t=0` 之前）进行任务分配扫描
- 循环扫描直到所有 Assistant 和 Chef 都有任务
- 最多扫描 10 轮，避免无限循环

**实现逻辑**:
```python
# 第一个时间步：扫描并分配任务
for round_num in range(max_rounds):
    # 让所有 agent 尝试认领任务
    for agent_idx, agent in enumerate(team.agents):
        if hasattr(agent, '_try_claim_task'):
            agent._try_claim_task()
    
    # 检查是否所有 Assistant 和 Chef 都有任务
    all_assigned = True
    for agent_idx, agent in enumerate(team.agents):
        role = getattr(agent, 'role', '').lower()
        if role in ('assistant', 'chef'):
            task = task_pool.get_agent_current_task(agent_idx)
            if task is None:
                all_assigned = False
                break
    
    if all_assigned:
        break
```

### 4. 输出当前各个智能体的任务状态

**文件**: `collab_overcooked/main.py`

**输出格式**:
```
============================================================
[任务分配状态] 当前各个智能体的任务:
============================================================
  A0 (Chef): Task 0(boiled_egg) - 伙伴: A1
  A1 (Assistant): Task 0(boiled_egg) - 伙伴: A0
  A2 (Dishwasher): 无任务
  A3 (Assistant): Task 1(baked_carrot_soup) - 伙伴: A4
  A4 (Chef): Task 1(baked_carrot_soup) - 伙伴: A3
============================================================
```

**输出时机**:
- 在第一个时间步任务分配完成后立即输出
- 显示每个 Agent 的任务 ID、订单名称和协作伙伴

## 📋 关键特性

### Agent 不碰撞
- ✅ 已修复：`real_time_planner` 不再将其他 agent 位置标记为障碍物
- Agent 之间可以共享位置，不会物理碰撞

### 任务分配规则
1. **Dishwasher (A2)**: 不参与烹饪任务认领，只处理洗碗任务
2. **Chef 和 Assistant**: 按 `agent_index` 从小到大优先认领
3. **配对规则**: 同一任务的 Chef 和 Assistant 自动配对
4. **任务完成**: 任务完成后，Agent 自动重置状态，准备认领新任务

### 通讯机制
- **同任务配对**: Chef ↔ Assistant（同一任务）
- **跨任务**: 不通讯（各干各的）
- **工具冲突**: 触发协调通讯（index 小者优先）
- **Dishwasher**: 独立工作，不参与通讯

## 🎯 使用示例

运行实验后，第一个时间步会看到：

```
============================================================
[初始任务分配] 开始扫描并分配任务...
============================================================
[TaskClaim] A0(Chef) 认领了 Task 0(boiled_egg)
[TaskClaim] A1(Assistant) 认领了 Task 0(boiled_egg)
[TaskClaim] A3(Assistant) 认领了 Task 1(baked_carrot_soup)
[TaskClaim] A4(Chef) 认领了 Task 1(baked_carrot_soup)
[初始任务分配] 所有 Assistant 和 Chef 都已分配任务（第 1 轮）

============================================================
[任务分配状态] 当前各个智能体的任务:
============================================================
  A0 (Chef): Task 0(boiled_egg) - 伙伴: A1
  A1 (Assistant): Task 0(boiled_egg) - 伙伴: A0
  A2 (Dishwasher): 无任务
  A3 (Assistant): Task 1(baked_carrot_soup) - 伙伴: A4
  A4 (Chef): Task 1(baked_carrot_soup) - 伙伴: A3
============================================================

>>>>>>>>>>>>>time: 0<<<<<<<<<<<<<<<<<<<<<
...
```

## 📝 注意事项

1. **Agent 代称**: 所有输出现在使用 `A0-A4` 而不是 `P0-P4`
2. **任务分配**: 第一个时间步会自动分配任务，确保所有 Chef 和 Assistant 都有任务
3. **Dishwasher**: A2 不参与烹饪任务，只处理洗碗任务
4. **Agent 碰撞**: Agent 之间不会物理碰撞，可以共享位置
