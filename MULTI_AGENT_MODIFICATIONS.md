# 多智能体支持修改总结

本文档总结了为支持多个智能体（而不只是两个）所做的所有代码修改。

## 修改的文件

### 1. `dependencies/overcooked_ai/overcooked_ai_py/agents/agent.py`

**修改内容：** `AgentGroup.reset()` 方法

**修改前：**
```python
def reset(self):
    for a in self.agents:
        index = a.agent_index
        a.reset(self.agents[1-index])  # 硬编码只支持两个智能体
```

**修改后：**
```python
def reset(self):
    for a in self.agents:
        index = a.agent_index
        # Get all other agents as teammates (supporting multiple agents)
        teammates = [other_agent for i, other_agent in enumerate(self.agents) if i != index]
        if len(teammates) == 1:
            # Backward compatibility: single teammate
            a.reset(teammates[0])
        else:
            # Multiple teammates: pass list of teammates
            a.reset(teammates)
```

**说明：** 现在可以获取所有其他智能体作为队友，支持任意数量的智能体。

---

### 2. `collab_overcooked/agents/collab.py`

#### 2.1 `LLMAgents.reset()` 方法

**修改内容：** 支持接受单个队友或多个队友列表

**修改前：**
```python
def reset(self, teammate: LLMPair):
    # ...
    self.teammate = teammate
```

**修改后：**
```python
def reset(self, teammate_or_teammates):
    # ...
    # Support both single teammate (backward compatibility) and multiple teammates
    if isinstance(teammate_or_teammates, list):
        self.teammates = teammate_or_teammates
        self.teammate = teammate_or_teammates[0] if len(teammate_or_teammates) > 0 else None
    else:
        self.teammate = teammate_or_teammates
        self.teammates = [teammate_or_teammates]
```

**说明：** 保持向后兼容性，同时支持多个队友。

#### 2.2 `LLMAgents.action()` 方法中的队友处理

**修改内容：** 更新所有队友的状态，而不仅仅是单个队友

**主要修改点：**
- 更新所有队友的 `order`
- 清空所有队友的对话历史
- 检查所有队友的动作完成状态
- 记录所有队友的 ml_actions（而不仅仅是另一个智能体）

**关键代码：**
```python
# Update order for all teammates
if hasattr(self, 'teammates'):
    for teammate in self.teammates:
        teammate.order = state.current_k_order[0]

# Record teammate ml_actions for all teammates
num_players = len(state.players)
for other_idx in range(num_players):
    if other_idx != self.agent_index and state.ml_actions[other_idx] is not None:
        self.teammate_ml_actions.append({
            "timestamp": self.current_timestep,
            "action": state.ml_actions[other_idx],
            "agent_index": other_idx,
        })
```

---

### 3. `collab_overcooked/main.py`

#### 3.1 动作打印（第376行）

**修改前：**
```python
print(f"action: P0 {Action.to_char(a_t[0])} | P1 {Action.to_char(a_t[1])}")
```

**修改后：**
```python
# Support multiple agents - dynamically print all agent actions
action_str = " | ".join([f"P{i} {Action.to_char(a_t[i])}" for i in range(len(a_t))])
print(f"action: {action_str}")
```

#### 3.2 行为记录和打印（第398-401行）

**修改前：**
```python
team.agents[1].teammate_ml_actions.append({'timestamp':t,'action':"deliver_soup()"})
print(f"P0's real behavior: {team.agents[1].teammate_ml_actions}")
print(f"P1's real behavior: {team.agents[0].teammate_ml_actions}")
```

**修改后：**
```python
# Print behavior for all agents (supporting multiple agents)
for agent_idx, agent in enumerate(team.agents):
    if hasattr(agent, 'teammate_ml_actions'):
        print(f"P{agent_idx}'s real behavior: {agent.teammate_ml_actions}")
```

#### 3.3 统计数据收集和合并（第405-416行）

**修改前：**
```python
turn_statistics_dict_agent0 = team.agents[0].turn_statistics_dict
turn_statistics_dict_agent1 = team.agents[1].turn_statistics_dict
turn_statistics_dict_both = combine_statistic_dict(turn_statistics_dict_agent0,turn_statistics_dict_agent1,map,reward)
statistics_dict['total_action_list'][0] = team.agents[1].teammate_ml_actions
statistics_dict['total_action_list'][1] = team.agents[0].teammate_ml_actions
```

