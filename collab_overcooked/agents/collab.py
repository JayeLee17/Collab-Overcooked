import itertools, os, json, re
from collections import defaultdict
from typing import Union, Optional, List, Dict, Any
import numpy as np
import pkg_resources
from collections import deque
import sys
import copy
from .modules import Module, statistics_dict, turn_statistics_dict
from overcooked_ai_py.mdp.actions import Action, Direction
from overcooked_ai_py.planning.search import find_path
from overcooked_ai_py.planning.search import get_intersect_counter
from overcooked_ai_py.planning.search import query_counter_states
from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld, OvercookedState
import queue
import warnings
import copy

from rich import print as rprint
from .modules import if_two_sentence_similar_meaning

cwd = os.getcwd()
PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")

NAME_TO_ACTION = {
    "NORTH": Direction.NORTH,
    "SOUTH": Direction.SOUTH,
    "EAST": Direction.EAST,
    "WEST": Direction.WEST,
    "INTERACT": Action.INTERACT,
    "STAY": Action.STAY,
}


class LLMPair(object):

    def __init__(
        self,
        model="gpt-3.5-turbo-0301",
        model_dirname="~/",
        local_server_api="http://localhost:8000/v1",
    ):
        self.agent_index = None
        self.model = model
        self.model_dirname = model_dirname
        self.local_server_api = local_server_api

        # API keys are now managed by individual LLM modules
        self.proxy = "http://10.29.202.138:7890"

    def load_openai_keys(self):
        # API keys are now managed by individual LLM modules
        pass

    # API key methods removed - now handled by LLM modules

    def set_agent_index(self, agent_index):
        raise NotImplementedError

    def action(self, state):
        raise NotImplementedError

    def reset(self):
        raise NotImplementedError


