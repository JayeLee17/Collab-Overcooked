"""
Rule Template Engine
规则模板引擎：使用预定义规则生成指令
"""

from typing import Dict, Any, Optional, List
from collab_overcooked.a2a_protocol.message import A2AMessage, MessageType, create_request_message


class RuleTemplateEngine:
    """
    规则模板引擎
    
    功能：
    1. 使用预定义规则生成指令
    2. 支持条件匹配（if-then）
    3. 支持参数化模板
    """
    
    def __init__(self):
        self.rules: List[Dict[str, Any]] = []
        self._load_default_rules()
    
    def _load_default_rules(self):
        """加载默认规则"""
        self.rules = [
            {
                "name": "request_ingredient",
                "condition": lambda state: (
                    state.get("my_role") == "Chef" and
                    state.get("my_held_object") is None and
                    state.get("task_order") is not None
                ),
                "action": lambda state: create_request_message(
                    from_agent=state["agent_index"],
                    to_agent=state.get("partner_index"),
                    action="pickup_egg",  # 根据 order 动态选择
                    reason="I need ingredients to start cooking",
                    task_id=state.get("task_id"),
                ),
            },
            {
                "name": "request_dish",
                "condition": lambda state: (
                    state.get("order_need_dish") is True and
                    state.get("my_held_object") is not None and
                    state.get("my_held_object").get("name") != "dish"
                ),
                "action": lambda state: create_request_message(
                    from_agent=state["agent_index"],
                    to_agent=state.get("partner_index"),
                    action="get_dish",
                    reason="I need a dish to deliver the food",
                    task_id=state.get("task_id"),
                ),
            },
            # 更多规则...
        ]
    
    def generate_instruction(self, state: Dict[str, Any]) -> Optional[A2AMessage]:
        """
        根据状态和规则生成指令
        
        Args:
            state: 环境状态字典
            
        Returns:
            生成的 A2A 消息（如果匹配到规则）
        """
        for rule in self.rules:
            try:
                if rule["condition"](state):
                    message = rule["action"](state)
                    return message
            except Exception as e:
                print(f"[RuleEngine] Error evaluating rule {rule['name']}: {e}")
                continue
        
        return None
    
    def add_rule(self, name: str, condition: callable, action: callable):
        """添加新规则"""
        self.rules.append({
            "name": name,
            "condition": condition,
            "action": action,
        })
    
    def remove_rule(self, name: str) -> bool:
        """删除规则"""
        for i, rule in enumerate(self.rules):
            if rule["name"] == name:
                del self.rules[i]
                return True
        return False
