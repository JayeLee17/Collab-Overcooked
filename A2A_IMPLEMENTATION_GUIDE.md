# A2A 协议实现指南

## 快速开始

### 1. 在 LLMAgents 中集成 A2A 协议

在 `collab.py` 的 `LLMAgents.__init__` 中添加：

```python
from collab_overcooked.a2a_protocol import A2AProtocol, InstructionExecutor, InstructionRegistry

class LLMAgents(LLMPair):
    def __init__(self, ...):
        # ... 现有代码 ...
        
        # A2A 协议初始化
        self.a2a_protocol = A2AProtocol(agent_index=self.agent_index, timeout=10)
        self.instruction_registry = InstructionRegistry()
        self.instruction_executor = InstructionExecutor(self.instruction_registry)
        
        # 设置执行回调（将指令动作转换为实际的 ml_action）
        self.instruction_executor.set_execution_callback(self._execute_action_from_instruction)
```

### 2. 实现动作执行回调

```python
def _execute_action_from_instruction(self, action_str: str) -> bool:
    """
    将指令动作字符串转换为实际的 ml_action
    
    Args:
        action_str: 如 "pickup(egg, ingredient_dispenser)"
        
    Returns:
        True 如果动作已设置成功
    """
    # 解析动作字符串
    # 设置 self.current_ml_action = action_str
    # 返回 True 表示动作已设置
    self.current_ml_action = action_str
    return True
```

### 3. 在 communication() 中处理 A2A 消息

修改 `LLMAgents.communication()`：

```python
def communication(self, message, state):
    # ... 现有代码 ...
    
    # 尝试解析 A2A 消息
    from collab_overcooked.a2a_protocol.message import parse_message
    a2a_msg = parse_message(message)
    
    if a2a_msg:
        # 处理 A2A 消息
        return self._handle_a2a_message(a2a_msg, state)
    
    # ... 原有的 Collab() 处理逻辑 ...
```

### 4. 实现 A2A 消息处理

```python
def _handle_a2a_message(self, message: A2AMessage, state) -> str:
    """处理 A2A 消息"""
    from collab_overcooked.a2a_protocol.message import (
        create_accept_message, create_reject_message, create_inform_message
    )
    
    # 接收消息
    self.a2a_protocol.receive_message(message)
    
    # 处理接收队列
    responses = self.a2a_protocol.process_incoming()
    
    for msg in responses:
        if msg.type == MessageType.REQUEST:
            # 决定是否接受请求
            if self._should_accept_request(msg, state):
                accept_msg = create_accept_message(
                    from_agent=self.agent_index,
                    to_agent=msg.from_,
                    original_message=msg
                )
                self.a2a_protocol.send_message(accept_msg)
                
                # 执行指令
                self.instruction_executor.execute_from_message(msg)
            else:
                reject_msg = create_reject_message(
                    from_agent=self.agent_index,
                    to_agent=msg.from_,
                    original_message=msg,
                    reason="I'm busy with another task"
                )
                self.a2a_protocol.send_message(reject_msg)
    
    # 返回动作（如果有待发送的消息，可以转换为 Collab() 格式）
    return "Action: wait(1)"  # 或返回实际动作
```

### 5. 在 action() 中发送 A2A 消息

在 `LLMAgents.action()` 中，当需要协作时：

```python
def action(self, state):
    # ... 现有代码 ...
    
    # 检查是否需要发送 A2A 消息
    if self._should_send_request(state):
        from collab_overcooked.a2a_protocol.message import create_request_message
        
        request = create_request_message(
            from_agent=self.agent_index,
            to_agent=self.get_comm_partner().agent_index,
            action="pickup_egg",
            reason="I need an egg to start cooking",
            task_id=self.task_pool.get_agent_current_task(self.agent_index)["id"] if self.task_pool else None
        )
        self.a2a_protocol.send_message(request)
    
    # 检查超时
    timed_out = self.a2a_protocol.check_timeouts(state.timestep)
    for msg in timed_out:
        print(f"[A2A] Message timed out: {msg}")
    
    # ... 继续原有逻辑 ...
```

### 6. 消息格式转换（与现有 Collab() 兼容）

为了与现有的 `Collab(request(...))` 格式兼容，可以添加转换函数：

```python
def a2a_to_collab_format(self, message: A2AMessage) -> str:
    """将 A2A 消息转换为 Collab() 格式"""
    if message.type == MessageType.REQUEST:
        return f"Collab(request({message.to}, {message.content.get('action', '')}()))"
    elif message.type == MessageType.ACCEPT:
        return f"Collab(ack({message.to}))"
    # ... 其他类型 ...
```

---

## 测试示例

创建测试脚本 `test_a2a_protocol.py`：

```python
from collab_overcooked.a2a_protocol import (
    A2AProtocol, InstructionRegistry, InstructionExecutor,
    create_request_message, create_accept_message
)

# 初始化
protocol = A2AProtocol(agent_index=0)
registry = InstructionRegistry()
executor = InstructionExecutor(registry)

# 创建请求消息
request = create_request_message(
    from_agent=0,
    to_agent=1,
    action="pickup_egg",
    reason="I need an egg"
)

# 发送消息
protocol.send_message(request)

# 模拟接收方（agent 1）
protocol_1 = A2AProtocol(agent_index=1)
protocol_1.receive_message(request)

# 处理并响应
responses = protocol_1.process_incoming()
for msg in responses:
    accept = create_accept_message(1, 0, msg)
    protocol_1.send_message(accept)

# 执行指令
executor.set_execution_callback(lambda action: print(f"Executing: {action}"))
executor.execute_from_message(request)
```

---

## 下一步工作

1. **完善 LLM 解析**：实现 `LLMInstructor.generate_message_from_text()` 使用 LLM 解析自然语言
2. **状态检查**：实现前置/后置条件检查（在 `InstructionExecutor` 中）
3. **消息持久化**：将消息历史保存到文件，支持重放
4. **性能优化**：消息队列批处理、异步发送
5. **可视化**：在 Web UI 中显示 A2A 消息流

---

## 与现有系统的兼容性

- **向后兼容**：现有的 `Collab(request(...))` 格式仍然可以工作
- **渐进迁移**：可以逐步将 `Collab()` 调用替换为 A2A 消息
- **混合模式**：支持同时使用两种格式（通过转换层）
