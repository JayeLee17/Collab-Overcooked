# A to A 协议与指令学习框架设计文档

## 一、研究目标

在现有多智能体多任务系统基础上，构建统一的 Agent-to-Agent（A to A）协议与指令学习机制，实现：
1. **统一协议规范**：PROPOSE / ACCEPT / INFORM / REQUEST 等基础交互指令
2. **指令→动作序列**：将语言化指令转换为原子动作序列
3. **可扩展接口**：支持 LLM 与规则模板两种实现方式

---

## 二、协议设计

### 2.1 消息格式规范

```python
# 统一消息格式
A2AMessage = {
    "type": "PROPOSE" | "ACCEPT" | "REJECT" | "INFORM" | "REQUEST",
    "from": agent_index,      # 发送者
    "to": agent_index | "broadcast",  # 接收者（broadcast 表示广播）
    "task_id": int | None,    # 关联的任务ID（可选）
    "content": {
        "action": str,        # 请求的动作（如 "pickup_egg", "place_on_counter"）
        "target": str,        # 目标对象/位置（如 "counter(3,1)", "pot0"）
        "reason": str,        # 原因/上下文
        "deadline": int | None,  # 期望完成时间步（可选）
    },
    "metadata": {
        "timestamp": int,     # 时间戳
        "priority": "high" | "medium" | "low",
        "requires_response": bool,  # 是否需要响应
    }
}
```

### 2.2 基础指令语义

| 指令 | 语义 | 触发条件 | 响应要求 |
|------|------|----------|----------|
| **PROPOSE** | 提议执行某个动作 | Agent 需要协作但不确定对方是否同意 | 需要 ACCEPT/REJECT |
| **ACCEPT** | 接受提议 | 收到 PROPOSE 且同意 | 无 |
| **REJECT** | 拒绝提议 | 收到 PROPOSE 但不同意 | 可选：提供原因 |
| **INFORM** | 通知状态/信息 | Agent 完成动作或状态变化 | 无需响应 |
| **REQUEST** | 请求执行动作 | Agent 明确需要对方执行某个动作 | 需要 ACCEPT/REJECT |

### 2.3 指令→动作序列映射

```python
# 指令到动作序列的映射表（可配置）
INSTRUCTION_TO_ACTIONS = {
    "pickup_egg": ["pickup(egg, ingredient_dispenser)", "go_to(counter(3,1))", "place_obj_on_counter()"],
    "place_on_counter": ["go_to(counter(3,1))", "place_obj_on_counter()"],
    "get_dish": ["go_to(dish_dispenser)", "pickup(dish, dish_dispenser)"],
    "deliver_food": ["go_to(serving_location)", "deliver_soup()"],
    # ... 更多映射
}
```

---

## 三、实现架构

### 3.1 核心模块

```
collab_overcooked/
├── a2a_protocol/              # A to A 协议模块
│   ├── __init__.py
│   ├── message.py            # 消息格式定义与解析
│   ├── protocol.py           # 协议状态机与处理逻辑
│   ├── instruction_executor.py  # 指令→动作序列执行器
│   └── registry.py           # 指令注册表（支持动态扩展）
├── instruction_learning/      # 指令学习模块
│   ├── __init__.py
│   ├── llm_instructor.py     # LLM 驱动的指令生成
│   ├── rule_template.py      # 规则模板引擎
│   └── action_planner.py    # 动作序列规划器
```

### 3.2 集成点

- **现有系统集成**：在 `LLMAgents.communication()` 中调用 A2A 协议处理器
- **任务池集成**：A2A 消息可关联 `TaskPool` 中的任务
- **状态同步**：通过 INFORM 消息实现跨 Agent 状态同步

---

## 四、实现步骤（分阶段）

### Phase 1: 协议基础设施（1-2周）
- [x] 消息格式定义（`message.py`）
- [ ] 协议状态机（`protocol.py`）
- [ ] 消息路由与广播机制
- [ ] 与现有 `communication()` 集成

### Phase 2: 基础指令实现（2-3周）
- [ ] PROPOSE/ACCEPT/REJECT 实现
- [ ] REQUEST 实现
- [ ] INFORM 实现
- [ ] 指令注册表（`registry.py`）

### Phase 3: 指令执行器（2-3周）
- [ ] 指令→动作序列映射表
- [ ] 动作序列执行器（`instruction_executor.py`）
- [ ] 执行状态跟踪与回滚

### Phase 4: 指令学习接口（2-3周）
- [ ] LLM 指令生成器（`llm_instructor.py`）
- [ ] 规则模板引擎（`rule_template.py`）
- [ ] 可插拔接口设计

### Phase 5: 测试与优化（1-2周）
- [ ] 单元测试
- [ ] 集成测试（多任务场景）
- [ ] 性能优化

---

## 五、使用示例

### 5.1 Chef 请求 Assistant 取鸡蛋

```python
# Chef (A0) 发送 REQUEST
message = A2AMessage(
    type="REQUEST",
    from_=0,
    to=1,  # Assistant
    task_id=0,
    content={
        "action": "pickup_egg",
        "target": "ingredient_dispenser",
        "reason": "I need an egg to start cooking",
    }
)

# Assistant (A1) 响应 ACCEPT 并执行
response = A2AMessage(
    type="ACCEPT",
    from_=1,
    to=0,
    task_id=0,
    content={"action": "pickup_egg", "status": "executing"}
)

# Assistant 执行动作序列
executor.execute("pickup_egg")  # → ["pickup(egg, I)", "go_to(counter(3,1))", "place_obj_on_counter()"]

# 完成后发送 INFORM
inform = A2AMessage(
    type="INFORM",
    from_=1,
    to=0,
    content={"action": "pickup_egg", "status": "completed", "location": "counter(3,1)"}
)
```

### 5.2 跨任务协调（工具冲突）

```python
# A2 和 A4 都需要使用 blender0
# A2 (index 小) 优先，发送 PROPOSE
propose = A2AMessage(
    type="PROPOSE",
    from_=2,
    to=4,
    content={"action": "use_blender", "target": "blender0", "reason": "I claimed task 1 first"}
)

# A4 响应 ACCEPT（根据冲突协调规则）
accept = A2AMessage(type="ACCEPT", from_=4, to=2, ...)
```

---

## 六、技术要点

### 6.1 消息持久化
- 消息历史存储在 `LLMAgents` 的 `conversation_history` 中
- 支持消息重放与调试

### 6.2 超时与重试
- REQUEST/PROPOSE 设置超时（如 10 个 timestep）
- 超时后自动重试或降级处理

### 6.3 优先级机制
- 高优先级消息（如工具冲突）优先处理
- 低优先级消息（如状态通知）可延迟

---

## 七、评估指标

1. **协议覆盖率**：PROPOSE/ACCEPT/INFORM/REQUEST 使用频率
2. **指令执行成功率**：指令→动作序列的成功率
3. **通信效率**：平均消息往返时间
4. **任务完成率**：使用 A2A 协议后的任务完成率提升

---

## 八、后续扩展

- **多轮协商**：支持复杂的多轮 PROPOSE-ACCEPT-REJECT 协商
- **指令学习**：从历史对话中学习新的指令→动作映射
- **跨场景迁移**：将指令模板迁移到其他 Overcooked 地图
