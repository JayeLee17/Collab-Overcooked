"""
A2A Protocol Message Format
统一的消息格式定义与解析
"""

from enum import Enum
from typing import Dict, Any, Optional, Union
from dataclasses import dataclass, asdict
import json
import re


class MessageType(Enum):
    """A2A 协议消息类型"""
    PROPOSE = "PROPOSE"      # 提议执行动作
    ACCEPT = "ACCEPT"        # 接受提议
    REJECT = "REJECT"        # 拒绝提议
    INFORM = "INFORM"        # 通知状态/信息
    REQUEST = "REQUEST"      # 请求执行动作


@dataclass
class A2AMessage:
    """
    A2A 协议消息格式
    
    Attributes:
        type: 消息类型（PROPOSE/ACCEPT/REJECT/INFORM/REQUEST）
        from_: 发送者 agent_index
        to: 接收者 agent_index 或 "broadcast"
        task_id: 关联的任务ID（可选）
        content: 消息内容（动作、目标、原因等）
        metadata: 元数据（时间戳、优先级等）
    """
    type: MessageType
    from_: int
    to: Union[int, str]  # agent_index 或 "broadcast"
    task_id: Optional[int] = None
    content: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        """初始化后处理"""
        if self.content is None:
            self.content = {}
        if self.metadata is None:
            self.metadata = {
                "timestamp": 0,
                "priority": "medium",
                "requires_response": self.type in [MessageType.PROPOSE, MessageType.REQUEST]
            }
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于序列化）"""
        return {
            "type": self.type.value,
            "from": self.from_,
            "to": self.to,
            "task_id": self.task_id,
            "content": self.content,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "A2AMessage":
        """从字典创建消息"""
        return cls(
            type=MessageType(data["type"]),
            from_=data["from"],
            to=data["to"],
            task_id=data.get("task_id"),
            content=data.get("content", {}),
            metadata=data.get("metadata", {}),
        )
    
    def is_broadcast(self) -> bool:
        """是否为广播消息"""
        return self.to == "broadcast"
    
    def requires_response(self) -> bool:
        """是否需要响应"""
        return self.metadata.get("requires_response", False)


def serialize_message(message: A2AMessage) -> str:
    """序列化消息为 JSON 字符串"""
    return json.dumps(message.to_dict(), ensure_ascii=False)


def parse_message(text: str) -> Optional[A2AMessage]:
    """
    从文本中解析 A2A 消息
    
    支持格式：
    1. JSON 格式：{"type": "REQUEST", "from": 0, "to": 1, ...}
    2. 简化格式：A2A(REQUEST, from=0, to=1, action="pickup_egg")
    3. 自然语言（LLM 输出）："I request agent 1 to pickup an egg"
    """
    if not text or not isinstance(text, str):
        return None
    
    text = text.strip()
    
    # 尝试解析 JSON
    try:
        data = json.loads(text)
        return A2AMessage.from_dict(data)
    except (json.JSONDecodeError, KeyError, ValueError):
        pass
    
    # 尝试解析 A2A(...) 格式
    a2a_pattern = r'A2A\s*\(\s*(\w+)\s*,\s*from\s*=\s*(\d+)\s*,\s*to\s*=\s*(\d+|broadcast)\s*(?:,\s*([^)]+))?\s*\)'
    match = re.search(a2a_pattern, text, re.IGNORECASE)
    if match:
        msg_type_str, from_str, to_str, params_str = match.groups()
        try:
            msg_type = MessageType[msg_type_str.upper()]
            from_ = int(from_str)
            to = int(to_str) if to_str != "broadcast" else "broadcast"
            
            content = {}
            if params_str:
                # 简单解析 key="value" 格式
                param_pattern = r'(\w+)\s*=\s*"([^"]+)"'
                for key, value in re.findall(param_pattern, params_str):
                    content[key] = value
            
            return A2AMessage(
                type=msg_type,
                from_=from_,
                to=to,
                content=content
            )
        except (KeyError, ValueError):
            pass
    
    # TODO: 自然语言解析（使用 LLM 或规则模板）
    # 暂时返回 None，表示无法解析
    return None


def create_request_message(
    from_agent: int,
    to_agent: int,
    action: str,
    target: Optional[str] = None,
    reason: Optional[str] = None,
    task_id: Optional[int] = None,
    priority: str = "medium"
) -> A2AMessage:
    """便捷函数：创建 REQUEST 消息"""
    return A2AMessage(
        type=MessageType.REQUEST,
        from_=from_agent,
        to=to_agent,
        task_id=task_id,
        content={
            "action": action,
            "target": target,
            "reason": reason,
        },
        metadata={
            "priority": priority,
            "requires_response": True,
        }
    )


def create_inform_message(
    from_agent: int,
    to_agent: Union[int, str],
    action: str,
    status: str,
    details: Optional[Dict[str, Any]] = None,
    task_id: Optional[int] = None
) -> A2AMessage:
    """便捷函数：创建 INFORM 消息"""
    content = {"action": action, "status": status}
    if details:
        content.update(details)
    
    return A2AMessage(
        type=MessageType.INFORM,
        from_=from_agent,
        to=to_agent,
        task_id=task_id,
        content=content,
        metadata={"requires_response": False}
    )


def create_propose_message(
    from_agent: int,
    to_agent: int,
    action: str,
    target: Optional[str] = None,
    reason: Optional[str] = None,
    task_id: Optional[int] = None
) -> A2AMessage:
    """便捷函数：创建 PROPOSE 消息"""
    return A2AMessage(
        type=MessageType.PROPOSE,
        from_=from_agent,
        to=to_agent,
        task_id=task_id,
        content={
            "action": action,
            "target": target,
            "reason": reason,
        },
        metadata={"requires_response": True}
    )


def create_accept_message(
    from_agent: int,
    to_agent: int,
    original_message: A2AMessage,
    additional_info: Optional[Dict[str, Any]] = None
) -> A2AMessage:
    """便捷函数：创建 ACCEPT 消息（响应 PROPOSE/REQUEST）"""
    content = {"accepted": True}
    if additional_info:
        content.update(additional_info)
    
    return A2AMessage(
        type=MessageType.ACCEPT,
        from_=from_agent,
        to=to_agent,
        task_id=original_message.task_id,
        content=content,
        metadata={"requires_response": False}
    )


def create_reject_message(
    from_agent: int,
    to_agent: int,
    original_message: A2AMessage,
    reason: Optional[str] = None
) -> A2AMessage:
    """便捷函数：创建 REJECT 消息"""
    content = {"accepted": False}
    if reason:
        content["reason"] = reason
    
    return A2AMessage(
        type=MessageType.REJECT,
        from_=from_agent,
        to=to_agent,
        task_id=original_message.task_id,
        content=content,
        metadata={"requires_response": False}
    )