class LLMAgents(LLMPair):

    def __init__(
        self,
        mlam,
        layout,
        model="gpt-3.5-turbo-0301",
        model_dirname="~/",
        local_server_api="http://localhost:8000/v1",
        timeout: Optional[float] = None,
        retrival_method="recent_k",
        K=1,
        actor="",
        auto_unstuck=False,
        controller_mode="new",  # the default overcooked-ai Greedy controller
        debug_mode="N",
        agent_index=None,
        outdir=None,
        history_window=0,
        reward_tracker=None,
        response_language=None,
    ):
        super().__init__(
            model=model, model_dirname=model_dirname, local_server_api=local_server_api
        )

        self.api_key = None  # Will be set by configuration
        self.trace = True
        self.debug_mode = "Y"
        self.controller_mode = controller_mode
        self.mlam = mlam
        self.layout = layout
        self.mdp = self.mlam.mdp
        self.test_mode = False
        self.test_ml_action = deque([])

        self.out_dir = outdir
        self.agent_index = agent_index

        self.retrival_method = retrival_method
        self.K = K
        self.timeout = timeout

        self.prev_state = None
        self.auto_unstuck = auto_unstuck

        self.current_ml_action = None
        self.current_ml_action_steps = 0
        self.time_to_wait = 0
        self.possible_motion_goals = None
        self.pot_id_to_pos = []
        self.action_wait_parse = queue.Queue()
        self.end_talk = False
        self.actor = actor
        # 支持 Chef / Assistant / Dishwasher 三种角色
        _actor_to_name = {"chef": "Chef", "assistant": "Assistant", "dishwasher": "Dishwasher"}
        self.name = _actor_to_name.get(self.actor, "Assistant")
        self.role = self.name  # 与 main.py 注入的 role 一致（可被覆盖）
        self.task_pool = None  # 由 main.py 注入
        self.communication_role = "ask"
        self.recipe = {}
        self.order = ""
        self.failed_history = []
        self.state = None
        self.response_language = response_language
        # dict to record if the error in T timestamp  was corrected, or just 'wait(1)'
        self.error_correct = {}
        self.history_records = []
        self.conversation_history = []
        self.current_turn_conversation = []
        self.conversation_history_timestamp = None
        self.current_observation_snapshot = None
        self.current_recent_goal_text = "[EMPTY]"
        self.pending_collab_reply = False
        self.pending_llm_logs = []
        self.reward_tracker = reward_tracker
        self._pending_reward_event = None
        self._last_reward_entry = None
        self._forced_action_override = None
        self.communication_turn_limit = 3
        self._communication_turn_counter = 0
        self._communication_turn_timestamp = None
        self._collab_ack_consumed = False
        # Track repeated no-progress ML actions to break dead loops (e.g., repeated place_obj_on_counter()).
        self._stuck_same_action_steps = 0
        self._last_exec_snapshot = None
        if history_window is None:
            window_value = 0
        else:
            try:
                window_value = int(history_window)
            except (TypeError, ValueError):
                window_value = 0
        self.history_window = max(0, window_value)
        turn_statistics_dict_cp = copy.deepcopy(turn_statistics_dict)
        self.turn_statistics_dict = turn_statistics_dict_cp
        # A2A Protocol: 旁路记录层（不影响任何现有逻辑）
        from collab_overcooked.a2a_protocol import A2AProtocol, InstructionRegistry
        self._a2a_protocol = A2AProtocol(agent_index=agent_index or 0)
        # A2A: 指令执行状态（接受来自其他 agent 的 REQUEST 后触发）
        self._a2a_registry = InstructionRegistry()
        self._a2a_pending_instruction: Optional[str] = None  # 正在执行的 A2A 指令
        self._a2a_source_agent: Optional[int] = None         # 请求方 agent_index
        self._a2a_source_task: Optional[int] = None          # 关联 task_id
        # self.generate_layout_prompt()

    @staticmethod
    def _expand_statistics_dict(d, num_agents):
        """Expand hardcoded 2-element lists in turn_statistics_dict to support num_agents."""
        def _ensure_list_size(lst, size, template_fn):
            while len(lst) < size:
                lst.append(template_fn())

        # content sub-lists
        for key in ("observation", "reflection", "content", "action_list", "original_log"):
            if key in d.get("content", {}):
                _ensure_list_size(d["content"][key], num_agents, list)

        # statistical_data sub-lists
        sd = d.get("statistical_data", {})
        if "communication" in sd:
            _ensure_list_size(sd["communication"], num_agents,
                              lambda: {"call": 0, "turn": [], "token": []})
        if "error" in sd:
            _ensure_list_size(sd["error"], num_agents,
                              lambda: {"format_error": {"error_num": 0, "error_message": []},
                                       "validator_error": {"error_num": 0, "error_message": []}})
        if "error_correction" in sd:
            _ensure_list_size(sd["error_correction"], num_agents,
                              lambda: {"format_correction": {"correction_num": 0, "correction_tokens": []},
                                       "validator_correction": {"correction_num": 0,
                                                                "reflection_obtain": [],
                                                                "correction_tokens": []}})

    def export_runtime_state(self) -> Dict[str, Any]:
        """Serialize conversation + history context for snapshot replay."""
        return {
            "current_recent_goal_text": self.current_recent_goal_text,
            "history_records": copy.deepcopy(self.history_records),
            "conversation_history": list(self.conversation_history),
            "current_turn_conversation": list(self.current_turn_conversation),
            "conversation_history_timestamp": self.conversation_history_timestamp,
            "teammate_ml_actions": copy.deepcopy(self.teammate_ml_actions),
            "pending_collab_reply": self.pending_collab_reply,
            "communication_turn_counter": self._communication_turn_counter,
            "communication_turn_timestamp": self._communication_turn_timestamp,
            "collab_ack_consumed": self._collab_ack_consumed,
            "current_ml_action": self.current_ml_action,
            "current_ml_action_steps": self.current_ml_action_steps,
            "time_to_wait": self.time_to_wait,
            "action_wait_queue": list(self.action_wait_parse.queue),
            "failed_history": copy.deepcopy(self.failed_history),
        }

    def import_runtime_state(self, data: Optional[Dict[str, Any]]):
        """Restore runtime fields from :meth:`export_runtime_state` output."""
        if not isinstance(data, dict):
            return
        self.current_recent_goal_text = data.get(
            "current_recent_goal_text", self.current_recent_goal_text
        )
        history_records = data.get("history_records")
        if isinstance(history_records, list):
            self.history_records = copy.deepcopy(history_records)
        conversation_history = data.get("conversation_history")
        if isinstance(conversation_history, list):
            self.conversation_history = list(conversation_history)
        turn_conversation = data.get("current_turn_conversation")
        if isinstance(turn_conversation, list):
            self.current_turn_conversation = list(turn_conversation)
        self.conversation_history_timestamp = data.get(
            "conversation_history_timestamp", self.conversation_history_timestamp
        )
        teammate_actions = data.get("teammate_ml_actions")
        if isinstance(teammate_actions, list):
            self.teammate_ml_actions = copy.deepcopy(teammate_actions)
        self.pending_collab_reply = bool(data.get("pending_collab_reply", False))
        self._communication_turn_counter = int(
            data.get("communication_turn_counter", 0) or 0
        )
        timestamp_val = data.get("communication_turn_timestamp")
        self._communication_turn_timestamp = (
            int(timestamp_val) if isinstance(timestamp_val, (int, float)) else None
        )
        self._collab_ack_consumed = bool(data.get("collab_ack_consumed", False))
        self.current_ml_action = data.get("current_ml_action")
        self.current_ml_action_steps = int(data.get("current_ml_action_steps", 0) or 0)
        self.time_to_wait = int(data.get("time_to_wait", 0) or 0)
        queue_items = data.get("action_wait_queue")
        if isinstance(queue_items, list):
            while not self.action_wait_parse.empty():
                self.action_wait_parse.get()
            for item in queue_items:
                self.action_wait_parse.put(item)
        failed_history = data.get("failed_history")
        if isinstance(failed_history, list):
            self.failed_history = copy.deepcopy(failed_history)

    def set_mdp(self, mdp: OvercookedGridworld):
        self.mdp = mdp

    def create_gptmodule(
        self, module_name, file_type="txt", retrival_method="recent_k", K=10
    ):
        print(f"\n--->Initializing GPT {module_name}<---\n")

        model_name = "gpt"
        if module_name == "planner":
            prompt_file = os.path.join(
                PROMPT_DIR,
                model_name,
                f"{self.actor}_{self.communication_role}.{file_type}",
            )
            self.prompt_file = prompt_file
        # elif module_name == "explainer":
        # 	prompt_file = os.path.join(PROMPT_DIR, model_name, module_name, f'player{self.agent_index}.{file_type}')
        else:
            raise Exception(f"Module {module_name} not supported.")
        # print(prompt_file)
        self.prompt_root = "/".join(self.prompt_file.split("/")[:-1])
        messages = [{"role": "system", "content": ""}]

        return Module(
            messages,
            self.model,
            api_key=self.api_key,
            base_url=self.local_server_api,
            model_dirname=self.model_dirname,
            local_server_api=self.local_server_api,
            timeout=self.timeout,
            retrival_method=retrival_method,
            K=K,
        )

    # 	return messages
    def load_prompt_file(self, mode="origin"):
        self.prompt_dir = PROMPT_DIR + "/gpt"
        prompt = ""
        with open(self.prompt_dir + "/prompt.txt", "r") as g:
            prompt = g.read()
        if mode == "origin":
            communication_rule_dir = self.prompt_dir + "/communication_rule.txt"
        elif mode == "correct":
            communication_rule_dir = self.prompt_dir + "/correct_rule.txt"
        elif mode == "reflection":
            communication_rule_dir = self.prompt_dir + "/reflection_rule.txt"

        with open(communication_rule_dir, "r") as g:
            communication_rule = g.read()
            prompt = prompt.replace("{communication_rule}", communication_rule)
            prompt = prompt.replace("{role}", self.name)
            # P1: {teammate} 优先显示同任务配对伙伴，而非所有队友
            _comm_partner = self.get_comm_partner() if hasattr(self, 'get_comm_partner') else None
            if _comm_partner is not None:
                teammate_names = f"A{_comm_partner.agent_index}({_comm_partner.name})"
            else:
                # 兜底：显示所有队友
                _all_tm = getattr(self, 'teammates', [])
                if not _all_tm and getattr(self, 'teammate', None):
                    _all_tm = [self.teammate]
                if _all_tm:
                    teammate_names = ", ".join(
                        f"A{getattr(tm, 'agent_index', '?')}({getattr(tm, 'name', 'Unknown')})"
                        for tm in _all_tm
                    )
                else:
                    teammate_names = "Unknown"
            prompt = prompt.replace("{teammate}", teammate_names)

        with open(self.prompt_dir + "/environment_rule.txt", "r") as g:
            environment_rule = g.read()
            environment_rule = environment_rule.replace("{role}", self.name)
            prompt = prompt.replace("{environment_rule}", environment_rule)
            character_player = "Suppose you are a player who is proficient in the overcooked_ai game. Your goal is to cooperate with your teammate who is also a LLM agent in order to get a high score."
            character_reflection = "Suppose you are a reflector who is proficient in the overcooked_ai game. Your goal is to analyzes the self-correction process of an LLM player after making a wrong action,  \
				and summarize the experience to help LLM players to reference and avoid making mistakes again."
            if mode == "reflection":
                prompt = prompt.replace("{character}", character_reflection)
            else:
                prompt = prompt.replace("{character}", character_player)
        with open(self.prompt_dir + f"/{self.actor}_skill.txt", "r") as g:
            skill = g.read()
            prompt = prompt.replace("{skill}", skill)

        if self.response_language:
            prompt += (
                "\n\nLanguage\n--------\n"
                f"- Respond only in {self.response_language}.\n"
                "- Do not switch languages based on teammate messages.\n"
            )

        chef_workflow = """- The usual workflow for the chef is:
  1. Read the cooking process from your recipe. All of your decisions must be strictly guided by the recipe and should not lead to unfounded behavior.
  2. Ask the assistant to pick up ingredients from the ingredient dispenser and use the correct utensil to handle them according to the recipe. Since you do not have access to all the objects, you need to assign some tasks to the assistant while you perform other tasks in parallel.
  3. Work in parallel with the assistant to finish the order in the shortest time possible, unless there is nothing you can do in the current situation. If you have nothing to do, you can wait.
  4. Serve the dish (optional). If the recipe specifies that the dish needs to be served on a plate, you must use `fill_dish_with_food(utensil_name)` to serve the dish from the utensil first; otherwise, just pick up the food from the utensil.
  5. Use `deliver_soup()."""
        assistant_workflow = """The usual workflow for the Assistant is:  
- 1. Ask the Chef for guidance, since you do not have the recipe and need the Chef to help you plan.  
- 2. Follow the Chef’s instructions unless they are incorrect. For example, if the Chef requests a utensil that is not available on your side, you should refuse and inform him. """
        #  -1. Communicate with the chef for instruction and don't make your own plans.\n\
        #  -2. Follow the instructions given by Chef unless his instruction is wrong. For example, if the utensil he wants you to use in not in your side, you should refuse and tell him.\n"
        # load recipe
        recipe_content = ""

        # P1: Dishwasher 专用工作流
        dishwasher_workflow = """- Your primary job is keeping the kitchen supplied with clean dishes.
  1. Monitor the TaskPool for pending washing jobs.
  2. When dirty dishes need washing, go to the water sink (W) and perform wash().
  3. After washing completes, clean dishes become available at the dish dispenser (D).
  4. If no washing jobs are pending, wait for new ones."""

        if self.actor == "chef":
            self.load_recipe()
            prompt = prompt.replace("{workflow}", chef_workflow)
            prompt = prompt.replace(
                "{has_recipe}",
                "You have recipe, so you need to direct yourself and your teammates to complete the order.",
            )
        elif self.actor == "dishwasher":
            # Dishwasher: 没有 recipe，有独立工作流
            prompt = prompt.replace("{workflow}", dishwasher_workflow)
            prompt = prompt.replace(
                "{has_recipe}",
                "You are a Dishwasher. You do not handle recipes. Your job is to wash dirty dishes at the water sink (W) to replenish clean dishes at the dispenser (D).",
            )
            prompt = prompt.replace("{job}", "")
        else:
            # Assistant: 使用同任务 Chef 的 recipe
            _comm_partner = self.get_comm_partner() if hasattr(self, 'get_comm_partner') else None
            if _comm_partner is not None and hasattr(_comm_partner, 'load_recipe'):
                _comm_partner.load_recipe()
            elif getattr(self, 'teammate', None) and hasattr(self.teammate, 'load_recipe'):
                self.teammate.load_recipe()
            prompt = prompt.replace("{workflow}", assistant_workflow)
            prompt = prompt.replace(
                "{job}",
                "You only need to ask and follow Chef's instruction in communication without making plan by yourself. Because you do not have recipe, which means your plan is likely to be wrong.",
            )
            prompt = prompt.replace(
                "{has_recipe}",
                "You do not have recipe, and you should always ask the chef for guidance, rather than making bad decisions on your own.",
            )
        for key, value in self.recipe.items():
            recipe_content += value + "\n\n\n"
        self.planner.instruction_head_list = [{"role": "system", "content": prompt}]
        self.planner.instruction_head_list[0][
            "content"
        ] = self.planner.instruction_head_list[0]["content"].replace(
            "{recipe}",
            recipe_content if recipe_content != "" else "You do not have the recipe\n",
        )
        final_prompt = self.planner.instruction_head_list[0]["content"]
        prompt_store = statistics_dict.setdefault("prompt_templates", {})
        prompt_entry = {
            "role": self.name,
            "actor": self.actor,
            "order": self.order,
            "prompt": final_prompt,
        }
        if recipe_content.strip():
            prompt_entry["recipe_text"] = recipe_content
        prompt_store[self.name] = prompt_entry
        return final_prompt

    def reset(self, teammate_or_teammates):
        self.planner.reset()
        # self.explainer.reset()
        self.prev_state = None
        self.current_ml_action = None
        self.current_ml_action_steps = 0
        self.time_to_wait = 0
        self.possible_motion_goals = None
        self.current_timestep = 0
        self.teammate_ml_actions = []
        self.teammate_intentions_dict = {}

        # Support both single teammate (backward compatibility) and multiple teammates
        if isinstance(teammate_or_teammates, list):
            self.teammates = teammate_or_teammates
            # For backward compatibility, set first teammate as primary teammate
            self.teammate = teammate_or_teammates[0] if len(teammate_or_teammates) > 0 else None
        else:
            # Single teammate (backward compatibility)
            self.teammate = teammate_or_teammates
            self.teammates = [teammate_or_teammates]
        
        self._pending_reward_event = None
        self._last_reward_entry = None

    def get_comm_partner(self):
        """获取当前通讯伙伴（同一任务的队友）

        P1 阶段会根据 TaskPool 配对返回同任务 Chef/Assistant。
        当前 P0 阶段先返回 self.teammate（兼容旧行为）。
        """
        # 如果有 task_pool，优先用同任务的队友
        if self.task_pool is not None:
            partner_idx = self.task_pool.get_task_partner(self.agent_index)
            if partner_idx is not None:
                for tm in getattr(self, 'teammates', []):
                    if getattr(tm, 'agent_index', None) == partner_idx:
                        return tm
            # 在多任务模式下：没有同任务伙伴时，不回退到旧的二人 teammate，避免跨任务串线
            return None
        return getattr(self, 'teammate', None)

    # ------------------------------------------------------------------
    # P1: 任务认领相关方法
    # ------------------------------------------------------------------

    def _try_claim_task(self):
        """自动认领任务（每个 timestep 开头调用）

        规则：
        - Dishwasher 不认领烹饪任务
        - 已有任务的 Agent 不再认领新任务
        - 同区域 Agent 按 agent_index 从小到大优先认领
        - 任务完成后自动重置 Agent 状态，准备认领新任务
        """
        if self.task_pool is None:
            return
        if self.role.lower() == "dishwasher":
            return  # Dishwasher 不参与烹饪任务认领

        # 检查是否已经有任务
        my_task = self.task_pool.get_agent_current_task(self.agent_index)
        if my_task is not None:
            return  # 已有任务，不认领新的

        # P2-d: 检测是否刚完成了任务（上一个任务已 completed），清理旧状态
        prev_task_id = getattr(self, '_last_task_id', None)
        if prev_task_id is not None:
            # 上一个任务已完成，但当前没有新任务 → 重置动作状态
            self._reset_after_task_complete()
            self._last_task_id = None

        # 获取可认领任务
        available = self.task_pool.get_available_tasks(role=self.role)
        if not available:
            return

        # 按 task_id 排序，取第一个（先发布先认领）
        available.sort(key=lambda t: t["id"])
        target_task = available[0]
        success = self.task_pool.claim_task(target_task["id"], self.agent_index, self.role)
        if success:
            self._last_task_id = target_task["id"]
            print(f"[TaskClaim] A{self.agent_index}({self.role}) 认领了 Task {target_task['id']}({target_task['order']})")
            # 如果 Chef+Assistant 都就位，标记任务开始
            task_after = self.task_pool.get_agent_current_task(self.agent_index)
            if task_after:
                roles_in_task = set(r.lower() for r in task_after["roles"].values())
                if "chef" in roles_in_task and "assistant" in roles_in_task:
                    self.task_pool.start_task(task_after["id"], self.current_timestep)
                    print(f"[TaskStart] Task {task_after['id']} 已配齐 Chef+Assistant，开始执行")

    def _reset_after_task_complete(self):
        """P2-d: 任务完成后清理旧状态，为认领新任务做准备"""
        self.current_ml_action = None
        self.current_ml_action_steps = 0
        self._stuck_same_action_steps = 0
        self._last_exec_snapshot = None
        # 清空待执行动作队列
        if hasattr(self, 'action_wait_parse'):
            while not self.action_wait_parse.empty():
                self.action_wait_parse.get()
        # 重新加载 prompt（新任务可能需要新通讯伙伴）
        self.planner.dialog_history_list = []
        print(f"[TaskReset] A{self.agent_index}({self.role}) 状态已重置，准备认领新任务")

    def _get_my_task_order(self) -> Optional[str]:
        """获取当前认领任务的 order 名称"""
        if self.task_pool is None:
            return None
        my_task = self.task_pool.get_agent_current_task(self.agent_index)
        if my_task is not None:
            return my_task["order"]
        return None

    def set_agent_index(self, agent_index):
        self.agent_index = agent_index
        if hasattr(self, "_a2a_protocol"):
            self._a2a_protocol.agent_index = agent_index
        self.planner = self.create_gptmodule(
            "planner", retrival_method=self.retrival_method, K=self.K
        )
        self.planner.name = self.name
        # self.explainer = self.create_gptmodule("explainer", retrival_method='recent_k', K=self.K)

        # print(self.planner.instruction_head_list[0]['content'])

    def generate_grid_layout_prompt(self, state):
        return self.mdp.state_string(state).replace("ø", "o")

    def generate_layout_prompt(self):
        """生成布局提示（支持多Agent）"""
        access = getattr(self.mdp, '_agent_utensil_access', {})

        # 自己的工具
        my_utensils = access.get(self.agent_index, self.mdp.utensil_list)
        layout_prompt = f"Your workspace (A{self.agent_index}, {self.name}): "
        for u in my_utensils:
            layout_prompt += u + "  "
        layout_prompt += "counter"
        # 如果是 Assistant，添加 dish_dispenser 和 ingredient_dispenser
        if self.name == "Assistant":
            layout_prompt += "  dish_dispenser  ingredient_dispenser"
        layout_prompt += "\n"

        # 所有队友的工具
        all_teammates = getattr(self, 'teammates', [])
        if not all_teammates and getattr(self, 'teammate', None):
            all_teammates = [self.teammate]

        for tm in all_teammates:
            tm_idx = getattr(tm, 'agent_index', None)
            tm_name = getattr(tm, 'name', 'Unknown')
            tm_utensils = access.get(tm_idx, [])
            layout_prompt += f"A{tm_idx}({tm_name}) workspace: "
            for u in tm_utensils:
                layout_prompt += u + "  "
            if tm_name == "Assistant":
                layout_prompt += "dish_dispenser  ingredient_dispenser"
            layout_prompt += "\n"

        return layout_prompt

    def generate_state_prompt(self, state):
        # Ensure all agents have populated utensil accessibility lists
        self.build_access_utensil(state)
        all_teammates = getattr(self, 'teammates', [])
        if not all_teammates and getattr(self, 'teammate', None):
            all_teammates = [self.teammate]
        for tm in all_teammates:
            if hasattr(tm, 'build_access_utensil'):
                tm.build_access_utensil(state)
        self._ensure_conversation_timestamp(state.timestep)
        self.layout_prompt = self.generate_layout_prompt()
        self.current_timestep = state.timestep
        ego = state.players[self.agent_index]

        time_prompt = f"Scene {state.timestep}: "
        ego_object = ego.held_object.name if ego.held_object else "nothing"
        ego_state_prompt = f"<A{self.agent_index}({self.name})> holds "

        # 检查手持物是否是可交付的成品
        recipe_keys = list(self.recipe.keys()) if self.recipe else []
        if ego_object in recipe_keys:
            ego_state_prompt += f"a dish with {ego_object} and needs to deliver soup.  "
        elif ego_object == "nothing":
            ego_state_prompt += f"{ego_object}. "
        else:
            ego_state_prompt += f"one {ego_object}. "

        current_action_text = self.current_ml_action if self.current_ml_action else "[EMPTY]"
        action_state_prompt = (
            f"The current action being executed by A{self.agent_index}({self.name}) is [{current_action_text}] "
        )

        # --- 多Agent队友状态（替代原来的单队友） ---
        teammates_state_prompt = ""
        teammates_action_prompt = ""
        for tm in all_teammates:
            tm_idx = getattr(tm, 'agent_index', None)
            if tm_idx is None or tm_idx >= len(state.players):
                continue
            tm_player = state.players[tm_idx]
            tm_name = getattr(tm, 'name', 'Unknown')
            tm_object = tm_player.held_object.name if tm_player.held_object else "nothing"

            tm_line = f"<A{tm_idx}({tm_name})> holds "
            if tm_object == "soup":
                tm_line += f"a dish with {tm_object}. "
            elif tm_object == "nothing":
                tm_line += f"{tm_object}. "
            else:
                tm_line += f"one {tm_object}. "
            teammates_state_prompt += tm_line

            tm_action = getattr(tm, 'current_ml_action', None) or "[EMPTY]"
            teammates_action_prompt += (
                f"The current action being executed by A{tm_idx}({tm_name}) is [{tm_action}] "
            )

        # 兼容旧变量名（供后续代码使用）
        teammate_state_prompt = teammates_state_prompt
        teammate_action_state_prompt = teammates_action_prompt

        kitchen_state_prompt = "Kitchen states: "
        prompt_dict = {
            "empty": "<{utensil_name}> is empty; ",
            "cooking": "<{utensil_name}> starts processing {food},it will be ready after {t} timesteps; ",
            "ready": "<{utensil_name}> has {food}; ",
            "full": "<{utensil_name}> has {food}, which are all the ingredients you need to make {transition}; ",
            "partially_full": "<{utensil_name}> has {food}, which is part of {transition}; ",
            "wrong": "<{utensil_name}> has {food},which seems do not belong to any recipe;",
        }
        utensil_states_dict = self.mdp.get_utensil_states(state)

        for key in utensil_states_dict.keys():
            for utensil in utensil_states_dict[key]:
                if key == "empty":
                    kitchen_state_prompt += prompt_dict[key].format(
                        utensil_name=utensil
                    )
                elif key == "cooking":
                    order = self.mdp.utensil_state_dict[utensil]["order"]
                    utensil_type = utensil[:-1]
                    # The cook time for all kinds ingredient of one certain order in an utensil should be the same
                    middle_ingredient_dict = self.mdp.recipe_config["recipes"][
                        utensil_type
                    ]
                    middle_ingredient_dict_list = list(middle_ingredient_dict.keys())
                    ingredient_in_utensil_first = self.mdp.utensil_state_dict[utensil][
                        "soup"
                    ].state[0]
                    middle_ingredient = ""
                    for m in middle_ingredient_dict_list:
                        if ingredient_in_utensil_first in m:
                            middle_ingredient = m
                    if middle_ingredient == "":
                        raise ValueError(
                            f"No operation time for {ingredient_in_utensil_first} in {utensil_type}"
                        )
                    recipe_cook_time = middle_ingredient_dict[middle_ingredient][
                        "cook_time"
                    ]
                    now_cook_time = self.mdp.utensil_state_dict[utensil]["soup"].state[
                        2
                    ]
                    food_desc = self._format_food_description(
                        self.mdp.utensil_state_dict[utensil]["soup"].state[0]
                    )

                    kitchen_state_prompt += prompt_dict[key].format(
                        utensil_name=utensil,
                        food=food_desc,
                        t=recipe_cook_time - now_cook_time,
                    )
                elif key == "ready":
                    kitchen_state_prompt += prompt_dict[key].format(
                        utensil_name=utensil,
                        food=self._format_food_description(
                            self.mdp.utensil_state_dict[utensil]["soup"].state[0]
                        ),
                    )
                elif key == "full":
                    for rpe_name, rpe in self.mdp.recipes[utensil[:-1]].items():
                        if set(
                            self.mdp.utensil_state_dict[utensil]["soup"].state[0]
                        ) == set(rpe["recipe"]):
                            transition = rpe_name
                    kitchen_state_prompt += prompt_dict[key].format(
                        utensil_name=utensil,
                        food=self._format_food_description(
                            self.mdp.utensil_state_dict[utensil]["soup"].state[0]
                        ),
                        transition=transition,
                    )
                elif key == "partially_full":
                    for rpe_name, rpe in self.mdp.recipes[utensil[:-1]].items():
                        if (
                            len(
                                set(
                                    self.mdp.utensil_state_dict[utensil]["soup"].state[
                                        0
                                    ]
                                ).intersection(set(rpe["recipe"]))
                            )
                            > 0
                        ):
                            transition = rpe_name
                    kitchen_state_prompt += prompt_dict[key].format(
                        utensil_name=utensil,
                        food=self._format_food_description(
                            self.mdp.utensil_state_dict[utensil]["soup"].state[0]
                        ),
                        transition=transition,
                    )
                elif key == "wrong":
                    kitchen_state_prompt += prompt_dict[key].format(
                        utensil_name=utensil,
                        food=self._format_food_description(
                            self.mdp.utensil_state_dict[utensil]["soup"].state[0]
                        ),
                    )
        # 多Agent: 使用自己的 pos_and_or 计算可达counter
        # 选一个任意队友来做 get_intersect_counter（该函数只关心两个位置之间的可达性）
        _fallback_other_idx = 0 if self.agent_index != 0 else (1 if len(state.players) > 1 else 0)
        intersect_counters = get_intersect_counter(
            state.players_pos_and_or[self.agent_index],
            state.players_pos_and_or[_fallback_other_idx],
            self.mdp,
            self.mlam,
        )
        counter_states = query_counter_states(self.mdp, state)

        kitchen_state_prompt += (
            "{} counters can be visited by <A{}({})>. Their states are as follows: ".format(
                len(intersect_counters), self.agent_index, self.name
            )
        )
        count_states = {}
        for i in intersect_counters:
            obj_i = "nothing"
            if counter_states[i] != " ":
                obj_i = counter_states[i]
            if obj_i in count_states:
                count_states[obj_i] += 1
            else:
                count_states[obj_i] = 1
        total_obj = self.mdp.default_ingredients
        for i in count_states:
            if i == "nothing":
                continue
            kitchen_state_prompt += (
                f"{count_states[i]} counters have {i} which you can pick it up. "
            )
        if len(list(count_states.keys())) == 1:
            kitchen_state_prompt += "counters have nothing."
        # for i in total_obj:
        # 	if i not in count_states:
        # 		kitchen_state_prompt += f'No counters have {i}. '
        kitchen_state_prompt += "\n"

        wong_message_prompt = (
            state.error_message[0] if len(state.error_message) > 0 else ""
        )
        # add failed history into state prompt (支持多Agent):
        _all_tm_actions = []
        for tm in all_teammates:
            tm_actions = getattr(tm, 'teammate_ml_actions', [])
            _all_tm_actions.extend(tm_actions[-2:] if len(tm_actions) > 2 else tm_actions)
        long_term_memory_prompt = f"Successful Action History: {_all_tm_actions[-5:] if len(_all_tm_actions) > 5 else _all_tm_actions}\n"
        reflection_memory = "Lessons from Past Failures\n"
        count = len(reflection_memory)
        temp_history = (
            self.failed_history
            if len(reflection_memory) < 3
            else self.failed_history[-3:]
        )
        if len(self.failed_history) != 0:
            for index, value in enumerate(temp_history):
                reflection = ""
                if value["reflection_content"][-1] == "\n":
                    reflection = value["reflection_content"][:-1]
                else:
                    reflection = f'{index+1}.{value["reflection_content"]}'
                reflection_memory += reflection + "\n"
        if count == len(reflection_memory):
            reflection_memory += "[]\n"
        self.planner.layout = self.layout_prompt
        self.planner.wong_message_prompt = wong_message_prompt
        self.planner.long_term_memory_prompt = reflection_memory

        order_text = (self.order or "").strip()
        if order_text.lower().startswith("order:"):
            order_text = order_text.split(":", 1)[1].strip()
        if not self.mdp.one_task_mode and len(state.current_k_order) > 1:
            tail_orders = [
                o.strip()
                for o in state.current_k_order[1:]
                if isinstance(o, str) and o.strip()
            ]
            if tail_orders:
                order_text += "".join(f"<<{o}" for o in tail_orders)

        # --- TaskPool 状态注入 Observation ---
        task_pool_prompt = ""
        if self.task_pool is not None:
            my_task = self.task_pool.get_agent_current_task(self.agent_index)
            if my_task:
                partner_indices = self.task_pool.get_task_teammates(self.agent_index)
                partner_str = ", ".join(f"A{p}" for p in partner_indices) if partner_indices else "none"
                task_pool_prompt = (
                    f"[Task] You are working on Task {my_task['id']}: {my_task['order']} "
                    f"(status: {my_task['status']}, partners: {partner_str})\n"
                )
            else:
                avail = self.task_pool.get_available_tasks(role=self.role)
                if avail:
                    avail_str = ", ".join(f"Task {t['id']}({t['order']})" for t in avail)
                    task_pool_prompt = f"[Task] You have no task. Available tasks: {avail_str}\n"
                else:
                    task_pool_prompt = "[Task] No tasks available. Wait for tasks to be published.\n"
            # 洗碗状态（对 Dishwasher 特别重要）
            if self.role.lower() == "dishwasher":
                pending_wash = self.task_pool.get_pending_wash_jobs()
                clean_dishes = getattr(self.mdp, 'clean_dishes_available', 0)
                max_dishes = getattr(self.mdp, 'max_clean_dishes', 0)
                task_pool_prompt += (
                    f"[Wash] Pending wash jobs: {len(pending_wash)}, "
                    f"Clean dishes: {clean_dishes}/{max_dishes}\n"
                )

        # --- Dish requirement rule (recipe-driven, no second-guessing) ---
        # User rule: If the recipe text does NOT mention dish/plate/fill_dish_with_food,
        # then treat it as NOT requiring a plate. Do not overthink.
        dish_rule_prompt = ""
        try:
            cur_orders = []
            if hasattr(state, "current_k_order") and state.current_k_order:
                cur_orders = [o for o in state.current_k_order if isinstance(o, str) and o.strip()]
            if self.task_pool is not None:
                for t in getattr(self.task_pool, "tasks", []) or []:
                    if t.get("status") != "completed":
                        o = t.get("order") or t.get("name")
                        if isinstance(o, str) and o.strip():
                            cur_orders.append(o.strip())
            seen = set()
            cur_orders = [o for o in cur_orders if not (o in seen or seen.add(o))]

            def _recipe_text_for(order_name: str) -> str:
                # Prefer loaded recipe cache; otherwise load from file (same matching rule as load_recipe)
                if order_name in getattr(self, "recipe", {}) and self.recipe.get(order_name):
                    return str(self.recipe.get(order_name) or "")
                try:
                    recipe_name_list = os.listdir(PROMPT_DIR + "/recipe/")
                    for r in recipe_name_list:
                        r_name = r[2:-4]
                        if order_name == r_name:
                            with open(PROMPT_DIR + "/recipe/" + r, "r", encoding="utf-8") as fh:
                                return fh.read()
                except Exception:
                    return ""
                return ""

            def _recipe_mentions_dish(txt: str) -> bool:
                s = (txt or "").lower()
                return ("dish" in s) or ("plate" in s) or ("fill_dish_with_food" in s)

            if cur_orders:
                parts = []
                for o in cur_orders:
                    recipe_txt = _recipe_text_for(o)
                    need_plate = _recipe_mentions_dish(recipe_txt)
                    if need_plate:
                        parts.append(
                            f"{o}: recipe_mentions_dish=YES → MUST plate (pickup(dish, dish_dispenser/counter) then fill_dish_with_food(...), then deliver_soup())"
                        )
                    else:
                        parts.append(
                            f"{o}: recipe_mentions_dish=NO → NO plate (pickup(final_food, utensil) then deliver_soup())"
                        )
                dish_rule_prompt = (
                    "[DishRule] Use ONLY recipe text. If recipe does NOT mention dish/plate/fill_dish_with_food, "
                    "then do NOT use a dish. Do NOT second-guess. "
                    + " | ".join(parts)
                    + "\n"
                )
        except Exception:
            dish_rule_prompt = ""

        # P2-b: 工具冲突提示
        conflict_prompt = self._build_conflict_prompt() if hasattr(self, '_build_conflict_prompt') else ""

        layout_section = self.layout_prompt.strip()
        kitchen_section = kitchen_state_prompt.strip()
        scene_components = [f"Scene {state.timestep}:"]
        if task_pool_prompt:
            scene_components.append(task_pool_prompt.strip())
        if dish_rule_prompt:
            scene_components.append(dish_rule_prompt.strip())
        if conflict_prompt:
            scene_components.append(conflict_prompt.strip())
        if layout_section:
            scene_components.append(layout_section)
        if kitchen_section:
            scene_components.append(kitchen_section)
        scene_text = "\n".join(scene_components)
        agent_state_text = (
            ego_state_prompt
            + action_state_prompt
            + teammate_state_prompt
            + teammate_action_state_prompt
        ).strip()
        conversation_text = self.format_conversation_history()
        self.current_observation_snapshot = {
            "timestamp": self.current_timestep,
            "order": order_text,
            "scene": scene_text,
            "agent_state": agent_state_text,
            "past_conversation": conversation_text,
        }
        current_block = self.format_current_observation_block(
            self.current_timestep,
            order_text,
            scene_text,
            agent_state_text,
            conversation_text,
            self.current_recent_goal_text,
        )
        history_prompt = self.build_history_prompt()
        sections = []
        if history_prompt:
            sections.append("History (latest decisions):\n" + history_prompt + "\n")
        sections.append("Current Observation:\n" + current_block)
        return "".join(sections)

    def build_access_utensil(self, state):
        """计算每个 Agent 可达的工具列表（支持多Agent）"""
        am = self.mlam
        num_players = len(state.players)

        # 初始化 per-agent 工具可达字典（仅首次）
        if not hasattr(self.mdp, '_agent_utensil_access'):
            self.mdp._agent_utensil_access = {}

        if len(self.mdp._agent_utensil_access) >= num_players:
            # 已经计算过了
            # 兼容旧字段
            if self.mdp.utensil_list_chef == [] or self.mdp.utensil_list_assist == []:
                self._fill_legacy_utensil_lists()
            return

        for i in range(num_players):
            if i in self.mdp._agent_utensil_access:
                continue
            player = state.players[i]
            accessible = []
            for utensil in self.mdp.utensil_list:
                motion_goals = am.ml_action_manager.go_to_utensil_actions(state, utensil, i)
                motion_goals = [
                    mg
                    for mg in motion_goals
                    if self.mlam.mp.is_valid_motion_start_goal_pair(
                        player.pos_and_or, mg
                    )
                ]
                if len(motion_goals) > 0:
                    accessible.append(utensil)
            self.mdp._agent_utensil_access[i] = accessible

        # 兼容旧字段 (utensil_list_chef / utensil_list_assist)
        self._fill_legacy_utensil_lists()

    def _fill_legacy_utensil_lists(self):
        """用 per-agent 工具表回填旧的 chef/assist 列表"""
        if not hasattr(self.mdp, '_agent_utensil_access'):
            return
        access = self.mdp._agent_utensil_access
        if self.mdp.utensil_list_chef == []:
            # 第一个 chef 的工具列表
            for idx, utensils in access.items():
                agent_obj = self._find_agent_by_index(idx)
                if agent_obj and getattr(agent_obj, 'role', '').lower() == 'chef':
                    self.mdp.utensil_list_chef = list(utensils)
                    break
            if self.mdp.utensil_list_chef == [] and 0 in access:
                self.mdp.utensil_list_chef = list(access[0])
        if self.mdp.utensil_list_assist == []:
            for idx, utensils in access.items():
                agent_obj = self._find_agent_by_index(idx)
                if agent_obj and getattr(agent_obj, 'role', '').lower() == 'assistant':
                    self.mdp.utensil_list_assist = list(utensils)
                    break
            if self.mdp.utensil_list_assist == [] and 1 in access:
                self.mdp.utensil_list_assist = list(access[1])

    def _find_agent_by_index(self, idx):
        """在 teammates 中查找指定 index 的 Agent"""
        if self.agent_index == idx:
            return self
        for tm in getattr(self, 'teammates', []):
            if getattr(tm, 'agent_index', None) == idx:
                return tm
        return None

    def get_my_utensils(self):
        """获取当前 Agent 可访问的工具列表"""
        if hasattr(self.mdp, '_agent_utensil_access') and self.agent_index in self.mdp._agent_utensil_access:
            return self.mdp._agent_utensil_access[self.agent_index]
        return self.mdp.utensil_list

    # ------------------------------------------------------------------
    # P2-b: 工具冲突检测
    # ------------------------------------------------------------------

    def _extract_target_utensil(self, action_str=None):
        """从动作字符串中提取目标工具名称

        Args:
            action_str: 动作字符串，默认使用 self.current_ml_action
        Returns:
            工具名称字符串（如 'pot0'）或 None
        """
        if action_str is None:
            action_str = self.current_ml_action
        if not action_str:
            return None
        action, params = self.parse_params_in_action(action_str)
        # 直接操作工具的动作（cook/cut/bake/stir/wash）
        utensil_actions = set(self.mdp.interact_actions) if hasattr(self.mdp, 'interact_actions') else set()
        utensil_actions.update({"cook", "cut", "bake", "stir", "wash"})
        if action in utensil_actions and params:
            return params[0]
        # put_obj_in_utensil(utensil_name)
        if action == "put_obj_in_utensil" and params:
            return params[0]
        # fill_dish_with_food(utensil_name)
        if action == "fill_dish_with_food" and params:
            return params[0]
        # pickup(food, utensil_name) — 第二个参数如果是 utensil
        if action == "pickup" and len(params) >= 2:
            if params[1] in (getattr(self.mdp, 'utensil_list', []) or []):
                return params[1]
        return None

    def _detect_and_store_conflicts(self, state):
        """收集所有 Agent 的目标工具，检测冲突并存储结果

        检测结果存入 self._utensil_conflict_info:
            None   — 无冲突
            list   — [(utensil, [agent_indices], priority_agent_idx), ...]
        """
        self._utensil_conflict_info = None  # reset
        if self.task_pool is None:
            return

        # 收集所有 Agent（包括自己）的目标工具
        all_agents = [self] + list(getattr(self, 'teammates', []))
        agent_plans = {}
        for ag in all_agents:
            ag_idx = getattr(ag, 'agent_index', None)
            if ag_idx is None:
                continue
            target = ag._extract_target_utensil() if hasattr(ag, '_extract_target_utensil') else None
            if target:
                agent_plans[ag_idx] = target

        if not agent_plans:
            return

        raw_conflicts = self.task_pool.detect_utensil_conflicts(agent_plans)
        if not raw_conflicts:
            return

        # 附加优先级信息：index 最小者优先
        enriched = []
        for utensil, agents in raw_conflicts:
            priority = min(agents)
            enriched.append((utensil, agents, priority))
        self._utensil_conflict_info = enriched

    def _build_conflict_prompt(self):
        """根据 _utensil_conflict_info 生成冲突提示文本，注入到 observation 中"""
        if not getattr(self, '_utensil_conflict_info', None):
            return ""
        lines = ["[Utensil Conflict Warning]"]
        my_idx = self.agent_index
        for utensil, agents, priority in self._utensil_conflict_info:
            if my_idx not in agents:
                continue  # 只提示与自己相关的冲突
            other_agents = [a for a in agents if a != my_idx]
            others_str = ", ".join(f"A{a}" for a in other_agents)
            if my_idx == priority:
                lines.append(
                    f"  ⚡ You (A{my_idx}) and {others_str} are both targeting <{utensil}>. "
                    f"You have PRIORITY (lowest index). Proceed with your action."
                )
            else:
                lines.append(
                    f"  ⚠ You (A{my_idx}) and {others_str} are both targeting <{utensil}>. "
                    f"A{priority} has priority. You should WAIT or choose an alternative utensil/action."
                )
        return "\n".join(lines) + "\n" if len(lines) > 1 else ""

    ##################
    """
	The followings are the Planner part
	"""
    ##################

    def action(self, state):
        self.state = state
        self.build_access_utensil(state)
        turn_statistics_dict_cp = copy.deepcopy(turn_statistics_dict)
        # Expand lists to support > 2 agents
        num_players = len(state.players)
        self._expand_statistics_dict(turn_statistics_dict_cp, num_players)
        self.turn_statistics_dict = turn_statistics_dict_cp
        start_pos_and_or = state.players_pos_and_or[self.agent_index]

        # only use to record the teammate ml_action,
        # if teammate finish ml_action in t-1, it will record in s_t,
        # otherwise, s_t will just record None,
        # and we here check this information and store it
        self.current_timestep = state.timestep
        self.planner.current_timestep = state.timestep
        self._reset_comm_turn_counter()
        self._collab_ack_consumed = False

        # ----- P1: 自动认领任务 -----
        # 全局调度模式下，任务分配由 GlobalScheduler 统一执行，避免与本地顺序认领冲突
        if not getattr(self, "use_global_scheduler", False):
            self._try_claim_task()

        # ----- P2-b: 工具冲突检测 -----
        self._detect_and_store_conflicts(state)

        # ----- A2A: 处理收到的协议消息（上一 timestep 路由来的） -----
        _a2a_driven_action = self._process_incoming_a2a(self.current_timestep)

        # Update order: 只使用自己认领任务的 order。无任务时置空，避免插手其他任务。
        task_order = self._get_my_task_order()
        effective_order = task_order if task_order else ""
        self.order = effective_order
        # 同步队友自己的任务 order（不要覆盖成当前 agent 的 order）
        if hasattr(self, 'teammates'):
            for teammate in self.teammates:
                teammate_task_order = teammate._get_my_task_order() if hasattr(teammate, "_get_my_task_order") else None
                teammate.order = teammate_task_order if teammate_task_order else ""
        elif self.teammate:
            teammate_task_order = self.teammate._get_my_task_order() if hasattr(self.teammate, "_get_my_task_order") else None
            self.teammate.order = teammate_task_order if teammate_task_order else ""

        # 多任务模式下：Chef/Assistant 无任务时必须空闲等待，不能插手他人任务
        # 但若 A2A 协议给了指令（来自队友的 REQUEST），优先执行该指令
        if self.task_pool is not None and self.role.lower() in ("chef", "assistant"):
            my_task = self.task_pool.get_agent_current_task(self.agent_index)
            if my_task is None:
                if _a2a_driven_action:
                    # A2A 驱动：接受了队友的 REQUEST，执行对应动作
                    self.current_ml_action = _a2a_driven_action
                    self.current_ml_action_steps = 0
                    _a2a_driven_action = None  # 已使用，清空
                else:
                    self.current_ml_action = "wait(1)"
                    self.current_ml_action_steps = 0
                    self.time_to_wait = 1
                    self.pending_collab_reply = False
        self.change_communication_role("ask", "answer")
        self.planner.dialog_history_list = []
        # Clear dialogue history for all teammates (supporting multiple agents)
        if hasattr(self, 'teammates'):
            for teammate in self.teammates:
                teammate.planner.dialog_history_list = []
        elif self.teammate:
            self.teammate.planner.dialog_history_list = []
        # check if teammates have finished their actions (supporting multiple agents)
        if hasattr(self, 'teammates'):
            for teammate in self.teammates:
                if teammate.current_ml_action_steps > 0 and teammate.current_ml_action is not None:
                    current_ml_action_done = teammate.check_current_ml_action_done(state)
                    if current_ml_action_done:
                        teammate.current_ml_action = None
        elif self.teammate:
            if self.teammate.current_ml_action_steps > 0 and self.teammate.current_ml_action is not None:
                current_ml_action_done = self.teammate.check_current_ml_action_done(state)
                if current_ml_action_done:
                    self.teammate.current_ml_action = None

        # Record teammate ml_actions for all teammates (supporting multiple agents)
        num_players = len(state.players)
        for other_idx in range(num_players):
            if other_idx != self.agent_index and state.ml_actions[other_idx] is not None:
                self.teammate_ml_actions.append(
                    {
                        "timestamp": self.current_timestep,
                        "action": state.ml_actions[other_idx],
                        "agent_index": other_idx,  # Record which agent performed the action
                    }
                )

        # if current ml action does not exist, generate a new one
        # A2A: 如果收到来自队友的 REQUEST 且有可执行指令，优先注入，无需 LLM 生成
        if self.current_ml_action is None:
            if _a2a_driven_action:
                self.current_ml_action = _a2a_driven_action
                _a2a_driven_action = None
            else:
                self.current_ml_action = self.generate_ml_action(state)

        # when "wait" and has other action in action_wait_parse ,replace wait as the action
        if "wait" in self.current_ml_action and not self.action_wait_parse.empty():
            self.current_ml_action = self.generate_ml_action(state)

        # if the current ml action is in process, Player{self.agent_index} done, else generate a new one
        if self.current_ml_action_steps > 0:
            current_ml_action_done = self.check_current_ml_action_done(state)
            if current_ml_action_done:
                # A2A: 若该动作是由 A2A REQUEST 驱动的，完成后向请求方发 INFORM
                if self._a2a_pending_instruction:
                    self._a2a_send_inform_completion(self.current_timestep)
                # generate a new ml action
                self.generate_success_feedback(state)
                self.current_ml_action = None
                self._stuck_same_action_steps = 0
                self._last_exec_snapshot = None
                self.current_ml_action = self.generate_ml_action(state)
            else:
                # Protective reset: if place action keeps repeating with same local state, force replanning.
                if self.current_ml_action and "place_obj_on_counter" in self.current_ml_action:
                    p = state.players[self.agent_index]
                    held = p.get_object().name if p.has_object() else ""
                    snapshot = (self.current_ml_action, p.position, p.orientation, held)
                    if self._last_exec_snapshot == snapshot:
                        self._stuck_same_action_steps += 1
                    else:
                        self._stuck_same_action_steps = 0
                    self._last_exec_snapshot = snapshot
                    if self._stuck_same_action_steps >= 4:
                        print(
                            f"[WARN A{self.agent_index}] place_obj_on_counter() no progress for "
                            f"{self._stuck_same_action_steps + 1} steps, force replanning."
                        )
                        self.current_ml_action = None
                        self.current_ml_action_steps = 0
                        self.time_to_wait = 1
                        self._stuck_same_action_steps = 0
                        self._last_exec_snapshot = None
        count = 0
        if self.current_ml_action_steps == 0:
            self.failed_message = self.validate_current_ml_action(state)
            self._ensure_penalty_reward_entry(self.current_ml_action)
            self._handle_validator_failure(self.failed_message)
            self._flush_reward_event()
            if "success" in self.failed_message and self.test_mode:
                self.test_ml_action.popleft()
            # only try to self-correct 1 time
            while "success" not in self.failed_message:
                if self.test_mode:
                    self.current_ml_action = "wait(1)"
                    self.time_to_wait = 1
                    print(self.failed_message)
                else:
                    # del all wait parse action
                    while not self.action_wait_parse.empty():
                        self.action_wait_parse.get()
                    if count >= 1:
                        self.current_ml_action = "wait(1)"
                        self.time_to_wait = 1
                        break
                    self.trace = False
                    self.generate_failure_feedback(
                        self.current_ml_action, self.failed_message
                    )
                    self.change_correct_prompt()
                    self.turn_statistics_dict["statistical_data"]["error"][
                        self.agent_index
                    ]["validator_error"]["error_num"] += 1
                    self.turn_statistics_dict["statistical_data"]["error"][
                        self.agent_index
                    ]["validator_error"]["error_message"].append(self.failed_message)
                    self.current_ml_action = self.generate_ml_action(state)
                    count += 1
                self.failed_message = self.validate_current_ml_action(state)
                self._ensure_penalty_reward_entry(self.current_ml_action)
                self._ensure_penalty_reward_entry(self.current_ml_action)
                self._handle_validator_failure(self.failed_message)
                self._flush_reward_event()
        # generate rethink if the problem is solved and not just 'wait(1)'
        if not self.trace and count < 1:
            self.turn_statistics_dict["statistical_data"]["error_correction"][
                self.agent_index
            ]["validator_correction"]["correction_num"] += 1
            if self.current_timestep not in self.error_correct.keys():
                self.generate_rethink(state)

        # print(self.current_ml_action_steps)
        # print(self.time_to_wait)
        # print(self.check_current_ml_action_done(state))
        # print(f"{self.name} current action:{self.current_ml_action}")
        # assert(self.current_ml_action_steps<5)
        # statistic
        self.turn_statistics_dict["content"]["reflection"][self.agent_index] = (
            copy.deepcopy(self.failed_history)
        )
        queue_snapshot = list(self.action_wait_parse.queue)
        action_list = self._gather_pending_actions(
            self.current_ml_action, queue_snapshot
        )
        self.turn_statistics_dict["content"]["action_list"][self.agent_index] = (
            copy.deepcopy(action_list)
        )
        self.turn_statistics_dict["timestamp"] = self.current_timestep
        self.turn_statistics_dict["order_list"] = state.current_k_order
        self.turn_statistics_dict["actions"].append(self.current_ml_action)
        # save statistic data
        # del history
        self.planner.dialog_history_list = []
        # 清理所有队友的对话历史（支持多Agent）
        for _tm in getattr(self, 'teammates', []):
            if hasattr(_tm, 'planner'):
                _tm.planner.dialog_history_list = []

        self.trace = True

        # if "deliver_soup()" in self.current_ml_action:
        # 	self.teammate.teammate_ml_actions.append({'timestamp':self.current_timestep,'action':"deliver_soup()"})

        if "wait" in self.current_ml_action or "recipe" in self.current_ml_action:
            self.current_ml_action_steps += 1
            self.time_to_wait -= 1
            lis_actions = self.mdp.get_valid_actions(state.players[self.agent_index])
            # chosen_action =lis_actions[np.random.randint(0,len(lis_actions))]
            chosen_action = (0, 0)
            # Use version 0.0.1 logic (from dependencies/overcooked_ai)
            self.prev_state = state
            return self._finalize_action_return(chosen_action, "")
        else:
            possible_motion_goals = self.find_motion_goals(state)

            # 保护机制：当前高层动作不可达时，重置让 LLM 重新规划，避免永久卡住
            if not possible_motion_goals:
                print(f"[WARN A{self.agent_index}] No motion goals for ml_action={self.current_ml_action}, resetting to wait(1)")
                self.current_ml_action = None
                self.current_ml_action_steps = 0
                self.time_to_wait = 1
                self.prev_state = state
                return self._finalize_action_return(Action.STAY, "")

            current_motion_goal, chosen_action = self.choose_motion_goal(
                start_pos_and_or, possible_motion_goals, state
            )
        # if "wait" in self.current_ml_action:
        # 	print(f'current motion goal for P{self.agent_index} is wait')
        # else:
        # 	if current_motion_goal is None:
        # 		current_motion_goal = 'None'
        # 	print(f'current motion goal for P{self.agent_index} is {current_motion_goal}')

        if self.auto_unstuck and chosen_action != Action.INTERACT:
            if self.prev_state is not None and state.players == self.prev_state.players:
                num_players = len(state.players)
                # 构建 joint_actions: 自己尝试所有动作，其余玩家 STAY
                action_slots = []
                for p_idx in range(num_players):
                    if p_idx == self.agent_index:
                        action_slots.append(Action.ALL_ACTIONS)
                else:
                        action_slots.append([Action.STAY])
                joint_actions = list(itertools.product(*action_slots))

                unblocking_joint_actions = []
                for j_a in joint_actions:
                    # 排除所有人都 INTERACT/STAY 的无效组合
                    if j_a[self.agent_index] != Action.INTERACT:
                        # Use version 0.0.1 logic (from dependencies/overcooked_ai)
                        new_state, _, _ = self.mlam.mdp.get_state_transition(
                            state, j_a
                        )
                        if (
                            new_state.players_pos_and_or
                            != self.prev_state.players_pos_and_or
                        ):
                            unblocking_joint_actions.append(j_a)
                stay_action = tuple([Action.STAY] * num_players)
                unblocking_joint_actions.append(stay_action)
                chosen_action = unblocking_joint_actions[
                    np.random.choice(len(unblocking_joint_actions))
                ][self.agent_index]

        self.prev_state = state
        if chosen_action is None:
            self.current_ml_action = "wait(1)"
            self.time_to_wait = 1
            chosen_action = Action.STAY

        if self.current_ml_action_steps == 0:
            self.current_ml_action_steps = 1

        # print(f'ml_action = {self.current_ml_action}')
        # print(f'P{self.agent_index} : {Action.to_char(chosen_action)}')
        # Use version 0.0.1 logic (from dependencies/overcooked_ai)
        primary_action, primary_params = self.parse_params_in_action(self.current_ml_action)
        if primary_action == "pickup":
            pickup_item = primary_params[0] if primary_params else ""
            return self._finalize_action_return(chosen_action, pickup_item)
        elif primary_action in self.mdp.interact_actions:
            return self._finalize_action_return(chosen_action, "[START]")
        else:
            return self._finalize_action_return(chosen_action, "")

    # Parse action as function(parms1,parms2)
    def parse_params_in_action(self, action: str, need_output=False):
        action = action.replace(" ", "")
        function_name = ""
        params = []
        pattern = (
            r"(?:\d+\.\s*)?(\w+)\s*(?:\((.*?)\))?"  # r"^\"?\d*\.?\"?\s*(\w+)\((.*)\)"
        )
        match = re.match(pattern, action)
        # function name and parmas with "()"
        if match:
            function_name = match.group(1)
            if match.group(2) == None:
                params = []
            else:
                params = match.group(2).split(",")
            for index, p in enumerate(params):
                params[index] = params[index].replace(" ", "")
                params[index] = params[index].replace("'", "")
                params[index] = params[index].replace('"', "")
            if len(params) > 1 and ("put_obj_in_utensil" in function_name):
                params = [params[1]]
        elif "NOTHING" in action or "nothing" in action:
            function_name = "wait"
            params = ["1"]
        # function name and parmas without "()"
        else:
            pattern = r"^\"?\d*\.?\"?\s*(\w+)\((.*)\)"
            if need_output:
                print(f"No match function like action in {action}")
            return function_name, params
        if need_output:
            print(f"function_name : {function_name},params_name:{params}")
        return function_name, params

    def load_recipe(self):
        # In multi-task mode an idle agent may have no assigned order.
        # Do not try to read recipe files when order is empty.
        if not self.order:
            return
        if self.order in self.recipe.keys():
            warnings.warn("Prompt has load the recipe")
            return
        recipe_name_list = os.listdir(PROMPT_DIR + "/recipe/")
        recipe_filename = ""
        for r in recipe_name_list:
            r_name = r[2:-4]
            if self.order == r_name:
                recipe_filename = r
                break
        if recipe_filename == "":
            warnings.warn(f"Recipe file not found for order '{self.order}', skip load_recipe.")
            return
        with open(PROMPT_DIR + "/recipe/" + recipe_filename) as r:
            self.recipe[self.order] = r.read()

    def _collab_requires_reply(self, text: str) -> bool:
        lowered = (text or "").lower()
        return ("collab(request" in lowered) or ("collab(seek" in lowered)

    def _strip_action_prefix(self, action_text: Optional[str]) -> str:
        if not isinstance(action_text, str):
            return ""
        stripped = action_text.strip()
        if stripped.lower().startswith("action:"):
            return stripped.split(":", 1)[1].strip()
        return stripped

    def _is_collab_action(self, action_text: Optional[str]) -> bool:
        normalized = self._strip_action_prefix(action_text)
        if not normalized:
            return False
        lowered = normalized.lower()
        if lowered.startswith("collab("):
            return True
        collab_primitives = ("request(", "seek(", "ack(", "deny(")
        return any(lowered.startswith(prefix) for prefix in collab_primitives)

    # ──────────────────────────────────────────────────────────────
    # A2A Protocol: 主动消息处理与指令执行
    # ──────────────────────────────────────────────────────────────

    def _collab_action_to_instruction(self, action_str: str) -> Optional[str]:
        """
        将 Collab 请求的 action 字符串映射到 InstructionRegistry key（用于协议分类和日志）。

        设计：
        - pickup(*任意食材*, ingredient_dispenser) → pickup_ingredient（通用，不绑定菜名）
        - place_obj_on_counter() → place_on_counter
        - put_obj_in_utensil(*)  → put_in_utensil
        - cook/cut/bake/stir     → operate_utensil
        - fill_dish_with_food(*) → fill_dish
        - deliver_soup()         → deliver_food
        - wash(*)                → wash_dishes
        - get_dish(*)            → get_dish
        - go_to(counter*)        → go_to_counter
        - go_to(serving*)        → go_to_serving
        """
        if not action_str:
            return None
        import re
        s = action_str.lower().strip()
        # 提取 request(Ax, VERB(...)) 中的 VERB
        m = re.search(r'request\s*\([^,]+,\s*([a-z_]+)\s*\(', s)
        verb = m.group(1).strip() if m else s.split("(")[0].strip()

        _MAP = {
            # ── 取食材（所有菜名归一） ──────────────────────────────
            "pickup":               "pickup_ingredient",
            # ── 放柜台 ──────────────────────────────────────────────
            "place_obj_on_counter": "place_on_counter",
            # ── 放入设备 ─────────────────────────────────────────────
            "put_obj_in_utensil":   "put_in_utensil",
            # ── 烹饪操作 ─────────────────────────────────────────────
            "cook":                 "operate_utensil",
            "cut":                  "operate_utensil",
            "bake":                 "operate_utensil",
            "stir":                 "operate_utensil",
            # ── 盛盘 ─────────────────────────────────────────────────
            "fill_dish_with_food":  "fill_dish",
            # ── 交付 ─────────────────────────────────────────────────
            "deliver_soup":         "deliver_food",
            # ── 洗碗 ─────────────────────────────────────────────────
            "wash":                 "wash_dishes",
            # ── 取盘子 ───────────────────────────────────────────────
            "get_dish":             "get_dish",
            # ── 导航 ─────────────────────────────────────────────────
            "go_to":                "go_to_counter",   # 默认；serving 会在下面细化
        }
        result = _MAP.get(verb)
        # 细化 go_to：判断目的地
        if verb == "go_to":
            if "serving" in s or "deliver" in s:
                result = "go_to_serving"
            else:
                result = "go_to_counter"
        # pickup 中若来源是 dish_dispenser → 归为 get_dish
        if verb == "pickup" and "dish_dispenser" in s:
            result = "get_dish"
        return result

    # find_motion_goals 能识别的合法动词集合（同步于 find_motion_goals 的分支）
    _VALID_ML_ACTION_VERBS = frozenset({
        "pickup", "put_obj_in_utensil", "place_obj_on_counter",
        "fill_dish_with_food", "deliver_soup", "cook", "cut",
        "stir", "bake", "wait", "wash", "get_dish", "add_toast",
        "go_to",  # 抽象导航格式，由 find_motion_goals 的 go_to 分支处理
    })

    def _extract_ml_action_from_collab(self, raw_collab: str) -> Optional[str]:
        """
        从 Collab 消息文本中提取可直接使用的 ml_action 字符串。

        支持三种格式：
        1. Collab(request(A1, pickup(carrot, ingredient_dispenser)))
           → pickup(carrot, ingredient_dispenser)          [直接合法 ml_action]
        2. Collab(request(A1, place_obj_on_counter()))
           → place_obj_on_counter()
        3. go_to(counter(3,1)) / go_to(pot0)               [抽象导航格式，go_to 分支处理]

        适用于所有 30+ 个 reference 菜品，无需硬编码菜名。
        """
        import re
        if not raw_collab:
            return None
        s = raw_collab.strip()

        # ── 优先尝试从 request(Ax, ACTION) 结构提取内层动作 ──────────────
        # 匹配 request(任意, 动词(...))，支持嵌套括号（如 go_to(counter(3,1))）
        # 先找到 request(..., 的位置，然后使用括号计数提取完整动作
        request_match = re.search(r'request\s*\([^,]+,\s*', s, re.IGNORECASE)
        if request_match:
            start_pos = request_match.end()
            # 从 start_pos 开始，找到第一个动词
            verb_match = re.search(r'([a-z_]+)\s*\(', s[start_pos:], re.IGNORECASE)
            if verb_match:
                verb_start = start_pos + verb_match.start()
                verb_name = verb_match.group(1).strip().lower()
                if verb_name in self._VALID_ML_ACTION_VERBS:
                    # 找到动词后的第一个 '('，然后使用括号计数找到匹配的 ')'
                    paren_start = start_pos + verb_match.end() - 1  # '(' 的位置
                    paren_count = 0
                    i = paren_start
                    while i < len(s):
                        if s[i] == '(':
                            paren_count += 1
                        elif s[i] == ')':
                            paren_count -= 1
                            if paren_count == 0:
                                # 找到匹配的 ')'
                                candidate = s[verb_start:i+1].strip()
                                return candidate
                        i += 1
                    # 如果没找到匹配的 ')'，尝试简单提取
                    simple_match = re.search(r'request\s*\([^,]+,\s*([a-z_]+\s*\([^)]*\))', s, re.IGNORECASE)
                    if simple_match:
                        candidate = simple_match.group(1).strip()
                        if candidate.split("(")[0].strip().lower() in self._VALID_ML_ACTION_VERBS:
                            return candidate

        # ── 若整体就是一个合法 ml_action（去掉 Collab(...) 包装）──────────
        # 去掉 Collab( ... ) 外壳后再尝试
        m2 = re.match(r'collab\s*\(\s*(.*)\s*\)\s*$', s, re.IGNORECASE | re.DOTALL)
        inner = m2.group(1).strip() if m2 else s
        verb2 = inner.split("(")[0].strip().lower()
        if verb2 in self._VALID_ML_ACTION_VERBS:
            return inner

        # ── 语义回退：保留 go_to 格式（由 find_motion_goals 处理）─────────
        if s.lower().startswith("go_to("):
            return s

        # ── 最终回退：用指令 key → 标准 ml_action 表 ─────────────────────
        _FALLBACK: Dict[str, str] = {
            "place_on_counter":  "place_obj_on_counter()",
            "pickup_ingredient": "pickup(ingredient, ingredient_dispenser)",
            "get_dish":          "get_dish(dish_dispenser)",
            "deliver_food":      "deliver_soup()",
            "wash_dishes":       "wash(water0)",
        }
        key = self._collab_action_to_instruction(raw_collab)
        if key and key in _FALLBACK:
            return _FALLBACK[key]

        return None

    def _process_incoming_a2a(self, timestep: int) -> Optional[str]:
        """
        处理 incoming A2A 消息队列（每次 action() 开始时调用）。
        - REQUEST 且能解析出合法 ml_action → 自动 ACCEPT + 返回该 ml_action
        - REQUEST 但无法解析              → REJECT
        - ACCEPT / INFORM / REJECT        → 打印协议事件，更新状态
        返回 None 表示无 A2A 驱动的动作。
        """
        try:
            from collab_overcooked.a2a_protocol.message import (
                MessageType, create_accept_message,
                create_reject_message,
            )
            from collab_overcooked.a2a_protocol.protocol import ProtocolState

            pending = self._a2a_protocol.process_incoming()
            for msg in pending:
                if msg.type == MessageType.REQUEST:
                    raw_action = msg.content.get("action", "")
                    # 动态解析：适用所有菜品，无需硬编码
                    ml_action = self._extract_ml_action_from_collab(raw_action)
                    # 同时用旧 key 做记录（方便日志和 INFORM 追踪）
                    key = self._collab_action_to_instruction(raw_action) or raw_action[:30]
                    if ml_action:
                        # 发送 ACCEPT
                        accept = create_accept_message(
                            self.agent_index, msg.from_, msg,
                            {"instruction": key, "ml_action": ml_action},
                        )
                        accept.metadata["timestamp"] = timestep
                        self._a2a_protocol.agent_index = self.agent_index
                        self._a2a_protocol.send_message(accept)
                        # 记录执行状态
                        self._a2a_pending_instruction = key
                        self._a2a_source_agent = msg.from_
                        self._a2a_source_task = msg.task_id
                        print(
                            f"[A2A] t={timestep} A{self.agent_index} ← REQUEST(A{msg.from_}) "
                            f"action='{self._truncate_message_for_log(raw_action, 200)}' → ACCEPT "
                            f"instruction='{key}' ml_action='{ml_action}'"
                        )
                        return ml_action
                    else:
                        # 无法执行：发送 REJECT
                        reject = create_reject_message(
                            self.agent_index, msg.from_, msg,
                            reason=f"cannot parse ml_action from: {raw_action[:40]}",
                        )
                        reject.metadata["timestamp"] = timestep
                        self._a2a_protocol.send_message(reject)
                        print(
                            f"[A2A] t={timestep} A{self.agent_index} ← REQUEST(A{msg.from_}) "
                            f"action='{self._truncate_message_for_log(raw_action, 200)}' → REJECT (parse failed)"
                        )

                elif msg.type == MessageType.ACCEPT:
                    content = msg.content or {}
                    print(
                        f"[A2A] t={timestep} A{self.agent_index} ← ACCEPT(A{msg.from_}) "
                        f"instruction='{content.get('instruction', '')}'"
                    )
                    conv_id = self._a2a_protocol._get_conversation_id(msg)
                    self._a2a_protocol.active_conversations[conv_id] = ProtocolState.EXECUTING

                elif msg.type == MessageType.INFORM:
                    content = msg.content or {}
                    print(
                        f"[A2A] t={timestep} A{self.agent_index} ← INFORM(A{msg.from_}) "
                        f"action='{content.get('action', '')}' "
                        f"status='{content.get('status', '')}'"
                    )

                elif msg.type == MessageType.REJECT:
                    content = msg.content or {}
                    print(
                        f"[A2A] t={timestep} A{self.agent_index} ← REJECT(A{msg.from_}) "
                        f"reason='{content.get('reason', '')}'"
                    )
        except Exception:
            pass  # 协议层不能影响主流程
        return None

    def _a2a_send_inform_completion(self, timestep: int):
        """
        当 A2A 驱动的动作完成后，向请求方发送 INFORM(completed) 消息。
        """
        if not self._a2a_pending_instruction or self._a2a_source_agent is None:
            return
        try:
            from collab_overcooked.a2a_protocol.message import create_inform_message
            inform = create_inform_message(
                from_agent=self.agent_index,
                to_agent=self._a2a_source_agent,
                action=self._a2a_pending_instruction,
                status="completed",
                details={"instruction": self._a2a_pending_instruction},
                task_id=self._a2a_source_task,
            )
            inform.metadata["timestamp"] = timestep
            self._a2a_protocol.agent_index = self.agent_index
            self._a2a_protocol.send_message(inform)
            print(
                f"[A2A] t={timestep} A{self.agent_index} → INFORM(A{self._a2a_source_agent}) "
                f"instruction='{self._a2a_pending_instruction}' status=COMPLETED"
            )
        except Exception:
            pass
        finally:
            self._a2a_pending_instruction = None
            self._a2a_source_agent = None
            self._a2a_source_task = None

    # ──────────────────────────────────────────────────────────────
    # A2A Protocol: 旁路记录层（将 Collab(...) 翻译为 A2A 消息存档）
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _truncate_message_for_log(text: str, max_len: int = 200) -> str:
        """
        智能截断消息文本用于日志输出。
        
        Args:
            text: 原始消息文本
            max_len: 最大长度（默认 200）
            
        Returns:
            截断后的文本，如果被截断则添加 "..."
        """
        if not text:
            return ""
        text = text.replace("\n", " ").replace("\r", " ")
        if len(text) <= max_len:
            return text
        # 尝试在单词边界截断
        truncated = text[:max_len]
        last_space = truncated.rfind(" ")
        if last_space > max_len * 0.7:  # 如果最后一个空格在 70% 位置之后
            truncated = truncated[:last_space]
        return truncated + "..."

    def _record_a2a_from_collab(
        self,
        talk_text: Optional[str],
        role: str,
        partner: Optional["LLMAgents"],
        timestep: int,
    ) -> None:
        """
        A2A 旁路记录层：将现有 Collab(...) 消息翻译为 A2A 消息并记录。
        纯记录，不改变任何现有逻辑。

        对应关系：
          Collab(request(...))  →  REQUEST
          Collab(ack(...))      →  ACCEPT
          Collab(seek(...))     →  PROPOSE
          Collab(deny(...))     →  REJECT
          其他 talk             →  INFORM
        """
        if not talk_text or not isinstance(talk_text, str):
            return
        try:
            from collab_overcooked.a2a_protocol.message import (
                MessageType, A2AMessage,
                create_request_message, create_accept_message,
                create_reject_message, create_inform_message, create_propose_message,
            )
            lowered = talk_text.lower()
            to_idx = partner.agent_index if partner else -1
            task_id = None
            if self.task_pool:
                t = self.task_pool.get_agent_current_task(self.agent_index)
                task_id = t["id"] if t else None

            if "request(" in lowered:
                msg = create_request_message(
                    from_agent=self.agent_index, to_agent=to_idx,
                    action=talk_text, task_id=task_id
                )
            elif "ack(" in lowered:
                msg = create_accept_message(
                    from_agent=self.agent_index, to_agent=to_idx,
                    original_message=A2AMessage(
                        type=MessageType.REQUEST, from_=to_idx, to=self.agent_index,
                        task_id=task_id
                    )
                )
            elif "seek(" in lowered:
                msg = create_propose_message(
                    from_agent=self.agent_index, to_agent=to_idx,
                    action=talk_text, task_id=task_id
                )
            elif "deny(" in lowered:
                msg = create_reject_message(
                    from_agent=self.agent_index, to_agent=to_idx,
                    original_message=A2AMessage(
                        type=MessageType.REQUEST, from_=to_idx, to=self.agent_index,
                        task_id=task_id
                    )
                )
            else:
                msg = create_inform_message(
                    from_agent=self.agent_index, to_agent=to_idx,
                    action=talk_text, status="talking", task_id=task_id
                )

            msg.metadata["timestamp"] = timestep
            msg.metadata["role"] = role  # "you" or "team"
            # Keep protocol identity synchronized with runtime-assigned agent index.
            self._a2a_protocol.agent_index = self.agent_index
            self._a2a_protocol.send_message(msg)
            # 可见日志：每条 Collab 消息都打印 A2A 类型和摘要
            short = self._truncate_message_for_log(talk_text, 200)
            print(
                f"[A2A] t={timestep} A{self.agent_index} → {msg.type.value}"
                f"(A{to_idx}) task={task_id} msg='{short}'"
            )
        except Exception:
            pass  # 记录层任何异常都不影响主流程

    def _preview_primary_action(self, action_block: Optional[str]) -> str:
        body = self._strip_action_prefix(action_block or "")
        body = self._sanitize_action_text(body)
        tokens = [token.strip() for token in body.split(";") if token.strip()]
        for token in tokens:
            if not self._is_collab_action(token):
                return token
        return tokens[0] if tokens else ""

    def _strip_code_fences(self, text: str) -> str:
        if not isinstance(text, str):
            return ""
        stripped = text.strip()
        if not stripped.startswith("```"):
            return stripped
        content = stripped[3:]
        if "\n" in content:
            _, remainder = content.split("\n", 1)
        else:
            remainder = ""
        closing = remainder.rfind("```")
        if closing != -1:
            remainder = remainder[:closing]
        return remainder.strip()

    def _set_planner_call_context(self, call_type: str, **metadata):
        if not hasattr(self, "planner") or self.planner is None:
            return
        context = {"call_type": call_type, "agent_index": self.agent_index}
        if metadata:
            context.update(metadata)
        setattr(self.planner, "_rl_call_context", context)

    def _queue_reward_event(self, action_text: Optional[str], call_index: Optional[int], call_type: str):
        if not self.reward_tracker or self.agent_index is None:
            self._pending_reward_event = None
            self._last_reward_entry = None
            return
        timestamp = getattr(self, "current_timestep", None)
        normalized = (action_text or "").strip()
        self._last_reward_entry = None
        self._pending_reward_event = {
            "action": normalized,
            "call_index": call_index,
            "call_type": call_type,
            "timestamp": timestamp,
        }

    def _apply_penalty_to_last_entry(self, penalty_type: str, detail: str) -> bool:
        if not self.reward_tracker or self.agent_index is None:
            return False
        entry = getattr(self, "_last_reward_entry", None)
        if not entry:
            return False
        if penalty_type == "format":
            value = self.reward_tracker.format_penalty_value
            reward_key = "format_reward"
        elif penalty_type == "validator":
            value = self.reward_tracker.validator_penalty_value
            reward_key = "validator_reward"
        else:
            return False
        penalties = entry.get("penalties")
        if penalties is None:
            penalties = []
            entry["penalties"] = penalties
        penalties.append({"type": penalty_type, "detail": detail, "value": value})
        entry[reward_key] = entry.get(reward_key, 0.0) + value
        entry["total"] = entry.get("total", 0.0) + value
        return True

    def _ensure_penalty_reward_entry(self, action_text: Optional[str], call_type: str = "planner_main"):
        if self._pending_reward_event is not None or self._last_reward_entry is not None:
            return
        if not self.reward_tracker or self.agent_index is None:
            return
        if not self.pending_llm_logs:
            return
        entry = self.pending_llm_logs[-1]
        call_index = entry.get("call_index")
        if call_index is None:
            return
        call_type_entry = entry.get("call_type") or call_type
        normalized = self._strip_action_prefix(action_text or "").strip()
        if not normalized:
            normalized = "[EMPTY]"
        self._queue_reward_event(normalized, call_index, call_type_entry)

    def _register_penalty(self, penalty_type: str, detail: str):
        if not self.reward_tracker or self.agent_index is None:
            return
        if self._pending_reward_event is None:
            if self._apply_penalty_to_last_entry(penalty_type, detail):
                return
        if penalty_type == "format":
            self.reward_tracker.register_format_error(self.agent_index, detail)
        else:
            self.reward_tracker.register_validator_error(self.agent_index, detail)

    def _record_communication_penalty(
        self, action_block: Optional[str], detail: str, penalty_type: str = "validator"
    ):
        if not self.reward_tracker or self.agent_index is None:
            return
        if not self.pending_llm_logs:
            return
        call_index = self.pending_llm_logs[-1].get("call_index")
        if call_index is None:
            return
        normalized = self._strip_action_prefix(action_block or "").strip()
        if not normalized:
            normalized = "[EMPTY]"
        print(f"[Validator] {self.name} {detail}.")
        self._queue_reward_event(normalized, call_index, "communication")
        self._register_penalty(penalty_type, detail)
        self._annotate_last_log_metadata(
            has_failure_context=True, validator_feedbacks=[detail]
        )
        self._flush_reward_event()

    def _flush_reward_event(self):
        if not self._pending_reward_event:
            return
        if not self.reward_tracker or self.agent_index is None:
            self._pending_reward_event = None
            self._last_reward_entry = None
            return
        event = self._pending_reward_event
        entry = self.reward_tracker.register_llm_action(
            agent_index=self.agent_index,
            timestamp=event.get("timestamp", self.current_timestep),
            action_text=event.get("action"),
            agent_name=self.name,
            call_index=event.get("call_index"),
            call_type=event.get("call_type"),
        )
        self._last_reward_entry = entry
        self._pending_reward_event = None

    def _relabel_last_llm_call(self, call_type: str, metadata: Optional[Dict[str, Any]] = None) -> Optional[int]:
        if not self.pending_llm_logs:
            return None
        entry = self.pending_llm_logs[-1]
        entry["call_type"] = call_type
        merged_meta = entry.get("metadata") or {}
        if metadata:
            merged_meta.update(metadata)
            entry["metadata"] = merged_meta
        elif merged_meta:
            entry["metadata"] = merged_meta
        if hasattr(self.planner, "relabel_last_record"):
            try:
                self.planner.relabel_last_record(call_type, metadata or None)
            except Exception:
                pass
        return entry.get("call_index")

    def _mark_last_call_as_action(self, forced_action: str, reason: str) -> Optional[int]:
        """Reclassify the latest LLM call as an action-producing query."""
        metadata = {
            "forced_action": forced_action,
            "forced_action_reason": reason,
        }
        call_index = self._relabel_last_llm_call("planner_main", metadata)
        if self.reward_tracker and self.agent_index is not None:
            self.reward_tracker.register_format_error(self.agent_index, reason)
        return call_index

    def _reset_comm_turn_counter(self):
        if self._communication_turn_timestamp != self.current_timestep:
            self._communication_turn_counter = 0
            self._communication_turn_timestamp = self.current_timestep

    def _register_comm_turn(self) -> int:
        self._reset_comm_turn_counter()
        self._communication_turn_counter += 1
        return self._communication_turn_counter

    def _set_action_override_from_plan(self, plan_tokens: List[str]):
        primary = ""
        for token in plan_tokens:
            if token and not self._is_collab_action(token):
                primary = token
                break
        if not primary:
            return
        call_index = len(self.pending_llm_logs) - 1
        if call_index < 0:
            return
        self._relabel_last_llm_call("planner_main")
        self._forced_action_override = {"call_index": call_index, "action": primary}

    def _gather_pending_actions(self, primary_action: Optional[str], queued_actions):
        pending = []
        if primary_action and not self._is_collab_action(primary_action):
            pending.append(primary_action)
        for action in queued_actions or []:
            if action and not self._is_collab_action(action):
                pending.append(action)
        return pending

    def _ensure_conversation_timestamp(self, timestamp: int):
        if self.conversation_history_timestamp != timestamp:
            self.conversation_history_timestamp = timestamp
            self.current_turn_conversation = []

    def _sanitize_action_text(self, text: Optional[str]) -> str:
        if not isinstance(text, str):
            return ""
        cleaned = text.replace("```", "").replace("```]", "")
        cleaned = cleaned.replace("[```", "").replace("```", "")
        cleaned = cleaned.strip()
        # Remove Markdown formatting: **bold**, *italic*, _underline_, etc.
        # Remove ** and * (but preserve them if they're part of function names, which is unlikely)
        cleaned = re.sub(r'\*\*([^*]+)\*\*', r'\1', cleaned)  # Remove **bold**
        cleaned = re.sub(r'\*([^*\s]+)\*', r'\1', cleaned)  # Remove *italic* (but not if it's part of a word)
        cleaned = re.sub(r'_\b([^_]+)\b_', r'\1', cleaned)  # Remove _underline_
        cleaned = re.sub(r'\*\*', '', cleaned)  # Remove any remaining **
        cleaned = re.sub(r'(?<!\w)\*(?!\w)', '', cleaned)  # Remove standalone * (not part of word)
        # Many LLMs emit one action per line / code fence; treat newlines as action separators.
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = cleaned.replace("\n", ";")
        # Remove stray markdown fences or unmatched brackets
        cleaned = cleaned.rstrip("`")
        cleaned = cleaned.rstrip("]")
        # Remove leading/trailing whitespace and clean up
        cleaned = cleaned.strip()
        return cleaned

    def append_conversation_line(self, speaker, message: str, propagate: bool = True):
        text = (message or "").strip()
        if not text or text == "[NOTHING]":
            return
        if self.current_timestep is not None:
            self._ensure_conversation_timestamp(self.current_timestep)
        line = f"{speaker}: {text}"
        self.conversation_history.append(line)
        if len(self.conversation_history) > 30:
            self.conversation_history = self.conversation_history[-30:]
        self.current_turn_conversation.append(line)
        if len(self.current_turn_conversation) > 20:
            self.current_turn_conversation = self.current_turn_conversation[-20:]
        if (
            self.current_observation_snapshot
            and self.current_observation_snapshot.get("timestamp") == self.conversation_history_timestamp
        ):
            self.current_observation_snapshot["past_conversation"] = self.format_conversation_history()
        if propagate:
            lower_text = text.lower()
            teammate = getattr(self, "teammate", None)
            if speaker == self.name:
                self.pending_collab_reply = False
                if "collab(" in lower_text:
                    self._collab_ack_consumed = True
                if teammate is not None:
                    needs_reply = self._collab_requires_reply(lower_text)
                    teammate.pending_collab_reply = (
                        needs_reply and not getattr(teammate, "_collab_ack_consumed", False)
                    )
            elif teammate is not None and speaker == teammate.name:
                needs_reply = self._collab_requires_reply(lower_text)
                self.pending_collab_reply = needs_reply and not self._collab_ack_consumed
                teammate.pending_collab_reply = False
        if propagate and getattr(self, "teammate", None) is not None:
            teammate_history = getattr(self.teammate, "conversation_history", None)
            if teammate_history is not None:
                self.teammate.append_conversation_line(speaker, message, propagate=False)

    def format_conversation_history(self) -> str:
        if not self.current_turn_conversation:
            return "[EMPTY]"
        return "\n".join(self.current_turn_conversation[-10:])

    def update_recent_goal_text(self, new_goal: Optional[str], reset_on_empty: bool = False):
        if new_goal is None:
            return
        cleaned = (new_goal or "").strip()
        if cleaned:
            self.current_recent_goal_text = cleaned
        elif reset_on_empty:
            self.current_recent_goal_text = "[EMPTY]"

    def _log_llm_call(self, call_type: str, prompt_text: str, response_text: str, tokens: int = 0, metadata: Optional[dict] = None):
        prompt = (prompt_text or "").strip()
        response = (response_text or "").strip()
        entry = {
            "timestamp": self.current_timestep,
            "agent": self.name,
            "call_index": len(self.pending_llm_logs),
            "call_type": call_type,
            "input": prompt,
            "output": response,
            "tokens": tokens,
        }
        if metadata:
            entry["metadata"] = metadata
        self.pending_llm_logs.append(entry)
    
    def _annotate_last_log_metadata(self, **fields):
        if not self.pending_llm_logs:
            return
        entry = self.pending_llm_logs[-1]
        metadata = entry.get("metadata") or {}
        for key, value in fields.items():
            if key in {"format_issues", "validator_feedbacks"}:
                existing = metadata.get(key, [])
                if not isinstance(existing, list):
                    existing = [existing] if existing else []
                items = value if isinstance(value, list) else [value]
                for item in items:
                    if item not in existing:
                        existing.append(item)
                metadata[key] = existing
            elif key == "has_failure_context":
                metadata[key] = bool(value or metadata.get(key))
            else:
                metadata[key] = value
        entry["metadata"] = metadata

    def _handle_format_issues(self, issues: List[str]):
        if not issues:
            return
        self._annotate_last_log_metadata(format_issues=issues, has_failure_context=True)
        if self.reward_tracker and self.agent_index is not None:
            for issue in issues:
                self.reward_tracker.register_format_error(self.agent_index, issue)

    def _handle_validator_failure(self, message: Optional[str]):
        if not message:
            return
        normalized = message.strip()
        if not normalized or "success" in normalized.lower():
            return
        self._annotate_last_log_metadata(
            has_failure_context=True, validator_feedbacks=[normalized]
        )
        self._register_penalty("validator", normalized)

    def _report_action_format_error(self, detail: str, action_text: Optional[str] = None, call_type: str = "planner_main"):
        self._ensure_penalty_reward_entry(action_text, call_type)
        self._register_penalty("format", detail)
    
    def _format_food_description(self, food_state):
        if isinstance(food_state, str):
            return food_state
        if isinstance(food_state, (list, tuple)):
            items = [str(x) for x in food_state if str(x)]
            if not items:
                return "[EMPTY]"
            if len(items) == 1:
                return items[0]
            return ", ".join(items)
        return str(food_state)

    def record_history_entry(self, think_text: str, recent_goal_text: str, action_text: str, error_text: str = ""):
        if not self.current_observation_snapshot:
            return
        entry = {
            "timestamp": self.current_observation_snapshot["timestamp"],
            "order": self.current_observation_snapshot["order"],
            "scene": self.current_observation_snapshot["scene"],
            "agent_state": self.current_observation_snapshot["agent_state"],
            "past_conversation": self.current_observation_snapshot["past_conversation"],
            "think": (think_text or "").strip() or "[EMPTY]",
            "recent_goal": (recent_goal_text or "").strip() or "[EMPTY]",
            "action": (action_text or "").strip() or "[EMPTY]",
        }
        if error_text:
            entry["error"] = error_text.strip()
        window = max(0, getattr(self, "history_window", 0))
        if window <= 0:
            self.history_records = []
            self.current_recent_goal_text = entry["recent_goal"]
            return
        self.history_records = [h for h in self.history_records if h["timestamp"] != entry["timestamp"]]
        self.history_records.append(entry)
        if len(self.history_records) > window:
            self.history_records = self.history_records[-window:]
        self.current_recent_goal_text = entry["recent_goal"]

    def build_history_prompt(self) -> str:
        window = max(0, getattr(self, "history_window", 0))
        if window <= 0 or not self.history_records:
            return ""
        current_ts = getattr(self, "current_timestep", None)
        usable_entries = (
            [h for h in self.history_records if current_ts is None or h["timestamp"] < current_ts]
            if current_ts is not None
            else self.history_records
        )
        if not usable_entries:
            return ""
        blocks = []
        for entry in usable_entries[-window:]:
            blocks.append(self.format_history_entry(entry))
        return "\n".join(blocks)

    def format_history_entry(self, entry: dict) -> str:
        block = [
            f"timestep {entry['timestamp']}:",
            f"Order: {entry['order']}",
            entry["scene"],
            f"Agent State: {entry['agent_state']}",
            "Past conversation:",
            entry.get("past_conversation", "[EMPTY]") or "[EMPTY]",
            f"Think: {entry.get('think', '[EMPTY]')}",
            f"Recent Goal: {entry.get('recent_goal', '[EMPTY]')}",
            f"Action: {entry.get('action', '[EMPTY]')}",
        ]
        if entry.get("error"):
            block.append(f"Error: {entry['error']}")
        return "\n".join(block)

    def format_current_observation_block(self, timestamp: int, order_text: str, scene_text: str, agent_state_text: str, conversation_text: str, recent_goal_text: str) -> str:
        return (
            f"timestep {timestamp}:\n"
            f"Order: {order_text}\n"
            f"{scene_text}\n"
            f"Agent State: {agent_state_text}\n"
            "Past conversation:\n"
            f"{conversation_text}\n"
            f"Recent Goal: {recent_goal_text or '[EMPTY]'}\n"
        )

    def _append_with_newline(self, base: str, extra: str, blank_line: bool = False) -> str:
        base = base or ""
        extra = extra or ""
        if not extra:
            return base
        if not base:
            return extra
        if not base.endswith("\n"):
            base += "\n"
        if blank_line and not base.endswith("\n\n"):
            base += "\n"
        return base + extra

    def _finalize_action_return(self, chosen_action, interaction_param):
        logs_copy = copy.deepcopy(getattr(self, "pending_llm_logs", []))
        original_log = self.turn_statistics_dict["content"].get("original_log")
        if not isinstance(original_log, list):
            original_log = [[], []]
            self.turn_statistics_dict["content"]["original_log"] = original_log
        original_log[self.agent_index] = logs_copy
        self.pending_llm_logs = []
        return chosen_action, interaction_param

    def parse_ml_action(self, action_string):
        ml_action = ""
        action, params = self.parse_params_in_action(action_string)
        self.parse_action = action
        self.parse_action_params = params
        # compare params with lower objects list
        for index, p in enumerate(self.parse_action_params):
            for u in self.mdp.utensil_list:
                if p == u.lower():
                    self.parse_action_params[index] = u
            for i in self.mdp.default_ingredients:
                if p == i.lower():
                    self.parse_action_params[index] = i
        # parse function like return false,meaning the return action list is invalid.
        if self.parse_action == "":
            print(action_string)
            detail = "Please ensure the Action field lists semicolon-separated function calls without extra narration."
            print(detail)
            self._report_action_format_error(detail, action_string)
            return (
                False,
                detail,
            )
        if self.parse_action == "place_obj_on_counter":
            ml_action = "place_obj_on_counter()"
        elif self.parse_action == "pickup":
            if len(params) != 2:
                detail = "Wrong pickup() params. It should have 2 params: obj and distination."
                self._report_action_format_error(detail, action_string)
                return (
                    False,
                    detail,
                )
            # check pickup from dispenser, and pickup from utensil, pick up from counter
            # check whether the item is in the recipe list
            if params[0] not in self.mdp.all_ingredients + ["dish"]:
                detail = f"Wrong pickup() params {params[0]}. It does not belong to any recipe ingredients or dish."
                self._report_action_format_error(detail, action_string)
                return (
                    False,
                    detail,
                )
            if (
                (params[1] not in self.mdp.utensil_list)
                and ("counter" not in params[1])
                and ("dispenser" not in params[1])
            ):
                detail = f"Wrong pickup() params {params[1]}. It does not belong to any utensils, dispenser or counter."
                self._report_action_format_error(detail, action_string)
                return (
                    False,
                    detail,
                )
            if ("dispenser" in params[1]) and (
                params[0] not in self.mdp.default_ingredients + ["dish"]
            ):
                detail = f"Wrong pickup() params {params[0]},{params[1]}. You can only get raw ingredients and dish from dispenser."
                self._report_action_format_error(detail, action_string)
                return (
                    False,
                    detail,
                )
            # in case of pickup(dish,ingredient_dispenser) , pickup(ingredient,dish_dispenser)
            if ("dish" in params[0]) and ("ingredient_dispenser" in params[1]):
                detail = f"Wrong pickup() params {params[0]},{params[1]}. You can only get dish from dish_dispenser."
                self._report_action_format_error(detail, action_string)
                return (
                    False,
                    detail,
                )
            if "dish" not in params[0] and "dish_dispenser" in params[1]:
                detail = f"Wrong pickup() params {params[0]},{params[1]}. You can only get ingredient from ingredient_dispenser."
                self._report_action_format_error(detail, action_string)
                return (
                    False,
                    detail,
                )
            ml_action = f"pickup({params[0]},{params[1]})"
        elif self.parse_action == "put_obj_in_utensil":
            if len(params) != 1:
                detail = "Wrong put_obj_in_utensil() params. It should have 1 params: utensil."
                self._report_action_format_error(detail, action_string)
                return (
                    False,
                    detail,
                )
            if params[0] in self.mdp.utensil_list:
                ml_action = f"put_obj_in_utensil({params[0]})"
            else:
                if params[0] == "dish":
                    detail = (
                        "Wrong put_obj_in_utensil() params: dish.\n"
                        "Dish 不是 utensil，不能 put_obj_in_utensil(dish)。\n"
                        "如果你想装盘：先 pickup(dish,dish_dispenser) 或 pickup(dish,counter)，再 fill_dish_with_food(utensil_name)。\n"
                        "如果当前订单不需要 dish：直接 pickup(成品,utensil_name) 然后 deliver_soup()。"
                    )
                else:
                    detail = f"Wrong put_obj_in_utensil() parmas:{params[0]}"
                self._report_action_format_error(detail, action_string)
                return False, detail
        elif self.parse_action == "fill_dish_with_food":
            if len(params) != 1:
                detail = "Wrong fill_dish_with_food() params. It should have 1 params: utensil."
                self._report_action_format_error(detail, action_string)
                return (
                    False,
                    detail,
                )
            if params[0] in self.mdp.utensil_list:
                ml_action = f"fill_dish_with_food({params[0]})"
            else:
                detail = f"Wrong fill_dish_with_food() parmas:{params[0]}"
                self._report_action_format_error(detail, action_string)
                return False, detail
        elif self.parse_action == "deliver_soup":
            ml_action = "deliver_soup()"
        elif self.parse_action == "check_recipe":
            ml_action = "check_recipe()"
        # 	check all the action need to interact with utensils
        elif self.parse_action in self.mdp.interact_actions:
            if len(params) != 1:
                detail = "Please ensure the Action field is a semicolon-separated list of function calls without narration."
                self._report_action_format_error(detail, action_string)
                return (
                    False,
                    detail,
                )
            utensils = self.mdp.interact_actions.get(self.parse_action, [])
            if params[0] in utensils:
                ml_action = f"{self.parse_action}({params[0]})"
            else:
                detail = f"Wrong {self.parse_action}() parmas:{params[0]}"
                self._report_action_format_error(detail, action_string)
                return False, detail
        elif self.parse_action == "wait":
            time_to_wait = self.parse_wait_string(action_string)
            if time_to_wait > 5:
                detail = "Too long wait time. It should be less than 5."
                self._report_action_format_error(detail, action_string)
                return False, detail
            else:
                self.time_to_wait = time_to_wait
                ml_action = action_string
        else:
            print(action_string)
            detail = "Please ensure the Action field is a semicolon-separated list of function calls without narration."
            print(detail)
            self._report_action_format_error(detail, action_string)
            return (
                False,
                detail,
            )

        return True, ml_action

    def team_index_str(self):
        return str(1 - self.agent_index)

    def parse_wait_string(self, s):
        # Check if it's just "wait"
        if s == "wait":
            return 1

        # Remove 'wait' and other characters from the string
        s = (
            s.replace("wait", "")
            .replace("(", "")
            .replace(")", "")
            .replace('"', "")
            .replace(".", "")
        )

        # If it's a number, return it as an integer
        if s.isdigit():
            return int(s)

        # If it's not a number, return a default value or raise an exception
        return 1

    def change_communication_role(self, role, team_role):
        # self
        self.communication_role = role
        a = self.load_prompt_file()
        # 加载所有队友的提示词（支持多Agent）
        for _tm in getattr(self, 'teammates', []):
            if hasattr(_tm, 'load_prompt_file'):
                _tm.load_prompt_file()

    def build_correction_suffix(self):
        failure_notes = [
            dialog["content"]
            for dialog in self.planner.dialog_history_list
            if dialog.get("role") == "failure_explanation"
        ]
        suffix_lines = ["\n\n### Correction Guidance"]
        if failure_notes:
            suffix_lines.append("Controller feedback from the previous attempt:")
            suffix_lines.extend(failure_notes)
        suffix_lines.append(
            "Regenerate a complete response that strictly follows the Think / Recent Goal / Action format and resolves the issue above."
        )
        return "\n".join(suffix_lines)

    def change_correct_prompt(self):
        base_prompt = self.load_prompt_file(mode="origin")
        correction_suffix = self.build_correction_suffix()
        self.planner.instruction_head_list[0]["content"] = base_prompt + correction_suffix

    def enforce_collab_reply(self, last_response: str):
        base_prompt = self.planner.current_user_message.get("content", "")
        correction_prompt = (
            base_prompt
            + "\n\nYou just received a collaboration request from your teammate. "
            "You must respond with a Collab(...) message (request/seek/deny/ack) acknowledging or addressing the request. "
            "Rewrite your previous reply accordingly.\n\nYour last reply was:\n"
            + last_response
            + "\n\nReturn a corrected response that includes a Collab(...) action."
        )
        self.planner.current_user_message = {"role": "user", "content": correction_prompt}
        self._set_planner_call_context("format_correct", role="collab_reply")
        response, correction_tokens = self.planner.query(
            proxy=self.proxy, stop="Scene", trace=True
        )
        fmt_error = self.turn_statistics_dict["statistical_data"]["error"][self.agent_index]["format_error"]
        fmt_error["error_num"] += 1
        fmt_error["error_message"].append(correction_prompt)
        fmt_corr = self.turn_statistics_dict["statistical_data"]["error_correction"][self.agent_index]["format_correction"]
        fmt_corr["correction_num"] += 1
        fmt_corr["correction_tokens"].append(correction_tokens)
        return response, correction_tokens

    def name(self):
        return "A" + str(self.agent_index)  # A0-A4 instead of P0-P4

    def team_name(self):
        return "Player " + self.team_index_str()

    def message_formate_control(
        self, role: str, turn_dialog_self: list, turn_dialog_team: list
    ):
        pre_message = ""
        answer_num = 1
        think_turn = 1
        flag = False
        # turn 0
        # pre_message += f'''{self.name} think history turn 0 : [EMPTY]\n'''
        ##As asker, generate message for self
        # <example>:
        # Order:Onion_soup(No recipe)
        # Scene 0: Chef holds nothing. Assistant holds nothing. Assistant's action:None.Kitchen states: Chef space:Pot0 empty.Pot1 empty.chopping_board1 empty.Stirrer empty. Assistant space:chopping_board0 empty.Cooker empty.Counter:empty.
        # Assistant think history turn 0 : [EMPTY]
        # Assistant think history turn 1 : I do not know what to do. I should ask chef.
        # Assistant say turn 1 : Can you give me advice?
        # Chef  say turn 1 : You should wait.<END>
        # Assistant think : I need to wait.
        # Assistant say : [NOTHING]
        # Assistant plan: wait(1)
        # </example>
        if role == "asker":
            for t in turn_dialog_self:
                # if t["role"] == "think":
                # 	pre_message +=  f'''{self.name} think history turn {think_turn} : {t['content']}\n'''
                if t["role"] == "talk":
                    pre_message += f"""{self.name} say history turn {think_turn} : {t['content']}\n"""
                    # search teammate response
                    for r in turn_dialog_team:
                        if r["role"] == "talk":
                            if think_turn == answer_num:
                                pre_message += f"""{self.teammate.name} say history turn {think_turn} : {r['content']}\n"""
                                break
                            else:
                                answer_num += 1
                    think_turn += 1
        ##As answer, generate message for self
        # <example>:
        # Assistant think history turn 0 : [EMPTY]
        # Chef say history turn 1 : Can you pick up an onion for me ?
        # Assistant think history turn 1: I can go the ingredient_dispenser pick an onion for chef. I do not know if it is right.
        # Assistant say history turn 1: Do you want me pick onion from ingredient_dispenser?
        # Chef say history turn 2 : YES.
        # Assistant think: I known i need to pick onion from ingredient_dispenser.
        # Assistant say : OK<END>
        # Assistant plan : pickup(onion,ingredient_dispenser)
        # </example>
        elif role == "answer":
            for t in turn_dialog_team:
                if t["role"] == "talk":
                    pre_message += f"""{self.teammate.name} say history turn {think_turn} : {t['content']}\n"""
                    # search self think and talk
                    for r in turn_dialog_self:
                        if r["role"] == "think":
                            if think_turn == answer_num:
                                # pre_message += f'''{self.name} think history turn {think_turn} : {r['content']}\n'''
                                flag = True
                            else:
                                answer_num += 1
                        if r["role"] == "talk" and flag:
                            pre_message += f"""{self.name} say history turn {think_turn} : {r['content']}\n"""
                            flag = False
                            break
                    think_turn += 1
        else:
            raise ValueError("Wrong actor for build communication message!")
        return pre_message

    def communication(self, message, state):
        """P1: 使用 get_comm_partner() 获取同任务通讯伙伴"""
        # 获取通讯伙伴（基于 TaskPool 配对）
        comm_partner = self.get_comm_partner()
        if comm_partner is None:
            # 没有通讯伙伴（如 Dishwasher 或未配对），直接返回动作
            print(f"[Comm] A{self.agent_index}({self.name}) 没有通讯伙伴，跳过通讯")
            action_block = self.parse_response(message, "action")
            if action_block == "":
                action_block = "wait(1)"
            return action_block

        self.end_talk = False
        self._forced_action_override = None
        you_response = message
        team_response = ""
        communication_turn = 0
        max_communication_turns = 3
        last_message = ""

        print(f"\n\n>>>>>>>>>>>>>>>>>>Begin communication (A{self.agent_index}({self.name}) <-> A{comm_partner.agent_index}({comm_partner.name}))<<<<<<<<<<<<<\n")
        while self.end_talk is False:
            communication_turn += 1
            if communication_turn > max_communication_turns:
                print(
                    f"[Warning] Communication exceeded {max_communication_turns} exchanges. Forcing termination."
                )
                forced_action = "wait(1)"
                forced_index = self._mark_last_call_as_action(
                    forced_action, "communication_turn_limit"
                )
                if self.reward_tracker and self.agent_index is not None:
                    self.reward_tracker.register_format_error(
                        self.agent_index, "communication_turn_limit"
                    )
                if forced_index is not None:
                    self._forced_action_override = {
                        "call_index": forced_index,
                        "action": forced_action,
                    }
                self.end_talk = True
                you_response = f"Action: {forced_action}"
                break
            print(f"Input for {comm_partner.name}" + ":\n")
            format_you_response = comm_partner.message_formate_control(
                "answer",
                comm_partner.planner.dialog_history_list,
                self.planner.dialog_history_list,
            )
            comm_partner.current_timestep = state.timestep
            comm_partner.state_prompt = comm_partner.generate_state_prompt(state)
            print(
                self._append_with_newline(
                    comm_partner.state_prompt,
                    comm_partner.planner.wong_message_prompt,
                    blank_line=True,
                )
            )
            # teammate must reply
            self.end_talk, team_response = comm_partner.answer(
                format_you_response, "team", state
            )
            print(f"Answer of {comm_partner.name}" + ":\n")
            print(team_response + "\n\n")
            teammate_talk, _ = comm_partner.parse_response(team_response, "talk")
            self.append_conversation_line(comm_partner.name, teammate_talk)

            print(f"Input for {self.name}" + ":\n")
            format_team_response = self.message_formate_control(
                "asker",
                self.planner.dialog_history_list,
                comm_partner.planner.dialog_history_list,
            )
            self.current_timestep = state.timestep
            self.state_prompt = self.generate_state_prompt(state)
            print(
                self._append_with_newline(
                    self.state_prompt,
                    self.planner.wong_message_prompt,
                    blank_line=True,
                )
            )
            last_message = format_team_response + "\n\n"
            self.pre_message = last_message

            self.end_talk, you_response = self.answer(
                format_team_response, "you", state
            )
            if self.end_talk is False:
                print(f"Answer of {self.name}" + ":\n")
            else:
                print(
                    f">>>>>>>>>>>>>>>>>>>>{self.name} decide to make action:<<<<<<<<<<<<<<<<<\n"
                )
            print(you_response + "\n\n\n")
            you_talk, _ = self.parse_response(you_response, "talk")
            self.append_conversation_line(self.name, you_talk)
        print("\n\n>>>>>>>>>>>>>>>>>>Finish communication<<<<<<<<<<<<<\n")
        # parse
        action_block = self.parse_response(you_response, "action")
        if action_block == "":
            print("You did not provide an action last time.\n")
            action_block, _ = self.important_part_no_create(1, "action", you_response)
        return action_block

    def important_part_no_create(self, retry_num, part_type, response):
        def _ensure_error_slots_for_agent() -> None:
            """
            Guard against partially initialized statistics arrays.
            Timeout/retry paths can run with stale templates in some runs.
            """
            sd = self.turn_statistics_dict.setdefault("statistical_data", {})
            error_list = sd.setdefault("error", [])
            error_corr_list = sd.setdefault("error_correction", [])

            while len(error_list) <= self.agent_index:
                error_list.append(
                    {
                        "format_error": {"error_num": 0, "error_message": []},
                        "validator_error": {"error_num": 0, "error_message": []},
                    }
                )
            while len(error_corr_list) <= self.agent_index:
                error_corr_list.append(
                    {
                        "format_correction": {"correction_num": 0, "correction_tokens": []},
                        "validator_correction": {
                            "correction_num": 0,
                            "reflection_obtain": [],
                            "correction_tokens": [],
                        },
                    }
                )

        part_display = {
            "think": "Think",
            "action": "Action",
            "talk": "Action",
            "recent_goal": "Recent Goal",
        }.get(part_type, part_type)
        prompt = (
            "\n\nYou did not create correct "
            + part_display
            + " part last time, now please remember to add "
            + part_display
            + " according to the format of example strictly!Below is the history:<BEGAIN>\n"
        )
        retry_num_max = retry_num
        final_response = response
        while retry_num > 0:
            # retry query
            self.planner.current_user_message = {
                "role": "user",
                "content": prompt
                + self.planner.current_user_message["content"]
                + "\n\nYour last response is :"
                + response
                + "\n\n<END>Now please return correct answer with your loss part.",
            }
            # print(self.planner.current_user_message)
            self._set_planner_call_context("format_correction", missing_part=part_type)
            response, correction_tokens = self.planner.query(
                proxy=self.proxy, stop="Scene", trace=True
            )
            self._log_llm_call(
                "format_correction",
                self.planner.current_user_message["content"],
                response,
                correction_tokens,
                {"missing_part": part_type},
            )
            # statistic
            _ensure_error_slots_for_agent()
            self.turn_statistics_dict["statistical_data"]["error"][self.agent_index][
                "format_error"
            ]["error_num"] += 1
            self.turn_statistics_dict["statistical_data"]["error"][self.agent_index][
                "format_error"
            ]["error_message"].append(self.planner.current_user_message["content"])

            # print(response)
            parse_result = self.parse_response(response, part_type)
            retry_num -= 1
            final_response = response
            response = "\n\nYour last response is :" + response + "\n\n"
            if retry_num == 0 and parse_result == "":
                warnings.warn(
                    "Failed to create "
                    + part_display
                    + " for "
                    + str(retry_num_max)
                    + " times"
                )
                parse_result = "You do not have " + part_display + " last time."
            if parse_result != "":
                # correction_num means the number of truly correct the format error ,maybe a success need several trys.
                # correction_tokens means all the tokens for trying, so len(correction_tokens)>= correction_num
                self.turn_statistics_dict["statistical_data"]["error_correction"][
                    self.agent_index
                ]["format_correction"]["correction_num"] += 1
                self.turn_statistics_dict["statistical_data"]["error_correction"][
                    self.agent_index
                ]["format_correction"]["correction_tokens"].append(correction_tokens)
                break
        return parse_result, final_response

    # It is used to give organized messages to gpt and organize the generated think, talk and other information
    def answer(self, message, role, state):
        self.state_prompt = self.generate_state_prompt(state)
        self.planner.current_user_message = {
            "role": "user",
            "content": self._append_with_newline(self.state_prompt, message, blank_line=True),
        }
        self._set_planner_call_context("communication", role=role)
        response, tokens_num = self.planner.query(
            proxy=self.proxy, stop="Scene", trace=True
        )
        extra_tokens = 0
        collab_violation_response = None
        collab_violation_logged = False
        if role == "team" and self.pending_collab_reply:
            preview_talk, _ = self.parse_response(response, "talk")
            if "collab(" not in (preview_talk or "").lower():
                collab_violation_response = response
                response, correction_tokens = self.enforce_collab_reply(response)
                extra_tokens += correction_tokens
        self._log_llm_call(
            "communication",
            self.planner.current_user_message["content"],
            response,
            tokens_num + extra_tokens,
            {"role": role},
        )
        if collab_violation_response is not None:
            violation_action = self.parse_response(collab_violation_response, "action")
            self._record_communication_penalty(
                violation_action, "missing_initial_collab_reply"
            )
            collab_violation_logged = True
        format_issues: List[str] = []
        think_text = self.parse_response(response, "think")
        if think_text == "":
            format_issues.append("missing_think")
        # Think did not generate error handling:
        if think_text == "":
            print("Do not create think", response)
            # print(self.planner.current_user_message["content"])
            think_text, _ = self.important_part_no_create(1, "think", message)

        # Agent1 has decided to stop talking
        # if ~self.end_talk:
        parse_talk, end_talk = self.parse_response(response, "talk")
        action_text = self.parse_response(response, "action")
        if action_text == "":
            format_issues.append("missing_action")
        recent_goal_text = self.parse_response(response, "recent_goal")
        if recent_goal_text == "":
            format_issues.append("missing_recent_goal")
        self.update_recent_goal_text(recent_goal_text)
        recent_goal_entry = recent_goal_text or self.current_recent_goal_text
        collab_reply_missing = (
            role == "team"
            and self.pending_collab_reply
            and "collab(" not in (parse_talk or "").lower()
        )
        if collab_reply_missing:
            if not collab_violation_logged:
                self._record_communication_penalty(
                    action_text, "missing_initial_collab_reply"
                )
                collab_violation_logged = True
            parse_talk = f"Collab(ack({self.teammate.name}))"
            end_talk = False
        if role == "team" and "collab(" in (parse_talk or "").lower():
            self._collab_ack_consumed = True
            self.pending_collab_reply = False
        self.planner.dialog_history_list.append({"role": "think", "content": think_text})
        total_tokens = tokens_num + extra_tokens
        self._handle_format_issues(format_issues)

        # statistic
        if role == "you":
            communication_index = self.agent_index
            self.turn_statistics_dict["statistical_data"]["communication"][
                communication_index
            ]["turn"].append(parse_talk)
            self.turn_statistics_dict["statistical_data"]["communication"][
                communication_index
            ]["token"].append(total_tokens)
            self.turn_statistics_dict["content"]["content"][communication_index].append(
                {
                    "agent": self.agent_index,
                    "think": think_text,
                    "say": parse_talk,
                    "action": action_text,
                    "recent_goal": recent_goal_entry,
                }
            )
        else:
            communication_index = self.teammate.agent_index
            self.teammate.turn_statistics_dict["statistical_data"]["communication"][
                communication_index
            ]["turn"].append(parse_talk)
            self.teammate.turn_statistics_dict["statistical_data"]["communication"][
                communication_index
            ]["token"].append(total_tokens)
            self.teammate.turn_statistics_dict["content"]["content"][
                communication_index
            ].append(
                {
                    "agent": self.agent_index,
                    "think": think_text,
                    "say": parse_talk,
                    "action": action_text,
                    "recent_goal": recent_goal_entry,
                }
            )

        if parse_talk == "":
            parse_talk, response = self.important_part_no_create(1, "talk", response)
        parse_talk, end_talk = self.parse_response(response, "talk")
        if parse_talk == "[NOTHING]" and end_talk:
            parse_talk = "OK.<END>"
        # A2A 旁路记录：将 Collab() 消息翻译为 A2A 格式（不影响任何逻辑）
        _comm_partner = self.get_comm_partner() if hasattr(self, '_a2a_protocol') else None
        self._record_a2a_from_collab(parse_talk, role, _comm_partner, getattr(self, 'current_timestep', 0))
        self.planner.dialog_history_list.append({"role": "talk", "content": parse_talk})

        # check if there is action
        # nothing did not produce an action
        new_plan = self.parse_response(response, "action")
        pattern = r"(?i)nothing"
        matches = re.findall(pattern, new_plan)
        if matches:
            return end_talk, response
        if self._is_collab_action(new_plan):
            return end_talk, response

        new_plan = self.parse_ml_action_top(new_plan, False)
        if new_plan:
            self._set_action_override_from_plan(new_plan)
        # When correct, new action should also replace the old action list.
        if not self.trace:
            while not self.action_wait_parse.empty():
                self.action_wait_parse.get()

            clean_plan = [a for a in new_plan if not self._is_collab_action(a)]
            for index, a in enumerate(clean_plan):
                if index > 0:
                    self.action_wait_parse.put(a)
            v = list(self.action_wait_parse.queue)
            # rprint(f"[red][CORRECT][/red]Generate new correct action list <{v}>\n")
            return end_talk, response
        # new_plan : list
        # Only when the number of action to do is 2, replace the last one by the action generated in communication
        # If the number of action to do = 1: self.action_wait_parse.qsize() = 0 ,add it to the sequence
        # If the number of action to do >2 : new generated action may not follow the timestep in the queue actions, it will perform badly.

        if self.action_wait_parse.qsize() == 0:
            clean_plan = [a for a in new_plan if not self._is_collab_action(a)]
            v = clean_plan
            print(f"\nGenerate new action list <{v}>\n")
            for p in clean_plan:
                self.action_wait_parse.put(p)
                # rprint(f"[green][ADD][/green]:Add new plan {p}\n")
        elif self.action_wait_parse.qsize() >= 1:
            pass
            rprint(
                f"[yellow][ADD][/yellow]:Current action are too much. Does not add <{new_plan}> in queue\n"
            )

        return end_talk, response

    # Extract the think and TALK from the GPT reply.
    # mode:think,<TALK>
    def parse_response(self, response, mode, need_correct=False):
        role = "Chef" if self.agent_index == 0 else "Assistant"
        text = self._strip_code_fences(response or "")

        section_pattern = re.compile(
            r"^\s*(Think|Recent Goal|Action)\s*:\s*(.*?)(?=^\s*(?:Think|Recent Goal|Action)\s*:|\Z)",
            re.IGNORECASE | re.DOTALL | re.MULTILINE,
        )
        sections = {}
        for match in section_pattern.finditer(text):
            header = match.group(1).strip().lower()
            content = (match.group(2) or "").strip()
            sections[header] = content

        if mode == "think":
            think_block = sections.get("think")
            if think_block:
                return think_block
            pattern = r"Think\s*:\s*(.*?)(?:\n\s*Recent Goal:|\n\s*Action:|\Z)"
            match = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                return match[0]
            if need_correct:
                response, _ = self.important_part_no_create(1, "think", response)
            else:
                response = ""
            return response
        elif mode == "recent_goal":
            recent_block = sections.get("recent goal")
            if recent_block:
                return recent_block
            pattern = r"Recent Goal\s*:\s*(.*?)(?:\n\s*Action:|\Z)"
            match = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                return match[0].strip()
            return ""
        elif mode == "talk":
            action_block = sections.get("action")
            if action_block:
                stripped = self._strip_action_prefix(action_block)
                stripped = self._sanitize_action_text(stripped)
                if self._is_collab_action(stripped):
                    return stripped, False
                if "[NOTHING]" in stripped.upper():
                    return "[NOTHING]", True
                return "[NOTHING]", True
            pattern = f"(?:{role})\\s+say\\s*:?\\s*(.*?)(?:\\s+|\\n+)?$"
            match = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                if "[NOTHING]" in match[0]:
                    return "[NOTHING]", True
                end_match = (
                    True
                    if re.findall(r"(.*?)(END)", match[0], re.DOTALL | re.IGNORECASE)
                    else False
                )
                return match[0], end_match
            return "[NOTHING]", True
        elif mode == "action":
            action_block = sections.get("action")
            if action_block:
                cleaned = self._sanitize_action_text(action_block)
                return f"Action: {cleaned}"
            # Try to extract Action field, handling Markdown formatting
            # Pattern 1: "Action:" or "**Action:**" followed by content (stop at next section)
            action_pattern = r"(?:\*\*)?Action\s*:?\s*(?:\*\*)?\s*(.*?)(?=\n\s*(?:Recent Goal|Think|$))"
            match = re.search(action_pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                action_content = match.group(1).strip()
                # Remove any trailing ** or markdown
                action_content = re.sub(r'\*\*\s*$', '', action_content)
                cleaned = self._sanitize_action_text(action_content)
                if cleaned:  # Only return if we found actual content
                    return f"Action: {cleaned}"
            # Pattern 2: Simple "Action: ..." without markdown
            action_pattern = r"Action\s*:\s*(.*)"
            match = re.search(action_pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                cleaned = self._sanitize_action_text(match.group(1))
                if cleaned:
                    return f"Action: {cleaned}"
            plan_pattern = rf"{role}\s+plan\s*:?\s*(.*)"
            plan_match = re.search(plan_pattern, text, re.IGNORECASE | re.DOTALL)
            if plan_match:
                plan_body = self._sanitize_action_text(plan_match.group(1))
                if plan_body:
                    return f"Action: {plan_body}"
            if need_correct:
                response, _ = self.important_part_no_create(1, "action", response)
            else:
                response = ""
            return response
        else:
            raise KeyError("Not exist mode for parse_response()")

    def generate_rethink(self, state):
        extracted_text = ""
        failture = ""
        message = self.message_formate_control(
            "asker",
            self.planner.dialog_history_list_storage,
            self.teammate.planner.dialog_history_list_storage,
        )
        failure_message = ""
        for index, s in enumerate(self.planner.dialog_history_list):
            if s["role"] == "failure_explanation":
                # Multi failed message
                if failure_message != "":
                    failure_message += (
                        f"When you handile the error above, {s['content']}"
                    )
                else:
                    failure_message = "Failed Reason:" + s["content"]
                    failture = s["content"]
        success_message = f"Solution: Your action '{self.current_ml_action}' successes at solving the problem.\n"
        self.load_prompt_file("reflection")
        self.state_prompt = self.generate_state_prompt(state)
        combined_prompt = self._append_with_newline(
            self._append_with_newline(
                self._append_with_newline(self.state_prompt, message, blank_line=True),
                failure_message,
                blank_line=True,
            ),
            success_message,
            blank_line=True,
        )
        self.planner.current_user_message = {
            "role": "user",
            "content": combined_prompt,
        }
        print(f"rethink input content: {self.planner.current_user_message['content']}")
        self._set_planner_call_context("rethink")
        response, correction_tokens = self.planner.query(
            proxy=self.proxy,
            stop="Scene",
            trace=self.trace,
            rethink=True,
        )
        self._log_llm_call(
            "rethink",
            self.planner.current_user_message["content"],
            response,
            correction_tokens,
            {"failure": failture},
        )
        print(f"response of rethink: {response}")
        pattern = r"Rethink:(.*?)\."
        matches = re.search(pattern, response, re.IGNORECASE | re.DOTALL)
        if matches:
            extracted_text = matches.group(1).strip()
            print(f"success extract rethink content.\n{extracted_text}")
            # count
            self.turn_statistics_dict["statistical_data"]["error_correction"][
                self.agent_index
            ]["validator_correction"]["reflection_obtain"] = self.current_timestep
        else:
            print("\nFailed to create rethink.\n")
        # problem + solution
        flag = 0
        for index, reflection in enumerate(self.failed_history):
            if failture in reflection["error"]:
                self.failed_history[index]["reflection_content"] += extracted_text
                flag = 1
        # If the reflection is not duplicated and reflection successes
        if flag == 0 and matches:
            self.failed_history.append(
                {
                    "error": failture,
                    "reflection_content": extracted_text,
                    "obtain_timestamp": self.current_timestep,
                }
            )
        return

    def generate_ml_action(self, state):
        """
        Selects a medium level action for the current state.
        Motion goals can be thought of instructions of the form:
                [do X] at location [Y]

        In this method, X (e.g. deliver the soup, pick up an onion, etc) is chosen based on
        a simple set of  heuristics based on the current state.

        Effectively, will return a list of all possible locations Y in which the selected
        medium level action X can be performed.
        """
        if self.test_mode:
            self.current_ml_action_steps = 0
            action = ""
            if len(self.test_ml_action) == 0:
                action = "wait(1)"
                self.time_to_wait = 1
                self.test_ml_action.append(action)
            else:
                action = self.test_ml_action[0]
                numbers = re.findall(r"\d+", action)
                if "wait" in action and numbers != []:
                    self.time_to_wait = int(numbers[0])
            return action

        # At most one error at a time
        failure_message = ""
        ml_action = ""
        think_output = ""
        recent_goal_entry = self.current_recent_goal_text
        planner_call_index = None
        if self.action_wait_parse.empty() or (not self.trace):
            state_prompt = self.generate_state_prompt(state)
            # Error checking
            for index, s in enumerate(self.planner.dialog_history_list):
                if s["role"] == "failure_explanation":
                    # Multi failed message
                    if failure_message != "":
                        failure_message += (
                            f"When you handile the error above, {s['content']}\n"
                        )
                    else:
                        failure_message = (
                            "\nBelow are the failed and think history about your last chosen action. Use this information to reach a correct action alone AND DO NOT COMMUNICATE WITH YOUR TEAMMATE:\n"
                            + s["content"]
                            + "\n"
                        )
            state_prompt += failure_message

            print(f"\n\n### Observation module to " + self.name + "\n")

            state_prompt = self.generate_state_prompt(state)
            self.state_prompt = state_prompt
            state_message = {
                "role": "user",
                "content": self._append_with_newline(
                    state_prompt,
                    self.planner.wong_message_prompt,
                    blank_line=True,
                ),
            }
            self.state_prompt = state_prompt
            self.planner.current_user_message = state_message
            # print("history information")
            print(f"{state_message['content']}")
            # statistic
            self.turn_statistics_dict["content"]["observation"][self.agent_index] = (
                state_message["content"]
            )

            print(f"\n\n\n### GPT Planner module\n")
            print("====== GPT Query ======")
            self._set_planner_call_context("planner_main")
            response, tokens_num = self.planner.query(
                proxy=self.proxy,
                stop="Scene",
                trace=self.trace,
                map=self.mdp.state_string(self.state).replace("ø", "o"),
            )
            self._log_llm_call(
                "planner_main",
                state_message["content"],
                response,
                tokens_num,
            )
            planner_call_index = len(self.pending_llm_logs) - 1
            print(response)
            # check whether need communication
            # check whether has the action
            format_issues: List[str] = []
            communicate_response, _ = self.parse_response(response, "talk")
            think_text = self.parse_response(response, "think")
            if think_text == "":
                format_issues.append("missing_think")
            recent_goal_text = self.parse_response(response, "recent_goal")
            if recent_goal_text == "":
                format_issues.append("missing_recent_goal")
            action_text_block = self.parse_response(response, "action")
            if action_text_block == "":
                format_issues.append("missing_action")
            think_output = think_text
            if think_text == "":
                print("\n\n\n******No Think Part, Correcting*********\n\n\n")
                think_text, response = self.important_part_no_create(1, "think", response)
                think_output = think_text
            if recent_goal_text == "":
                print("\n\n\n******No Recent Goal Part, Correcting*********\n\n\n")
                recent_goal_text, response = self.important_part_no_create(
                    1, "recent_goal", response
                )
            if action_text_block == "":
                print("\n\n\n******No Action Part, Correcting**********\n\n\n")
                action_text_block, response = self.important_part_no_create(1, "action", response)
            self._handle_format_issues(format_issues)
            self.update_recent_goal_text(recent_goal_text)
            recent_goal_entry = self.current_recent_goal_text
            # If important keyword still miss, this timestamp is failed.
            if think_text == "" or action_text_block == "":
                print(
                    "\n\n\nMiss important part after several retry. This timstep is failed!\n\n\n"
                )
                ml_action = "wait(1)"
                if planner_call_index is not None:
                    self._queue_reward_event(ml_action, planner_call_index, "planner_main")
                return ml_action

            self.planner.add_msg_to_dialog_history(
                {"role": "scene", "content": state_message["content"]}
            )
            self.planner.add_msg_to_dialog_history(
                {"role": "think", "content": think_text}
            )

            # statistic
            self.turn_statistics_dict["content"]["content"][self.agent_index].append(
                {
                    "agent": self.agent_index,
                    "think": think_text,
                    "say": communicate_response,
                    "action": action_text_block,
                    "recent_goal": recent_goal_entry,
                }
            )
            # If this function call is to correct action, the tokens should count into the number of correction_tokens.
            if failure_message != "":
                self.turn_statistics_dict["statistical_data"]["error_correction"][
                    self.agent_index
                ]["validator_correction"]["correction_tokens"].append(tokens_num)
                # check if the action is empty
                if action_text_block == "":
                    action_parse, action_text_block = self.important_part_no_create(1, "action", response)
                    if action_parse == "":
                        self.error_correct[self.current_timestep] = False
                        ml_action = "wait(1)"
                    else:
                        ml_action = self.parse_ml_action_top(action_text_block, True)
                else:
                    ml_action = self.parse_ml_action_top(action_text_block, True)
            elif (
                "[NOTHING]" not in communicate_response
                and "[EMPTY]" not in communicate_response
                and self.role.lower() != "dishwasher"  # P1: Dishwasher 不参与通讯
                and self.get_comm_partner() is not None  # P1: 必须有通讯伙伴才发起通讯
            ):
                turn_count = self._register_comm_turn()
                if turn_count >= self.communication_turn_limit:
                    forced_action = "wait(1)"
                    forced_idx = self._mark_last_call_as_action(
                        forced_action, "communication_turn_limit"
                    )
                    if self.reward_tracker and self.agent_index is not None:
                        self.reward_tracker.register_format_error(
                            self.agent_index, "communication_turn_limit"
                        )
                    if forced_idx is not None:
                        self._forced_action_override = {
                            "call_index": forced_idx,
                            "action": forced_action,
                        }
                        planner_call_index = forced_idx
                    ml_action = forced_action
                else:
                    self._relabel_last_llm_call("communication")
                    planner_call_index = None
                self.planner.add_msg_to_dialog_history(
                    {"role": "talk", "content": communicate_response}
                )
                self.append_conversation_line(self.name, communicate_response)
                # statistic
                self.turn_statistics_dict["statistical_data"]["communication"][
                    self.agent_index
                ]["turn"].append(communicate_response)
                self.turn_statistics_dict["statistical_data"]["communication"][
                    self.agent_index
                ]["token"].append(tokens_num)
                self.turn_statistics_dict["statistical_data"]["communication"][
                    self.agent_index
                ]["call"] += 1
                response = self.communication(communicate_response, state)
            elif ("[NOTHING]" not in action_text_block) and (action_text_block != ""):
                ml_action = self.parse_ml_action_top(action_text_block, True)
            else:
                # No action and no communication content, wait
                ml_action == "wait(1)"
        else:
            temp_list = []
            while not self.action_wait_parse.empty():
                item = self.action_wait_parse.get()
                temp_list.append(item)
            actionable_list = [
                act for act in temp_list if not self._is_collab_action(act)
            ]
            for index, t in enumerate(actionable_list):
                if index == 0:
                    continue
                self.action_wait_parse.put(t)
            if actionable_list:
                print(f"\n\n\n### Already have action sequence in pre-communication:\n {actionable_list}")
                response = f"Action: {';'.join(actionable_list)}"
            else:
                response = ""
            recent_goal_entry = self.current_recent_goal_text
        # when handle several failure, the precious communication history should be jumped
        if self.planner.dialog_history_list_storage == [] and not self.trace:
            self.planner.dialog_history_list_storage = self.planner.dialog_history_list
            # P1: 使用 get_comm_partner() 而非直接引用 self.teammate
            _partner = self.get_comm_partner()
            if _partner is not None:
                _partner.planner.dialog_history_list_storage = (
                    _partner.planner.dialog_history_list
            )
        self.del_dialog_history()
        if ml_action == "":
            ml_action = self.parse_ml_action_top(response, True)
        self.current_ml_action_steps = 0
        failed_message_value = getattr(self, "failed_message", "success")
        if isinstance(failed_message_value, str):
            cleaned_failure = failed_message_value.strip()
            if cleaned_failure.lower() == "success":
                cleaned_failure = ""
        else:
            cleaned_failure = ""
        self.record_history_entry(
                think_output, self.current_recent_goal_text, ml_action, cleaned_failure
        )
        override = getattr(self, "_forced_action_override", None)
        reward_call_index = planner_call_index
        reward_action = ml_action or self._preview_primary_action(action_text_block)
        if override and isinstance(override, dict):
            forced_idx = override.get("call_index")
            if forced_idx is not None:
                reward_call_index = forced_idx
            forced_action = override.get("action")
            if not reward_action and forced_action:
                reward_action = forced_action
        if reward_call_index is not None:
            if not reward_action:
                reward_action = "[EMPTY]"
            self._queue_reward_event(reward_action, reward_call_index, "planner_main")
        self._forced_action_override = None
        return ml_action

    # parse ml_action from  response and self correct
    def parse_ml_action_top(self, response, add_to_queue):
        # add_to_queue: if replace the old action_wait_parse with  new action list.
        print("\n===== Parser =====\n")
        normalized_input = (response or "").strip()
        action_string = normalized_input
        if not action_string.lower().startswith("action"):
            extracted = self.parse_response(response, "action")
            if extracted:
                action_string = extracted.strip()
        if action_string == "":
            print(f"Response does not follow the format rules:{response}")
            return "wait(1)" if add_to_queue else ["wait(1)"]
        action_body = self._strip_action_prefix(action_string)
        action_body = self._sanitize_action_text(action_body)
        lowered_action_string = action_body.lower()
        action_tokens = [
            token.strip()
            for token in lowered_action_string.split(";")
            if token.strip()
        ]
        non_collab_tokens = [
            token for token in action_tokens if not self._is_collab_action(token)
        ]
        ml_action = non_collab_tokens[0] if non_collab_tokens else ""
        if ml_action == "":
            ml_action = "wait(1)"
            non_collab_tokens = ["wait(1)"]
        if add_to_queue:
            while not self.action_wait_parse.empty():
                self.action_wait_parse.get()
            for index, a in enumerate(non_collab_tokens):
                if index > 0:
                    self.action_wait_parse.put(a)
            if "wait" in ml_action:
                self.time_to_wait = self.parse_wait_string(ml_action)
        if "wait" not in ml_action:
            self.planner.add_msg_to_dialog_history(
                {"role": "assistant", "content": ml_action}
            )
        # print(f"{self.name}: {ml_action}")
        return ml_action if add_to_queue else non_collab_tokens

    ##################
    """
	The followings are the Verificator part
	"""
    ##################

    def check_current_ml_action_done(self, state):
        """
        checks if the current ml action is done
        :return: True or False
        """
        if self.current_ml_action is None:
            return True
        action, params = self.parse_params_in_action(self.current_ml_action)
        self.parse_action_params = params
        player = state.players[self.agent_index]
        # pot_states_dict = self.mlam.mdp.get_pot_states(state)
        if "pickup" in self.current_ml_action:
            return (
                player.has_object()
                and player.get_object().name == self.parse_action_params[0]
            )
        elif any(s in self.current_ml_action for s in self.mdp.interact_actions):
            return (
                self.parse_action_params[0]
                in self.mdp.get_utensil_states(state)["cooking"]
                or state.error_message != []
            )
        elif "place" in self.current_ml_action:
            return not player.has_object()
        elif "recpie" in self.current_ml_action:
            return self.order in self.recipe.keys()
        elif "fill" in self.current_ml_action:
            return player.held_object != None and self.order in player.held_object.name
        elif "put" in self.current_ml_action or "place" in self.current_ml_action:
            return not player.has_object()
        elif "deliver" in self.current_ml_action:
            return not player.has_object()
        elif "recipe" in self.current_ml_action:
            if self.actor == "chef":
                chef = self
                if chef.time_to_wait == 0:
                    self.load_recipe()
                else:
                    self.time_to_wait -= 1
            else:
                chef = self.teammate
            return self.order in chef.recipe
        elif "wait" in self.current_ml_action:
            return self.time_to_wait <= 0

    def check_pickup_food_dish(self, food, utensil):
        valide = True
        utensil = re.sub(r"\d+", "", utensil)
        if food in self.mdp.need_dish.keys():
            # check if the utensil is the finally step, which can not directly pick up without dish.
            for utensil_name in self.mdp.recipes.keys():
                u = self.mdp.recipes[utensil_name]
                for f in u.keys():
                    if (food == f) and (utensil == utensil_name):
                        valide = False
        return valide or not (self.mdp.need_dish[food] == 1)

    def validate_current_ml_action(self, state):
        """
        make sure the current_ml_action exists and is valid
        return: success, or failed reason.
        """
        failed_message = "success"
        if self.current_ml_action is None:
            return f"There is no action for {self.actor}.\n"
        player = state.players[self.agent_index]
        has_object = player.has_object()
        self.parse_action, self.parse_action_params = self.parse_params_in_action(
            self.current_ml_action
        )

        def valide_obj(obj):
            return player.has_object() and obj in player.get_object().name

        empty_counter = self.mdp.get_empty_counter_locations(state)
        utensil_state = self.mdp.get_utensil_states(state)
        # Action format check
        format_valide, format_error_message = self.parse_ml_action(
            self.current_ml_action
        )
        if not format_valide:
            return format_error_message
        elif "wait" in format_error_message:
            self.current_ml_action = format_error_message
            return failed_message
        # Assessing the logical Soundness of actions
        if "pickup" in self.parse_action:
            if (self.parse_action_params[1] in self.mdp.utensil_list) and (
                self.parse_action_params[1] in utensil_state["cooking"]
            ):
                failed_message = f"{self.actor} can not pick up {self.parse_action_params[0]} from {self.parse_action_params[1]}. The utensil {self.parse_action_params[1]} is cooking, and you should wait for it is ready.\n"
                return failed_message
            # In case of directly picking up cooked food which need dish in the finally step
            if not self.check_pickup_food_dish(
                self.parse_action_params[0], self.parse_action_params[1]
            ):
                failed_message = f"{self.actor} can not pick up {self.parse_action_params[0]} from {self.parse_action_params[1]}. {self.order} need a dish.\n"
                return failed_message
            if self.parse_action_params[0] in self.mdp.utensil_list:
                failed_message = f"You can not move utensil. It is fixed! If you can not access utensil, you should use counter as transfer station\n"
                return failed_message
            # check if agent access the pickup destination
            motion_goals = self.find_motion_goals(state)
            flag2 = len(motion_goals) == 0
            if flag2:
                # Debug: Print detailed information for pickup from ingredient_dispenser
                if "dispenser" in self.parse_action_params[1] and self.actor == "assistant":
                    player_pos = state.players[self.agent_index].position
                    ingredient_locations = self.mdp.get_ingredient_dispenser_locations()
                    print(f"[DEBUG A{self.agent_index}] pickup({self.parse_action_params[0]}, {self.parse_action_params[1]}) failed:")
                    print(f"  Player position: {player_pos}")
                    print(f"  Ingredient dispenser locations: {ingredient_locations}")
                    print(f"  Motion goals found: {len(motion_goals)}")
                    # Check if ingredient_dispenser is accessible
                    if hasattr(self.mlam, 'ml_action_manager'):
                        ml_manager = self.mlam.ml_action_manager
                        if hasattr(ml_manager, 'joint_motion_planner') and hasattr(ml_manager.joint_motion_planner, 'motion_planner'):
                            mp = ml_manager.joint_motion_planner.motion_planner
                            for loc in ingredient_locations:
                                if loc in mp.motion_goals_for_pos:
                                    print(f"  Location {loc} has {len(mp.motion_goals_for_pos[loc])} motion goals")
                                else:
                                    print(f"  Location {loc} NOT in motion_goals_for_pos!")
                
                failed_message = f"{self.actor} can not reach the destination.Please check if the destination is in your space.Please check if you can interact it. You can let your teammate pick the ingredient from utensil and put in on the counter, then pick up ingredient from the counter.\n"
                if "counter" in self.parse_action_params:
                    failed_message = (
                        f"There is no {self.parse_action_params[0]} on counter {self.actor} can visited."
                        + (
                            "Assistant can directly pick ingredients from dispenser.\n"
                            if self.actor == "assistant"
                            else "If assistant is already preparing ingredient for you,you should wait.\n"
                        )
                    )
                elif (
                    "dispenser" in self.parse_action_params[1] and self.actor == "chef"
                ):
                    failed_message = f"Chef can not directly access dispenser.Chef can gain ingredients with the help of assistant."
            elif has_object:
                failed_message = f"There is object in {self.actor}'s hand, so can not pick other thing.\n"
            # check if the item is in the destination
            elif (
                (self.parse_action_params[1] in utensil_state["empty"])
                or (self.parse_action_params[1] in self.mdp.utensil_list)
                and (
                    self.parse_action_params[0]
                    != self.mdp.utensil_state_dict[self.parse_action_params[1]][
                        "soup"
                    ].state[0]
                )
            ):
                failed_message = f"There is no {self.parse_action_params[0]} in {self.parse_action_params[1]}, please check the item your want to pickup.\n"
            return failed_message + "\n"
        elif self.parse_action == "put_obj_in_utensil":
            # check if the food in utensil is full
            if not self.parse_action_params:
                return "Wrong put_obj_in_utensil() params. It should have 1 params: utensil.\n"
            if self.parse_action_params[0] in utensil_state["cooking"]:
                failed_message = f"{self.actor} can not put obj into  {self.parse_action_params[0]}. The utensil {self.parse_action_params[0]} is cooking, and you should wait for it is ready.\n"
                return failed_message
            if self.parse_action_params[0] in utensil_state["ready"]:
                failed_message = f"{self.actor} can not put obj into  {self.parse_action_params[0]}. The utensil {self.parse_action_params[0]} is ready, and you should pick up it or fill with dish according to the recipe.\n"
                return failed_message
            # if  self.parse_action_params[0] in utensil_state['full']:
            # 	return f"The {self.parse_action_params[0]} is full. You can not put more ingredients in it.\n"

            # If the utensil is empty, add the order name TODO:maybe a bug here:If agent add ingredient not for current order?
            if self.parse_action_params[0] in utensil_state["empty"]:
                self.mdp.utensil_state_dict[self.parse_action_params[0]][
                    "order"
                ] = self.order
            flag2 = len(self.find_motion_goals(state)) == 0
            if flag2:
                failed_message = f"{self.actor} can not reach the {self.parse_action_params[0]}.Please check if you can interact it. You can put ingredient on the counter,then let your teammate pick the ingredient from counter and put it into utensil.\n"
            elif len(empty_counter) == 0:
                failed_message = f"There is no empty counter to place object.\n"
            elif (
                self.parse_action_params[0] in utensil_state["ready"]
                or self.parse_action_params[0] in utensil_state["cooking"]
            ):
                failed_message = f"{self.parse_action_params[0]} is busy. You can not add more ingredients in it.\n"
            elif not player.has_object():
                failed_message = f"There is no object in {self.actor}'s hand, so can not put it on {self.parse_action_params[0]}.\n"
            elif player.get_object().name == "dish":
                failed_message = f"You can not put dish into any utensil. Dish can only be placed on counter.\n"
            return failed_message
        elif self.parse_action == "place_obj_on_counter":
            if not has_object:
                failed_message = f"There is no object in {self.actor}'s hand, so can not place object on counter.\n"
            elif len(empty_counter) == 0:
                failed_message = f"There is no empty counter to place object.\n"
            return failed_message
        elif self.parse_action == "fill_dish_with_food":
            # check if the recipe need dish
            if self.mdp.need_dish[self.order] == 0:
                failed_message = f"{self.order} does not need dish. Please directly pick cooked food from utensil and deliver it to the service location.\n"
            # fill only when utensil is cooking or ready
            elif not (
                self.parse_action_params[0] in utensil_state["ready"]
                or self.parse_action_params[0] in utensil_state["cooking"]
            ):
                failed_message = f"{self.parse_action_params[0]} is not ready or cooking for filled.\n"
            return (
                f"There is no dish in hand.\n"
                if (not valide_obj("dish") and self.mdp.need_dish[self.order] == 1)
                else failed_message
            )
        elif self.parse_action == "deliver_soup":
            has_soup = False
            for s in self.mdp.need_dish.keys():
                if valide_obj(s):
                    has_soup = True
            if not has_soup:
                if has_object:
                    return f"Item in your hand is not finished food. You should put it on counter or right utensil first. Then check whether the order need dish to fill with. If it need a dish, you should ask assistant for a dish and then you fill dish with food from utensil. If not, you can directly pick up food from utensil. Finally you can deliver soup again.\n"
                else:
                    return f"Your hand is empty. You should check whether the order need dish to fill with first. If it need a dish, you should ask assistant for a dish and then you fill dish with food from utensil. If not, you can directly pick up food from utensil. Finally you can deliver soup again.\n"
            else:
                return failed_message

        elif self.parse_action == "wash":
            # P2: wash(water0) — Dishwasher 专用，或任何 Agent 都可以洗碗
            flag2 = len(self.find_motion_goals(state)) == 0
            if flag2:
                failed_message = f"{self.actor} can not reach the {self.parse_action_params[0]}. Please check if the water sink is in your space.\n"
                return failed_message
            # 检查是否有待洗碗任务
            if self.task_pool is not None:
                pending_wash = self.task_pool.get_pending_wash_jobs()
                if len(pending_wash) == 0:
                    failed_message = f"No dirty dishes to wash. Wait for a delivery to generate a wash job.\n"
            return failed_message
        elif any(s in self.parse_action for s in self.mdp.interact_actions):
            flag2 = len(self.find_motion_goals(state)) == 0
            if flag2:
                failed_message = f"{self.actor} can not reach the {self.parse_action_params[0]}..Please check if the utensil is in your space.\n"
                return failed_message
            # only when utensil is partially_full can be operated. when Empty、ready、cooking ,utensil can not be operated.
            if self.parse_action_params[0] in utensil_state["empty"]:
                failed_message = f"Ingredients in {self.parse_action_params[0]} are not enough to begin the operation.\n"
            # check if the utensil is already cooking or ready
            elif (
                self.parse_action_params[0] in utensil_state["ready"]
                or self.parse_action_params[0] in utensil_state["cooking"]
            ):
                failed_message = (
                    f"{self.parse_action_params[0]} is busy. You can not operate it.\n"
                )
            elif has_object:
                failed_message = f"There is object in {self.actor}'s hand, so can not interact with utensil.\n"
            return failed_message
        # the same as pickup(toast,counter)
        elif self.parse_action == "check_recipe":
            if self.actor != "chef":
                failed_message = (
                    f"Assistant can not check recipe.Only chef can do it.\n"
                )
            elif self.order in self.recipe.keys():
                failed_message = f"You have get the recipe before. Look at the <Recipe need to know> part.\n"
            else:
                self.time_to_wait = 2
            return failed_message
        elif self.parse_action == "wait":
            match = re.search(r"\d+", self.current_ml_action)
            number = 1
            if match:
                number = match.group()
            else:
                number = 30
            return (
                failed_message
                if 0 < int(number) <= 20
                else f"Wait time is not valide.\n"
            )
        else:
            raise ValueError("Wrong action")

    def generate_success_feedback(self, state):
        success_feedback = f"### Controller Validation\n {self.name} succeeded at {self.current_ml_action}. \n"
        print(success_feedback)
        if "wait" not in success_feedback:
            self.planner.add_msg_to_dialog_history(
                {
                    "role": "user",
                    "content": f"{self.name} succeeded at {self.current_ml_action}.",
                }
            )

    def del_dialog_history(self):
        del_list = []
        for index, dialog in enumerate(self.planner.dialog_history_list):
            if any(s == dialog["role"] for s in ["talk", "think"]):
                del_list.append(index)
        self.planner.dialog_history_list = [
            value
            for idx, value in enumerate(self.planner.dialog_history_list)
            if idx not in del_list
        ]
        del_list = []
        for index, dialog in enumerate(self.teammate.planner.dialog_history_list):
            if any(s == dialog["role"] for s in ["talk", "think"]):
                del_list.append(index)
        self.teammate.planner.dialog_history_list = [
            value
            for idx, value in enumerate(self.teammate.planner.dialog_history_list)
            if idx not in del_list
        ]

    def generate_failure_feedback(self, action, failed_message):
        failure_feedback = f"Your action {action} raised an error: " + failed_message
        print(f"\n~~~~~~~~ Explainer~~~~~~~~\n{failure_feedback}")
        self.del_dialog_history()
        self.planner.add_msg_to_dialog_history(
            {"role": "failure_explanation", "content": failure_feedback}
        )

    ##################
    """
	The followings are the Controller part almost inherited from GreedyHumanModel class
	"""
    ##################

    def find_shared_counters(self, state, mlam):
        counter_dicts = query_counter_states(self.mdp, state)

        partner_idx = None
        if self.task_pool is not None:
            partner_idx = self.task_pool.get_task_partner(self.agent_index)
        # Fallback for legacy 2-agent mode when task pool is absent
        if partner_idx is None and len(state.players_pos_and_or) == 2:
            partner_idx = 1 - self.agent_index
        if partner_idx is None or partner_idx >= len(state.players_pos_and_or):
            return []

        counter_list = get_intersect_counter(
            state.players_pos_and_or[self.agent_index],
            state.players_pos_and_or[partner_idx],
            self.mdp,
            self.mlam,
        )

        print("counter_list = {}".format(counter_list))
        lis = []
        for i in counter_list:
            if counter_dicts[i] == " ":
                lis.append(i)
        available_plans = mlam.ml_action_manager._get_ml_actions_for_positions(lis)
        return available_plans

    def find_motion_goals(self, state):
        """
        Generates the motion goals for the given medium level action.
        :param state:
        :return:
        """
        am = self.mlam
        motion_goals = []
        player = state.players[self.agent_index]
        pot_states_dict = self.mdp.get_pot_states(state)
        counter_objects = self.mdp.get_counter_objects_dict(
            state, list(self.mdp.terrain_pos_dict["X"])
        )
        # NOTE: In RL mode, the model may accidentally emit multiple actions in one string
        # (e.g., "put_obj_in_utensil(...)\n\npickup(...)\n..."). We always parse the *first*
        # action, and branch based on the parsed function name instead of substring checks
        # on the raw string to avoid mis-routing (e.g., "pickup" appearing later).
        self.parse_action, self.parse_action_params = self.parse_params_in_action(
            self.current_ml_action
        )
        # pickup dish or ingredients
        ml_manager = am.ml_action_manager if hasattr(am, "ml_action_manager") else None
        if ml_manager is None:
            raise AttributeError("MediumLevelPlanner missing ml_action_manager")

        if self.parse_action == "pickup":
            # pickup(obj, destination) requires 2 params; otherwise treat as unreachable/invalid.
            if len(self.parse_action_params) < 2:
                return []
            motion_goals = ml_manager.pickup_obj_actions(
                state,
                self.parse_action_params[0],
                self.parse_action_params[1],
                self.agent_index,
                counter_objects,
            )
        elif self.parse_action == "add_toast":
            motion_goals = ml_manager.pickup_obj_actions(
                state, "toast", "counter", self.agent_index, counter_objects
            )
        elif self.parse_action == "put_obj_in_utensil":
            if not self.parse_action_params:
                return []
            motion_goals = ml_manager.go_to_utensil_actions(
                state, self.parse_action_params[0], self.agent_index
            )
        elif self.parse_action == "place_obj_on_counter":
            motion_goals = self.find_shared_counters(state, self.mlam)
            if len(motion_goals) == 0:
                motion_goals = ml_manager.place_obj_on_counter_actions(state)
        elif self.parse_action == "fill_dish_with_food":
            if not self.parse_action_params:
                return []
            motion_goals = ml_manager.go_to_utensil_actions(
                state, self.parse_action_params[0], self.agent_index
            )
        elif self.parse_action == "deliver_soup":
            motion_goals = ml_manager.deliver_soup_actions()
        elif any(s in (self.parse_action or "") for s in ["cook", "cut", "stir", "bake"]):
            if not self.parse_action_params:
                return []
            motion_goals = ml_manager.go_to_utensil_actions(
                state, self.parse_action_params[0], self.agent_index
            )
        elif self.parse_action == "wait":
            motion_goals = ml_manager.wait_actions(player)
        elif self.parse_action == "wash":
            # wash(water0) — navigate to the water sink utensil
            if not self.parse_action_params:
                return []
            motion_goals = ml_manager.go_to_utensil_actions(
                state, self.parse_action_params[0], self.agent_index
            )
        elif self.parse_action == "go_to":
            # 人类可读的抽象导航格式：go_to(counter(...)) / go_to(utensil) / go_to(serving_location)
            # 允许 A2A 协议和 InstructionRegistry 使用自然描述格式
            # 注意：parse_params_in_action 可能无法正确处理嵌套括号（如 counter(3,1)），
            # 所以我们需要从原始 action 字符串中提取完整参数
            param = ""
            if self.parse_action_params:
                # 如果参数被错误分割（如 ['counter(3', '1']），尝试合并
                param = ",".join(self.parse_action_params).strip()
            else:
                # 如果参数为空，尝试从原始 action 字符串中提取
                import re
                m = re.search(r'go_to\s*\(\s*([^)]+)\s*\)', self.current_ml_action, re.IGNORECASE)
                if m:
                    param = m.group(1).strip()
            
            # 参数匹配（支持部分匹配，即使解析不完美也能工作）
            if "counter" in param.lower() or param == "":
                # go_to(counter(...)) → 找可放置物品的柜台位置（与 place_obj_on_counter 等价）
                motion_goals = self.find_shared_counters(state, self.mlam)
                if not motion_goals:
                    motion_goals = ml_manager.place_obj_on_counter_actions(state)
            elif "serving" in param.lower() or "deliver" in param.lower():
                # go_to(serving_location) → 前往交付点
                motion_goals = ml_manager.deliver_soup_actions()
            elif "dish_dispenser" in param.lower():
                motion_goals = ml_manager.pickup_obj_actions(
                    state, "dish", "dish_dispenser", self.agent_index, counter_objects
                )
            elif "ingredient_dispenser" in param.lower():
                motion_goals = ml_manager.pickup_obj_actions(
                    state, None, "ingredient_dispenser", self.agent_index, counter_objects
                )
            elif param:
                # go_to(pot0) / go_to(oven0) / go_to(water0) 等 → 导航到目标设备
                # 提取设备名称（去除可能的括号和坐标）
                device_name = param.split("(")[0].strip() if "(" in param else param.strip()
                motion_goals = ml_manager.go_to_utensil_actions(
                    state, device_name, self.agent_index
                )
            else:
                motion_goals = ml_manager.wait_actions(player)
        elif self.parse_action == "get_dish":
            # get_dish(dish_dispenser) — 取盘子
            src = self.parse_action_params[0] if self.parse_action_params else "dish_dispenser"
            motion_goals = ml_manager.pickup_obj_actions(
                state, "dish", src, self.agent_index, counter_objects
            )
        else:
            raise ValueError("Invalid action: {}".format(self.current_ml_action))

        # Filter motion_goals by checking if they are reachable from current position
        # Use dynamic pathfinding instead of pre-computed connectivity graph
        # This allows agents to reach positions that may be blocked in the static graph
        # (e.g., agent starting positions that are actually walkable)
        valid_motion_goals = []
        for mg in motion_goals:
            # First check if the goal itself is valid (facing terrain feature, etc.)
            if not self.mlam.mp.is_valid_motion_goal(mg):
                continue
            
            # Use dynamic pathfinding to check if the goal is reachable
            # This is more accurate than pre-computed connectivity graph
            try:
                action_plan, plan_cost = self.real_time_planner(
                    player.pos_and_or, mg, state
                )
                is_valid = (action_plan is not None and plan_cost < np.inf)
            except Exception as e:
                # Fallback to static check if dynamic planning fails
                is_valid = self.mlam.mp.is_valid_motion_start_goal_pair(
                player.pos_and_or, mg
            )
            
            if is_valid:
                valid_motion_goals.append(mg)
            # Debug: Print why motion goals are filtered for pickup from ingredient_dispenser
            elif "pickup" in self.parse_action and "dispenser" in self.parse_action_params[1] and self.actor == "assistant":
                print(f"[DEBUG A{self.agent_index}] Motion goal {mg} filtered:")
                print(f"  Start: {player.pos_and_or}")
                print(f"  Goal: {mg}")
                print(f"  is_valid_motion_goal: {self.mlam.mp.is_valid_motion_goal(mg)}")
                print(f"  positions_are_connected: {self.mlam.mp.positions_are_connected(player.pos_and_or, mg)}")
                try:
                    action_plan, plan_cost = self.real_time_planner(
                        player.pos_and_or, mg, state
                    )
                    print(f"  dynamic_pathfinding: plan_cost={plan_cost}, plan={action_plan}")
                except Exception as e:
                    print(f"  dynamic_pathfinding: failed with {e}")

        return valid_motion_goals

    def choose_motion_goal(self, start_pos_and_or, motion_goals, state=None):
        """
        For each motion goal, consider the optimal motion plan that reaches the desired location.
        Based on the plan's cost, the method chooses a motion goal (either boltzmann rationally
        or rationally), and returns the plan and the corresponding first action on that plan.
        """

        if self.controller_mode == "new":
            (
                chosen_goal,
                chosen_goal_action,
            ) = self.get_lowest_cost_action_and_goal_new(
                start_pos_and_or, motion_goals, state
            )
        else:
            (
                chosen_goal,
                chosen_goal_action,
            ) = self.get_lowest_cost_action_and_goal(start_pos_and_or, motion_goals)
        return chosen_goal, chosen_goal_action

    def get_lowest_cost_action_and_goal(self, start_pos_and_or, motion_goals):
        """
        Chooses motion goal that has the lowest cost action plan.
        Returns the motion goal itself and the first action on the plan.
        """
        min_cost = np.inf
        best_action, best_goal = None, None
        for goal in motion_goals:
            action_plan, _, plan_cost = self.mlam.mp.get_plan(
                start_pos_and_or, goal
            )
            if plan_cost < min_cost:
                best_action = action_plan[0]
                min_cost = plan_cost
                best_goal = goal
        return best_goal, best_action

    def get_lowest_cost_action_and_goal_new(
        self, start_pos_and_or, motion_goals, state
    ):
        """
        Chooses motion goal that has the lowest cost action plan.
        Returns the motion goal itself and the first action on the plan.
        """
        min_cost = np.inf
        best_action, best_goal = None, None
        for goal in motion_goals:
            action_plan, plan_cost = self.real_time_planner(
                start_pos_and_or, goal, state
            )
            if plan_cost < min_cost:
                best_action = action_plan
                min_cost = plan_cost
                best_goal = goal
        if best_action is None:
            # print('\n\n\nBlocking Happend, executing default path\n\n\n')
            # print('current position = {}'.format(start_pos_and_or))
            # print('goal position = {}'.format(motion_goals))
            if np.random.rand() < 0.5:
                return None, Action.STAY
            else:
                return self.get_lowest_cost_action_and_goal(
                    start_pos_and_or, motion_goals
                )
        return best_goal, best_action

    def real_time_planner(self, start_pos_and_or, goal, state):
        terrain_matrix = {
            "matrix": copy.deepcopy(self.mlam.mdp.terrain_mtx),
            "height": len(self.mlam.mdp.terrain_mtx),
            "width": len(self.mlam.mdp.terrain_mtx[0]),
        }
        # Support multiple agents: use first other agent for pathfinding
        # Note: Agents don't collide, so we don't block other agents' positions
        # This allows A1 and A3 to simultaneously stand at (3,1) or (3,2) to access I(2,1) and C(2,2)
        num_players = len(state.players_pos_and_or)
        if num_players <= 2:
            other_pos_and_or = state.players_pos_and_or[1 - self.agent_index]
        else:
            # For multi-agent, just use the first other agent for find_path
            # (find_path will handle that one agent, but we don't block others since agents don't collide)
            other_indices = [i for i in range(num_players) if i != self.agent_index]
            other_pos_and_or = state.players_pos_and_or[other_indices[0]]
        # Pass block_other_agent=False to allow agents to share positions (no collision)
        action_plan, plan_cost = find_path(
            start_pos_and_or, other_pos_and_or, goal, terrain_matrix, block_other_agent=False
        )

        return action_plan, plan_cost
