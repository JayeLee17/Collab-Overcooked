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
        """
        加载默认指令映射。

        设计原则：
        - 使用通用指令 key，不绑定具体食材名（pickup_ingredient 而非 pickup_egg）
        - action_sequence 使用系统合法 ml_action 格式（find_motion_goals 能识别）
        - go_to(...) 格式已被 find_motion_goals 支持，可直接写入序列
        - 具体食材由 A2A 消息内容的 context 字段动态替换（{item} 占位符）
        """
        default_mappings = [
            # ── 通用取食材指令（适用所有菜品：egg/carrot/mushroom/potato/...） ──
            InstructionMapping(
                instruction="pickup_ingredient",
                action_sequence=[
                    "pickup({item}, ingredient_dispenser)",
                    "place_obj_on_counter()",
                ],
                description="从食材分发器取任意食材并放到共享柜台（item 由 context 指定）",
                preconditions=["ingredient_dispenser has {item}"],
                postconditions=["counter has {item}"],
            ),
            # ── 取盘子 ──────────────────────────────────────────────────────
            InstructionMapping(
                instruction="get_dish",
                action_sequence=[
                    "get_dish(dish_dispenser)",
                ],
                description="从盘子分发器取干净盘子",
                preconditions=["clean_dishes_available > 0"],
            ),
            # ── 放物品到柜台 ─────────────────────────────────────────────────
            InstructionMapping(
                instruction="place_on_counter",
                action_sequence=[
                    "place_obj_on_counter()",
                ],
                description="将手中物品放到共享柜台（Assistant → Chef 传递）",
                preconditions=["agent holds object"],
            ),
            # ── 放入设备（通用：pot/oven/chopping_board/blender） ─────────────
            InstructionMapping(
                instruction="put_in_utensil",
                action_sequence=[
                    "put_obj_in_utensil({utensil})",
                ],
                description="将手中物品放入目标设备（utensil 由 context 指定）",
                preconditions=["agent holds object"],
            ),
            # ── 烹饪操作（cook/cut/bake/stir/wash，utensil 由 context 指定） ──
            InstructionMapping(
                instruction="operate_utensil",
                action_sequence=[
                    "{operation}({utensil})",
                ],
                description="对目标设备执行操作（operation=cook/cut/bake/stir，utensil 由 context 指定）",
            ),
            # ── 盛盘（将锅/炉里的成品装入盘子） ────────────────────────────
            InstructionMapping(
                instruction="fill_dish",
                action_sequence=[
                    "fill_dish_with_food({utensil})",
                ],
                description="从设备中将成品装入盘子（utensil 由 context 指定）",
                preconditions=["agent holds dish"],
            ),
            # ── 交付 ────────────────────────────────────────────────────────
            InstructionMapping(
                instruction="deliver_food",
                action_sequence=[
                    "deliver_soup()",
                ],
                description="将完成品交付到服务台",
                preconditions=["agent holds finished food (with dish if required)"],
            ),
            # ── 洗碗 ────────────────────────────────────────────────────────
            InstructionMapping(
                instruction="wash_dishes",
                action_sequence=[
                    "wash(water0)",
                ],
                description="在洗碗池清洗脏盘子",
                preconditions=["pending_wash_jobs > 0"],
            ),
            # ── 导航到位置（抽象格式，由 go_to 分支处理） ─────────────────
            InstructionMapping(
                instruction="go_to_counter",
                action_sequence=[
                    "go_to(counter)",
                ],
                description="导航到共享柜台位置",
            ),
            InstructionMapping(
                instruction="go_to_serving",
                action_sequence=[
                    "go_to(serving_location)",
                ],
                description="导航到交付台",
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
