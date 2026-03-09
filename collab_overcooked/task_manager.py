"""
TaskPool: 多任务管理器
负责管理多个订单的生命周期：发布 → 认领 → 执行 → 完成
支持 Chef/Assistant 按同一任务自动配对
"""

import copy
from collections import defaultdict
from typing import List, Dict, Optional, Any, Tuple


class TaskPool:
    """公共任务池，管理所有订单的生命周期"""

    def __init__(self, order_list: List[str], num_concurrent_tasks: int = 3,
                 max_total_tasks: int = 0):
        """
        Args:
            order_list: 初始订单名称列表，如 ["boiled_egg", "boiled_egg", "boiled_egg"]
            num_concurrent_tasks: 同时保持的活跃任务数量
            max_total_tasks: 最大总任务数（0 = 无限制，仅完成 order_list 中的任务）
        """
        self.num_concurrent_tasks = num_concurrent_tasks
        self.max_total_tasks = max_total_tasks
        self._order_templates = list(order_list)  # 用于补充新任务的模板
        self._next_id = 0
        self.tasks: List[Dict[str, Any]] = []
        for order in order_list:
            self._add_task_internal(order)
        # 洗碗任务队列（delivery 后自动添加）
        self.wash_queue: List[Dict[str, Any]] = []
        # 统计
        self.stats = {
            "total_completed": 0,
            "total_wash_done": 0,
            "task_durations": [],  # (task_id, order, duration)
        }

    def _add_task_internal(self, order: str) -> Dict[str, Any]:
        """内部方法：添加任务"""
        task = {
            "id": self._next_id,
            "order": order,
            "status": "pending",
            "claimed_by": [],
            "roles": {},
            "start_time": None,
            "complete_time": None,
        }
        self.tasks.append(task)
        self._next_id += 1
        return task

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_available_tasks(self, role: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取可认领的任务列表

        Args:
            role: 如果指定角色，只返回还缺该角色的任务
        Returns:
            可认领任务列表
        """
        available = []
        for t in self.tasks:
            if t["status"] in ("pending", "claimed"):
                if role is None:
                    available.append(t)
                elif role.lower() == "dishwasher":
                    # Dishwasher 不认领烹饪任务
                    continue
                elif role.lower() == "chef":
                    # Chef 只认领还没有 Chef 的任务
                    has_chef = any(r.lower() == "chef" for r in t["roles"].values())
                    if not has_chef:
                        available.append(t)
                elif role.lower() == "assistant":
                    # Assistant 只认领还没有 Assistant 的任务
                    has_assistant = any(r.lower() == "assistant" for r in t["roles"].values())
                    if not has_assistant:
                        available.append(t)
        return available

    def get_agent_current_task(self, agent_index: int) -> Optional[Dict[str, Any]]:
        """获取 Agent 当前认领的任务"""
        for t in self.tasks:
            if agent_index in t["claimed_by"] and t["status"] in ("pending", "claimed", "in_progress"):
                return t
        return None

    def get_task_partner(self, agent_index: int) -> Optional[int]:
        """获取同一任务中的协作伙伴 agent_index"""
        task = self.get_agent_current_task(agent_index)
        if task is None:
            return None
        partners = [idx for idx in task["claimed_by"] if idx != agent_index]
        return partners[0] if partners else None

    def get_task_teammates(self, agent_index: int) -> List[int]:
        """获取同一任务中的所有队友 agent_index 列表"""
        task = self.get_agent_current_task(agent_index)
        if task is None:
            return []
        return [idx for idx in task["claimed_by"] if idx != agent_index]

    # ------------------------------------------------------------------
    # 认领 / 状态流转
    # ------------------------------------------------------------------

    def claim_task(self, task_id: int, agent_index: int, role: str) -> bool:
        """Agent 认领任务

        Args:
            task_id: 任务 ID
            agent_index: Agent 索引
            role: Agent 角色 (Chef/Assistant/Dishwasher)
        Returns:
            是否成功认领
        """
        if task_id < 0 or task_id >= len(self.tasks):
            return False
        task = self.tasks[task_id]
        if task["status"] == "completed":
            return False
        if agent_index in task["claimed_by"]:
            return True  # 已经认领过
        # 一个 agent 同一时刻只能认领一个活跃任务
        current = self.get_agent_current_task(agent_index)
        if current is not None and current["id"] != task_id and current["status"] in ("pending", "claimed", "in_progress"):
            return False

        # 检查该角色是否已被占用
        role_lower = role.lower()
        if role_lower in ("chef", "assistant"):
            existing_roles = [r.lower() for r in task["roles"].values()]
            if role_lower in existing_roles:
                return False  # 该角色位已被占用

        task["claimed_by"].append(agent_index)
        task["roles"][agent_index] = role
        task["status"] = "claimed"
        return True

    def start_task(self, task_id: int, timestep: int = 0):
        """标记任务开始执行"""
        if 0 <= task_id < len(self.tasks):
            task = self.tasks[task_id]
            if task["status"] == "claimed":
                task["status"] = "in_progress"
                task["start_time"] = timestep

    def complete_task(self, task_id: int, timestep: int = 0):
        """标记任务完成，同时生成洗碗任务"""
        if 0 <= task_id < len(self.tasks):
            task = self.tasks[task_id]
            task["status"] = "completed"
            task["complete_time"] = timestep
            # 统计
            self.stats["total_completed"] += 1
            duration = (timestep - task["start_time"]) if task["start_time"] is not None else 0
            self.stats["task_durations"].append((task_id, task["order"], duration))

    def release_agent(self, agent_index: int):
        """释放 Agent 的当前任务（任务完成后调用）"""
        # Agent 的 claimed_task 状态由 get_agent_current_task 自动管理
        pass

    def replenish_tasks(self) -> List[Dict[str, Any]]:
        """补充任务，使活跃任务数量回到 num_concurrent_tasks

        Returns:
            新添加的任务列表（可能为空）
        """
        if self.max_total_tasks > 0 and self._next_id >= self.max_total_tasks:
            return []  # 已达到最大总任务数
        active = [t for t in self.tasks if t["status"] != "completed"]
        new_tasks = []
        while len(active) + len(new_tasks) < self.num_concurrent_tasks:
            if self.max_total_tasks > 0 and (self._next_id + len(new_tasks)) >= self.max_total_tasks:
                break
            # 从模板中轮转选取 order
            template_idx = (self._next_id + len(new_tasks)) % max(len(self._order_templates), 1)
            order = self._order_templates[template_idx] if self._order_templates else "boiled_egg"
            new_task = self._add_task_internal(order)
            new_tasks.append(new_task)
        return new_tasks

    # ------------------------------------------------------------------
    # 洗碗任务
    # ------------------------------------------------------------------

    def add_wash_job(self, timestep: int, wash_time: int):
        """添加洗碗任务（delivery 后触发）"""
        self.wash_queue.append({
            "start_time": timestep,
            "finish_time": timestep + wash_time,
            "status": "washing",  # washing / done
        })

    def get_pending_wash_jobs(self) -> List[Dict[str, Any]]:
        """获取待处理的洗碗任务"""
        return [w for w in self.wash_queue if w["status"] == "washing"]

    def update_wash_jobs(self, current_timestep: int) -> int:
        """更新洗碗任务状态，返回新完成的洗碗数量"""
        completed = 0
        for w in self.wash_queue:
            if w["status"] == "washing" and current_timestep >= w["finish_time"]:
                w["status"] = "done"
                completed += 1
        self.stats["total_wash_done"] += completed
        return completed

    # ------------------------------------------------------------------
    # 工具冲突检测
    # ------------------------------------------------------------------

    def detect_utensil_conflicts(self, agent_plans: Dict[int, Optional[str]]) -> List[Tuple[str, List[int]]]:
        """检测工具使用冲突

        根据每个 Agent 当前动作的目标工具，找出多个 Agent 同时操作同一工具的冲突。
        冲突解决规则：agent_index 较小的 Agent 优先。

        Args:
            agent_plans: {agent_index: target_utensil_name_or_None}
        Returns:
            [(utensil_name, [agent_idx_1, agent_idx_2, ...]), ...] 有冲突的工具列表
            其中 agent 列表已按 index 升序排列，第一个即为优先者。
        """
        utensil_to_agents: Dict[str, List[int]] = defaultdict(list)
        for agent_idx, utensil in agent_plans.items():
            if utensil:
                utensil_to_agents[utensil].append(agent_idx)
        return [(u, sorted(agents)) for u, agents in utensil_to_agents.items() if len(agents) > 1]

    def get_conflict_resolution(self, agent_plans: Dict[int, Optional[str]]) -> Dict[int, Optional[str]]:
        """给出冲突解决建议

        Args:
            agent_plans: {agent_index: target_utensil_name_or_None}
        Returns:
            {agent_index: resolution_advice_or_None}
            - None 表示该 Agent 无冲突（可正常操作）
            - 字符串 "wait" 表示该 Agent 需要等待
        """
        conflicts = self.detect_utensil_conflicts(agent_plans)
        advice: Dict[int, Optional[str]] = {idx: None for idx in agent_plans}
        for utensil, agents in conflicts:
            priority = agents[0]  # index 最小的优先
            for ag in agents[1:]:
                advice[ag] = f"wait_for_{utensil}"
        return advice

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def all_done(self) -> bool:
        """所有任务是否完成"""
        return all(t["status"] == "completed" for t in self.tasks)

    def summary(self) -> str:
        """返回任务池状态摘要"""
        completed = sum(1 for t in self.tasks if t["status"] == "completed")
        active = sum(1 for t in self.tasks if t["status"] != "completed")
        lines = [f"[TaskPool Status] total: {len(self.tasks)}, active: {active}, completed: {completed}"]
        for t in self.tasks:
            agents_str = ", ".join(
                f"P{idx}({t['roles'].get(idx, '?')})" for idx in t["claimed_by"]
            )
            duration = ""
            if t["status"] == "completed" and t["start_time"] is not None and t["complete_time"] is not None:
                duration = f" (duration: {t['complete_time'] - t['start_time']}t)"
            lines.append(
                f"  Task {t['id']}: {t['order']} | {t['status']} | agents: [{agents_str}]{duration}"
            )
        wash_pending = len([w for w in self.wash_queue if w["status"] == "washing"])
        wash_done = len([w for w in self.wash_queue if w["status"] == "done"])
        lines.append(f"  Wash queue: {wash_pending} pending, {wash_done} done")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（用于 JSON 保存）"""
        return {
            "tasks": copy.deepcopy(self.tasks),
            "wash_queue": copy.deepcopy(self.wash_queue),
            "stats": copy.deepcopy(self.stats),
        }