**修改后：**
```python
# Support multiple agents
num_agents = len(team.agents)
turn_statistics_dicts = [agent.turn_statistics_dict for agent in team.agents]

# Combine statistics for all agents
if num_agents == 2:
    turn_statistics_dict_both = combine_statistic_dict(
        turn_statistics_dicts[0], turn_statistics_dicts[1], map, reward
    )
else:
    turn_statistics_dict_both = combine_statistic_dict_multi(
        turn_statistics_dicts, map, reward
    )

# Store action lists for all agents
statistics_dict['total_action_list'] = []
for agent_idx, agent in enumerate(team.agents):
    if hasattr(agent, 'teammate_ml_actions'):
        statistics_dict['total_action_list'].append(agent.teammate_ml_actions)
    else:
        statistics_dict['total_action_list'].append([])
```

---

### 4. `collab_overcooked/utils/utils.py`

**新增函数：** `combine_statistic_dict_multi()`

**功能：** 合并多个智能体的统计数据

**特点：**
- 支持任意数量的智能体
- 当只有2个智能体时，自动调用 `combine_statistic_dict()` 保持向后兼容
- 正确处理所有统计数据字段（communication, error, error_correction, content等）

**使用示例：**
```python
turn_statistics_dicts = [agent.turn_statistics_dict for agent in team.agents]
combined = combine_statistic_dict_multi(turn_statistics_dicts, map, reward)
```

---

## 使用方法

### 方法1: 通过 YAML 配置文件（推荐）

在 YAML 配置文件中添加多个智能体：

```yaml
agents:
  agent_0:
    type: "openai"
    model: "gpt-3.5-turbo"
    role: "Chef"
    api_key: "your_key"
  agent_1:
    type: "openai"
    model: "gpt-4"
    role: "Assistant"
    api_key: "your_key"
  agent_2:
    type: "openai"
    model: "gpt-3.5-turbo"
    role: "Assistant"
    api_key: "your_key"
```

然后运行：
```bash
python -m collab_overcooked.main --config configs/your_config.yaml
```

### 方法2: 通过 Python API

```python
from collab_overcooked.main import main

config = {
    'agents': {
        'agent_0': {
            'type': 'openai',
            'model': 'gpt-3.5-turbo',
            'role': 'Chef',
            'api_key': 'your_key'
        },
        'agent_1': {
            'type': 'openai',
            'model': 'gpt-4',
            'role': 'Assistant',
            'api_key': 'your_key'
        },
        'agent_2': {
            'type': 'openai',
            'model': 'gpt-3.5-turbo',
            'role': 'Assistant',
            'api_key': 'your_key'
        }
    },
    'environment': {
        'layout': 'cramped_room',
        'horizon': 120,
        'order': 'boiled_egg'
    }
}

main(config_path='configs/your_config.yaml')
```

---

## 向后兼容性

所有修改都保持了向后兼容性：

1. **两个智能体配置仍然有效**：如果只配置了 `agent_0` 和 `agent_1`，系统会正常工作
2. **旧的 p0/p1 参数仍然支持**：命令行参数 `--p0` 和 `--p1` 仍然可以使用
3. **统计数据格式兼容**：两个智能体时使用原有的 `combine_statistic_dict()` 函数

---

## 注意事项

1. **环境限制**：Overcooked 环境本身可能对智能体数量有限制，请确保布局支持所需的智能体数量
2. **性能考虑**：多个智能体会增加计算开销，特别是在使用 LLM 的情况下
3. **统计数据**：统计数据的结构已经更新以支持多个智能体，但某些分析脚本可能需要相应更新
4. **测试建议**：建议先使用两个智能体测试，确保向后兼容性，然后逐步增加智能体数量

---

## 测试建议

1. 首先使用两个智能体测试，确保向后兼容性
2. 然后逐步增加智能体数量进行测试
3. 检查统计数据是否正确记录和合并
4. 验证智能体之间的通信是否正常工作
5. 检查日志文件中的 `total_action_list` 是否包含所有智能体的动作
