"""
Instruction Executor
指令执行器：将指令转换为动作序列并执行
"""

from typing import List, Optional, Callable, Dict, Any
from .registry import InstructionRegistry, InstructionMapping
from .message import A2AMessage


class InstructionExecutor:
    """
    指令执行器
    
    负责：
    1. 解析指令名称
    2. 从注册表获取动作序列
    3. 执行动作序列（调用回调函数）
    4. 跟踪执行状态
    """
    
    def __init__(self, registry: Optional[InstructionRegistry] = None):
        """
        Args:
            registry: 指令注册表（如果为 None，创建新的）
        """
        self.registry = registry or InstructionRegistry()
        self.current_instruction: Optional[str] = None
        self.current_sequence: List[str] = []
        self.current_step: int = 0
        self.execution_callback: Optional[Callable[[str], bool]] = None
    
    def set_execution_callback(self, callback: Callable[[str], bool]):
        """
        设置动作执行回调
        
        Args:
            callback: 函数(action_str) -> bool，返回 True 表示动作执行成功
        """
        self.execution_callback = callback
    
    def execute_instruction(self, instruction: str, 
                          context: Optional[Dict[str, Any]] = None) -> bool:
        """
        执行指令
        
        Args:
            instruction: 指令名称（如 "pickup_egg"）
            context: 上下文信息（可选，用于参数替换）
            
        Returns:
            True 如果指令执行成功
        """
        # 从注册表获取映射
        mapping = self.registry.get(instruction)
        if not mapping:
            print(f"[Executor] Unknown instruction: {instruction}")
            return False
        
        # 检查前置条件（简化实现，实际应该检查环境状态）
        if mapping.preconditions:
            # TODO: 实现前置条件检查
            pass
        
        # 设置当前执行状态
        self.current_instruction = instruction
        self.current_sequence = mapping.action_sequence.copy()
        self.current_step = 0
        
        # 执行动作序列
        if self.execution_callback:
            for action_str in self.current_sequence:
                # 参数替换（如果有 context）
                if context:
                    action_str = self._substitute_params(action_str, context)
                
                # 执行动作
                success = self.execution_callback(action_str)
                if not success:
                    print(f"[Executor] Action failed: {action_str}")
                    return False
                self.current_step += 1
        
        # 检查后置条件（简化实现）
        if mapping.postconditions:
            # TODO: 实现后置条件验证
            pass
        
        # 重置状态
        self.current_instruction = None
        self.current_sequence = []
        self.current_step = 0
        
        return True
    
    def execute_from_message(self, message: A2AMessage) -> bool:
        """
        从 A2A 消息中提取指令并执行
        
        Args:
            message: A2A 消息（content.action 包含指令名称）
            
        Returns:
            True 如果执行成功
        """
        if not message.content or "action" not in message.content:
            print(f"[Executor] Message has no action: {message}")
            return False
        
        instruction = message.content["action"]
        context = message.content.copy()
        
        return self.execute_instruction(instruction, context)
    
    def _substitute_params(self, action_str: str, context: Dict[str, Any]) -> str:
        """
        替换动作字符串中的参数
        
        例如：action_str = "pickup({item}, {location})"
             context = {"item": "egg", "location": "ingredient_dispenser"}
        结果： "pickup(egg, ingredient_dispenser)"
        """
        # 简单实现：使用 format 或 replace
        try:
            return action_str.format(**context)
        except (KeyError, ValueError):
            # 如果格式化失败，返回原字符串
            return action_str
    
    def get_current_progress(self) -> Dict[str, Any]:
        """获取当前执行进度"""
        return {
            "instruction": self.current_instruction,
            "total_steps": len(self.current_sequence),
            "current_step": self.current_step,
            "progress": self.current_step / len(self.current_sequence) if self.current_sequence else 0.0,
        }
    
    def is_executing(self) -> bool:
        """是否正在执行指令"""
        return self.current_instruction is not None
