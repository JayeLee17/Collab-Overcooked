#!/usr/bin/env python3
"""
A2A Protocol 基础功能测试
验证消息创建、解析、协议状态机等核心功能
"""

import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from collab_overcooked.a2a_protocol import (
    A2AMessage, MessageType, A2AProtocol,
    InstructionRegistry, InstructionExecutor,
    create_request_message, create_accept_message, create_inform_message,
    parse_message, serialize_message
)

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

def ok(msg): print(f"{GREEN}  ✓ {msg}{RESET}")
def fail(msg): print(f"{RED}  ✗ {msg}{RESET}")
def info(msg): print(f"{YELLOW}  → {msg}{RESET}")

passed = 0
failed = 0

def check(cond, ok_msg, fail_msg):
    global passed, failed
    if cond:
        ok(ok_msg)
        passed += 1
    else:
        fail(fail_msg)
        failed += 1
    return cond

print("\n" + "="*60)
print(" A2A Protocol 基础功能测试")
print("="*60)

# ── Test 1: 消息创建 ─────────────────────────────────────────────────────────
print("\n── Test 1: 消息创建 ──")

request = create_request_message(
    from_agent=0,
    to_agent=1,
    action="pickup_egg",
    reason="I need an egg",
    task_id=0
)
check(request.type == MessageType.REQUEST,
      "create_request_message 创建 REQUEST 消息",
      "create_request_message 失败")
check(request.from_ == 0 and request.to == 1,
      "消息的 from/to 字段正确",
      "消息的 from/to 字段错误")

inform = create_inform_message(
    from_agent=1,
    to_agent=0,
    action="pickup_egg",
    status="completed"
)
check(inform.type == MessageType.INFORM,
      "create_inform_message 创建 INFORM 消息",
      "create_inform_message 失败")

# ── Test 2: 消息序列化/反序列化 ───────────────────────────────────────────────
print("\n── Test 2: 消息序列化/反序列化 ──")

json_str = serialize_message(request)
check(isinstance(json_str, str) and len(json_str) > 0,
      "消息序列化为 JSON 字符串",
      "消息序列化失败")

parsed = parse_message(json_str)
check(parsed is not None and parsed.type == MessageType.REQUEST,
      "从 JSON 解析消息成功",
      "从 JSON 解析消息失败")

# ── Test 3: A2A Protocol 状态机 ──────────────────────────────────────────────
print("\n── Test 3: A2A Protocol 状态机 ──")

protocol = A2AProtocol(agent_index=0, timeout=10)
check(protocol.agent_index == 0,
      "A2AProtocol 初始化成功",
      "A2AProtocol 初始化失败")

protocol.send_message(request)
outgoing = protocol.get_outgoing_messages()
check(len(outgoing) == 1 and outgoing[0].type == MessageType.REQUEST,
      "发送消息并获取 outgoing 队列",
      "发送消息失败")

# 模拟接收方
protocol_1 = A2AProtocol(agent_index=1)
protocol_1.receive_message(request)
check(len(protocol_1.incoming_queue) == 1,
      "接收消息成功",
      "接收消息失败")

responses = protocol_1.process_incoming()
check(len(responses) == 1 and responses[0].type == MessageType.REQUEST,
      "处理 incoming 队列，返回需要响应的消息",
      "处理 incoming 队列失败")

# ── Test 4: 指令注册表 ───────────────────────────────────────────────────────
print("\n── Test 4: 指令注册表 ──")

registry = InstructionRegistry()
check(registry.has("pickup_egg"),
      "默认指令 'pickup_egg' 已注册",
      "默认指令未注册")

mapping = registry.get("pickup_egg")
check(mapping is not None and len(mapping.action_sequence) > 0,
      "获取指令映射成功",
      "获取指令映射失败")
info(f"  'pickup_egg' 动作序列: {mapping.action_sequence}")

# ── Test 5: 指令执行器 ───────────────────────────────────────────────────────
print("\n── Test 5: 指令执行器 ──")

executor = InstructionExecutor(registry)
executed_actions = []

def mock_execute(action_str):
    executed_actions.append(action_str)
    return True

executor.set_execution_callback(mock_execute)
success = executor.execute_instruction("pickup_egg")
check(success and len(executed_actions) > 0,
      "执行指令成功，动作序列已执行",
      "执行指令失败")
info(f"  执行的动作: {executed_actions}")

# ── Test 6: 消息解析（A2A 格式） ─────────────────────────────────────────────
print("\n── Test 6: 消息解析（A2A 格式） ──")

a2a_text = 'A2A(REQUEST, from=0, to=1, action="pickup_egg")'
parsed_a2a = parse_message(a2a_text)
check(parsed_a2a is not None and parsed_a2a.type == MessageType.REQUEST,
      "解析 A2A(...) 格式消息成功",
      "解析 A2A(...) 格式消息失败")

# ── Test 7: 超时检查 ────────────────────────────────────────────────────────
print("\n── Test 7: 超时检查 ──")

protocol_2 = A2AProtocol(agent_index=0, timeout=5)
request_old = create_request_message(0, 1, "test_action")
request_old.metadata["timestamp"] = 0  # 旧时间戳
protocol_2.send_message(request_old)

timed_out = protocol_2.check_timeouts(current_timestep=10)  # 当前时间步 10，超时阈值 5
check(len(timed_out) == 1,
      "超时消息检测成功",
      "超时消息检测失败")

# ── 总结 ─────────────────────────────────────────────────────────────────────
print("\n" + "="*60)
total = passed + failed
print(f" 结果: {passed}/{total} 通过  {'🎉' if failed == 0 else '⚠'}")
if failed > 0:
    print(f"{RED} {failed} 个测试失败，请检查上方错误信息{RESET}")
print("="*60 + "\n")

sys.exit(0 if failed == 0 else 1)
