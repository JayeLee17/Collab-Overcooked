"""
Instruction Registry
指令注册表：管理指令→动作序列的映射
"""

from typing import Dict, List, Optional, Callable
from dataclasses import dataclass


@dataclass
class InstructionMapping:
    """指令映射定义"""
    instruction: str              # 指令名称（如 "pickup_egg"）
    action_sequence: List[str]    # 动作序列（如 ["pickup(egg, I)", "go_to(counter(3,1))"]）
    description: str              # 指令描述
    preconditions: Optional[List[str]] = None  # 前置条件（可选）
    postconditions: Optional[List[str]] = None  # 后置条件（可选）


class InstructionRegistry:
    """
    指令注册表
    
    支持：
    1. 静态注册（硬编码映射）
    2. 动态注册（运行时添加）
    3. 规则模板集成（预留接口）
    """
    
    def __init__(self):
        self._mappings: Dict[str, InstructionMapping] = {}
        self._load_default_mappings()
    
    def _load_default_mappings(self):
        """加载默认指令映射"""
        default_mappings = [
            InstructionMapping(
                instruction="pickup_egg",
                action_sequence=[
                    "pickup(egg, ingredient_dispenser)",
                    "go_to(counter(3,1))",
                    "place_obj_on_counter()"
                ],
                description="从食材分发器取鸡蛋并放到柜台",
                preconditions=["ingredient_dispenser has egg"],
                postconditions=["counter(3,1) has egg"]
            ),
            InstructionMapping(
                instruction="pickup_carrot",
                action_sequence=[
                    "pickup(carrot, ingredient_dispenser)",
                    "go_to(counter(3,1))",
                    "place_obj_on_counter()"
                ],
                description="从食材分发器取胡萝卜并放到柜台",
            ),
            InstructionMapping(
                instruction="get_dish",
                action_sequence=[
                    "go_to(dish_dispenser)",
                    "pickup(dish, dish_dispenser)"
                ],
                description="从盘子分发器取盘子",
                preconditions=["clean_dishes_available > 0"],
            ),
            InstructionMapping(
                instruction="place_on_counter",
                action_sequence=[
                    "go_to(counter(3,1))",
                    "place_obj_on_counter()"
                ],
                description="将手中物品放到柜台",
                preconditions=["agent holds object"],
            ),
            InstructionMapping(
                instruction="deliver_food",
                action_sequence=[
                    "go_to(serving_location)",
                    "deliver_soup()"
                ],
                description="交付食物到服务台",
                preconditions=["agent holds finished food"],
            ),
            InstructionMapping(
                instruction="wash_dishes",
                action_sequence=[
                    "go_to(water0)",
                    "wash(water0)"
                ],
                description="在洗碗池清洗脏盘子",
                preconditions=["pending_wash_jobs > 0"],
            ),
        ]
        
        for mapping in default_mappings:
            self.register(mapping)
    
    def register(self, mapping: InstructionMapping):
        """注册指令映射"""
        self._mappings[mapping.instruction] = mapping
    
    def get(self, instruction: str) -> Optional[InstructionMapping]:
        """获取指令映射"""
        return self._mappings.get(instruction)
    
    def has(self, instruction: str) -> bool:
        """检查指令是否存在"""
        return instruction in self._mappings
    
    def list_all(self) -> List[str]:
        """列出所有已注册的指令"""
        return list(self._mappings.keys())
    
    def search_by_keyword(self, keyword: str) -> List[InstructionMapping]:
        """根据关键词搜索指令"""
        keyword = keyword.lower()
        results = []
        for mapping in self._mappings.values():
            if (keyword in mapping.instruction.lower() or 
                keyword in mapping.description.lower()):
                results.append(mapping)
        return results
    
    def update(self, instruction: str, action_sequence: List[str], 
               description: Optional[str] = None):
        """更新指令映射"""
        if instruction in self._mappings:
            old = self._mappings[instruction]
            self._mappings[instruction] = InstructionMapping(
                instruction=instruction,
                action_sequence=action_sequence,
                description=description or old.description,
                preconditions=old.preconditions,
                postconditions=old.postconditions,
            )
        else:
            self.register(InstructionMapping(
                instruction=instruction,
                action_sequence=action_sequence,
                description=description or f"Custom instruction: {instruction}",
            ))
    
    def remove(self, instruction: str) -> bool:
        """删除指令映射"""
        if instruction in self._mappings:
            del self._mappings[instruction]
            return True
        return False
