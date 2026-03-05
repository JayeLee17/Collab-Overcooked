# 任务分配与认领机制实现指南

## 设计目标

1. **角色分工**：
   - Agent 1, 5: Chef（主厨）
   - Agent 2, 4: Assistant（助手）
   - Agent 3: Dishwasher（洗碗工，专门负责W处清洗）

2. **任务分配机制**：
   - 同时发布多个任务（orders）
   - 智能体自己认领任务
   - 完成当前任务后才能认领新任务

## 实现步骤

### 步骤1: 更新配置文件，添加特殊角色

在 `configs/default.yaml` 中：

```yaml
agents:
  agent_0:  # 对应地图中的玩家1
    role: "Chef"
    # ... 其他配置
  agent_1:  # 对应地图中的玩家2
    role: "Assistant"
    # ... 其他配置
  agent_2:  # 对应地图中的玩家3
    role: "Dishwasher"  # 新增：洗碗工角色
    # ... 其他配置
  agent_3:  # 对应地图中的玩家4
    role: "Assistant"
    # ... 其他配置
  agent_4:  # 对应地图中的玩家5
    role: "Chef"
    # ... 其他配置
```

### 步骤2: 修改 main.py，支持多任务发布

在 `main.py` 的 `main` 函数中，修改任务设置部分：

```python
# 当前代码（第252-255行）：
if variant['order'] !="" and check_recipe_parse(variant):
    mdp.start_order_list = [variant['order']]
    mdp.one_task_mode = True

# 修改为支持多任务：
if variant['order'] !="" and check_recipe_parse(variant):
    # 支持多个任务（用逗号分隔，或从配置读取）
    orders = variant.get('orders', variant.get('order', ''))
    if isinstance(orders, str):
        order_list = [o.strip() for o in orders.split(',')]
    else:
        order_list = orders if isinstance(orders, list) else [orders]
    
    mdp.start_order_list = order_list
    mdp.one_task_mode = False  # 改为 False，允许多任务模式
```

在 `configs/default.yaml` 中添加：

```yaml
environment:
  # 单个任务（向后兼容）
  order: "boiled_egg"
  # 或多个任务（新功能）
  orders: ["boiled_egg", "baked_bell_pepper", "mashed_potato_and_pea_patty"]
```

### 步骤3: 在 LLMAgents 类中添加任务认领机制

在 `collab_overcooked/agents/collab.py` 的 `LLMAgents.__init__` 中添加：

```python
def __init__(self, ...):
    # ... 现有代码 ...
    
    # 任务认领相关
    self.claimed_order = None  # 当前认领的任务
    self.task_completed = False  # 当前任务是否完成
    self.role = role  # 保存角色信息
```

在 `LLMAgents.reset` 方法中添加：

```python
def reset(self, teammates: Optional[List["LLMPair"]] = None):
    # ... 现有代码 ...
    
    # 重置任务认领状态
    self.claimed_order = None
    self.task_completed = False
```

### 步骤4: 实现任务认领逻辑

在 `collab_overcooked/agents/collab.py` 中添加新方法：

```python
def claim_task(self, state, available_orders):
    """
    智能体认领任务
    
    Args:
        state: 当前游戏状态
        available_orders: 可用任务列表（排除已被认领的任务）
    
    Returns:
        str: 认领的任务名称，如果无法认领则返回 None
    """
    # 如果已经有认领的任务且未完成，不能认领新任务
    if self.claimed_order is not None and not self.task_completed:
        return None
    
    # 根据角色选择任务
    if self.role == "Dishwasher":
        # 洗碗工不认领烹饪任务，只负责清洗
        return None
    
    # 如果没有可用任务，返回 None
    if not available_orders:
        return None
    
    # 简单的任务选择策略（可以根据需要改进）
    # Chef 优先选择需要烹饪的任务
    # Assistant 优先选择需要切菜/准备的任务
    if self.role == "Chef":
        # Chef 选择第一个可用任务
        selected_order = available_orders[0]
    elif self.role == "Assistant":
        # Assistant 选择第二个可用任务（如果有）
        selected_order = available_orders[0] if len(available_orders) > 0 else None
    else:
        selected_order = available_orders[0]
    
    self.claimed_order = selected_order
    self.task_completed = False
    return selected_order

def check_task_completion(self, state):
    """
    检查当前认领的任务是否已完成
    
    Args:
        state: 当前游戏状态
    
    Returns:
        bool: 任务是否完成
    """
    if self.claimed_order is None:
        return False
    
    # 检查任务是否在已完成列表中
    # 这需要跟踪已交付的订单
    # 可以通过检查 state.order_list 的变化来判断
    # 或者通过奖励信号来判断
    
    # 简化实现：如果当前认领的任务不在 order_list 中，说明已完成
    if self.claimed_order not in (state.order_list or []):
        self.task_completed = True
        return True
    
    return False

def get_available_orders(self, state, all_agents):
    """
    获取所有可用任务（排除已被其他智能体认领的任务）
    
    Args:
        state: 当前游戏状态
        all_agents: 所有智能体列表
    
    Returns:
        list: 可用任务列表
    """
    # 获取所有已认领的任务
    claimed_orders = set()
    for agent in all_agents:
        if hasattr(agent, 'claimed_order') and agent.claimed_order is not None:
            if not (hasattr(agent, 'task_completed') and agent.task_completed):
                claimed_orders.add(agent.claimed_order)
    
    # 返回未被认领的任务
    all_orders = state.order_list if state.order_list else []
    available = [order for order in all_orders if order not in claimed_orders]
    
    return available
```

