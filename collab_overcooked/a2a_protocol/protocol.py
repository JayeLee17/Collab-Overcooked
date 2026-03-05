"""
A2A Protocol State Machine
协议状态机与消息处理逻辑
"""

from typing import Dict, List, Optional, Callable
from enum import Enum
from collections import deque
from .message import A2AMessage, MessageType


class ProtocolState(Enum):
    """协议状态"""
    IDLE = "idle"                    # 空闲
    WAITING_RESPONSE = "waiting_response"  # 等待响应
    EXECUTING = "executing"          # 执行中
    COMPLETED = "completed"          # 已完成
    FAILED = "failed"                # 失败


class A2AProtocol:
    """
    A2A 协议处理器
    
    负责：
    1. 消息路由与分发
    2. 协议状态管理
    3. 超时与重试
    4. 消息历史记录
    """
    
    def __init__(self, agent_index: int, timeout: int = 10):
        """
        Args:
            agent_index: 当前 agent 的索引
            timeout: 消息超时时间（timestep）
        """
        self.agent_index = agent_index
        self.timeout = timeout
        
        # 消息队列
        self.incoming_queue: deque = deque()  # 接收到的消息
        self.outgoing_queue: deque = deque()  # 待发送的消息
        self.message_history: List[A2AMessage] = []  # 消息历史
        
        # 状态跟踪
        self.pending_requests: Dict[int, A2AMessage] = {}  # {message_id: message}
        self.active_conversations: Dict[int, ProtocolState] = {}  # {conversation_id: state}
        
        # 回调函数
        self.on_message_received: Optional[Callable[[A2AMessage], None]] = None
        self.on_timeout: Optional[Callable[[A2AMessage], None]] = None
    
    def send_message(self, message: A2AMessage) -> bool:
        """
        发送消息
        
        Returns:
            True 如果消息已加入发送队列
        """
        # 验证消息格式
        if message.from_ != self.agent_index:
            print(f"[A2A] Warning: message.from_ ({message.from_}) != agent_index ({self.agent_index})")
            message.from_ = self.agent_index
        
        # 设置时间戳
        if "timestamp" not in message.metadata:
            # 使用消息历史长度作为时间戳（简化实现）
            message.metadata["timestamp"] = len(self.message_history)
        
        # 加入发送队列和历史
        self.outgoing_queue.append(message)
        self.message_history.append(message)
        
        # 如果需要响应，记录为 pending
        if message.requires_response():
            conv_id = self._get_conversation_id(message)
            self.active_conversations[conv_id] = ProtocolState.WAITING_RESPONSE
            self.pending_requests[conv_id] = message
        
        return True
    
    def receive_message(self, message: A2AMessage) -> bool:
        """
        接收消息
        
        Returns:
            True 如果消息已处理
        """
        # 检查消息是否发送给自己
        if message.to != self.agent_index and message.to != "broadcast":
            return False
        
        # 加入接收队列和历史
        self.incoming_queue.append(message)
        self.message_history.append(message)
        
        # 触发回调
        if self.on_message_received:
            self.on_message_received(message)
        
        return True
    
    def process_incoming(self) -> List[A2AMessage]:
        """
        处理接收队列中的所有消息
        
        Returns:
            需要响应的消息列表
        """
        responses = []
        
        while self.incoming_queue:
            message = self.incoming_queue.popleft()
            
            # 根据消息类型处理
            if message.type == MessageType.REQUEST:
                # REQUEST 需要响应 ACCEPT/REJECT
                responses.append(message)
            elif message.type == MessageType.PROPOSE:
                # PROPOSE 需要响应 ACCEPT/REJECT
                responses.append(message)
            elif message.type == MessageType.ACCEPT:
                # ACCEPT 响应，更新状态
                conv_id = self._get_conversation_id(message)
                if conv_id in self.active_conversations:
                    self.active_conversations[conv_id] = ProtocolState.EXECUTING
            elif message.type == MessageType.REJECT:
                # REJECT 响应，标记失败
                conv_id = self._get_conversation_id(message)
                if conv_id in self.active_conversations:
                    self.active_conversations[conv_id] = ProtocolState.FAILED
            elif message.type == MessageType.INFORM:
                # INFORM 无需响应，直接处理
                pass
        
        return responses
    
    def get_outgoing_messages(self) -> List[A2AMessage]:
        """
        获取待发送的消息列表（并清空队列）
        
        Returns:
            待发送的消息列表
        """
        messages = list(self.outgoing_queue)
        self.outgoing_queue.clear()
        return messages
    
    def check_timeouts(self, current_timestep: int) -> List[A2AMessage]:
        """
        检查超时的消息
        
        Args:
            current_timestep: 当前时间步
            
        Returns:
            超时的消息列表
        """
        timed_out = []
        
        for conv_id, message in list(self.pending_requests.items()):
            timestamp = message.metadata.get("timestamp", 0)
            if current_timestep - timestamp > self.timeout:
                timed_out.append(message)
                # 标记为失败
                if conv_id in self.active_conversations:
                    self.active_conversations[conv_id] = ProtocolState.FAILED
                del self.pending_requests[conv_id]
                
                # 触发超时回调
                if self.on_timeout:
                    self.on_timeout(message)
        
        return timed_out
    
    def _get_conversation_id(self, message: A2AMessage) -> int:
        """生成对话ID（用于跟踪同一对话的消息）"""
        # 使用 (from, to, task_id) 的组合作为对话ID
        return hash((message.from_, message.to, message.task_id or 0))
    
    def get_conversation_state(self, message: A2AMessage) -> Optional[ProtocolState]:
        """获取对话状态"""
        conv_id = self._get_conversation_id(message)
        return self.active_conversations.get(conv_id)
    
    def mark_completed(self, message: A2AMessage):
        """标记对话为已完成"""
        conv_id = self._get_conversation_id(message)
        if conv_id in self.active_conversations:
            self.active_conversations[conv_id] = ProtocolState.COMPLETED
        if conv_id in self.pending_requests:
            del self.pending_requests[conv_id]
    
    def get_message_history(self, limit: Optional[int] = None) -> List[A2AMessage]:
        """获取消息历史"""
        if limit:
            return self.message_history[-limit:]
        return self.message_history.copy()

    def to_dict(self) -> Dict:
        """将消息历史序列化为可 JSON 化的字典，供统计保存使用"""
        return {
            "agent_index": self.agent_index,
            "total_messages": len(self.message_history),
            "message_history": [
                {
                    "type": msg.type.value,
                    "from": msg.from_,
                    "to": msg.to,
                    "task_id": msg.task_id,
                    "content": msg.content,
                    "timestamp": msg.metadata.get("timestamp", -1),
                    "role": msg.metadata.get("role", ""),
                }
                for msg in self.message_history
            ],
        }