### 步骤5: 修改 action 方法，集成任务认领逻辑

在 `collab_overcooked/agents/collab.py` 的 `action` 方法中修改：

```python
def action(self, state):
    self.state = state
    self.build_access_utensil(state)
    # ... 现有代码 ...
    
    # 【新增】任务认领逻辑
    # 1. 检查当前任务是否完成
    if self.claimed_order is not None:
        self.check_task_completion(state)
    
    # 2. 如果没有认领任务或任务已完成，尝试认领新任务
    if self.claimed_order is None or self.task_completed:
        # 获取所有队友（用于检查任务认领情况）
        all_agents = []
        if hasattr(self, 'teammates'):
            all_agents = self.teammates + [self]
        else:
            all_agents = [self]
            if self.teammate:
                all_agents.append(self.teammate)
        
        # 获取可用任务
        available_orders = self.get_available_orders(state, all_agents)
        
        # 认领任务
        if available_orders:
            claimed = self.claim_task(state, available_orders)
            if claimed:
                print(f"[Agent {self.agent_index}] 认领任务: {claimed}")
        
        # 如果任务已完成，清除认领状态
        if self.task_completed:
            print(f"[Agent {self.agent_index}] 完成任务: {self.claimed_order}")
            self.claimed_order = None
            self.task_completed = False
    
    # 3. 使用认领的任务（如果有），否则使用默认任务
    if self.claimed_order:
        self.order = self.claimed_order
    else:
        # 对于洗碗工，不设置 order（他们不处理订单）
        if self.role != "Dishwasher":
            self.order = state.current_k_order[0] if state.current_k_order else "any"
        else:
            self.order = None
    
    # ... 继续现有代码 ...
```

### 步骤6: 为洗碗工添加特殊逻辑

在 `action` 方法中，为洗碗工添加特殊处理：

```python
def action(self, state):
    # ... 前面的代码 ...
    
    # 【新增】洗碗工特殊逻辑
    if self.role == "Dishwasher":
        # 洗碗工不认领烹饪任务
        # 他们专注于清洗工作（W处）
        # 可以检查是否有脏盘子需要清洗
        # 或者简单地等待其他智能体完成交付后自动触发清洗
        
        # 洗碗工可以执行等待或移动到W处的动作
        # 具体的动作选择由 planner 决定
        pass
    
    # ... 继续现有代码 ...
```

### 步骤7: 修改环境，支持多任务模式

确保 `OvercookedEnv` 和 `OvercookedState` 正确处理多个任务：

- `state.order_list` 应该包含所有待完成的任务
- `state.current_k_order` 返回前 k 个任务（用于显示）
- 交付任务时，从 `order_list` 中移除已完成的任务

### 步骤8: 更新 Prompt，让智能体了解任务认领机制

在 prompt 文件中添加关于任务认领的说明：

```
任务分配规则：
1. 系统会同时发布多个任务
2. 每个智能体可以认领一个任务
3. 完成当前任务后才能认领新任务
4. Chef 负责烹饪任务
5. Assistant 负责准备和辅助任务
6. Dishwasher 专门负责清洗工作，不认领烹饪任务
```

## 测试建议

1. **单任务测试**：先测试单个任务，确保基本功能正常
2. **多任务测试**：测试多个任务同时发布
3. **任务认领测试**：验证智能体是否正确认领任务
4. **任务完成测试**：验证完成任务后能否认领新任务
5. **角色分工测试**：验证不同角色的智能体是否按预期工作

## 注意事项

1. **任务冲突**：需要确保多个智能体不会认领同一个任务
2. **任务完成检测**：需要准确检测任务何时完成
3. **洗碗工逻辑**：洗碗工不需要认领任务，但需要知道何时需要清洗
4. **性能考虑**：多任务模式可能会增加计算复杂度

## 扩展功能（可选）

1. **任务优先级**：为任务添加优先级，智能体优先认领高优先级任务
2. **任务协作**：某些任务可能需要多个智能体协作完成
3. **动态任务发布**：在游戏过程中动态添加新任务
4. **任务统计**：跟踪每个智能体完成的任务数量和类型
