"""
Pluggable global scheduler for multi-agent / multi-task coordination.

Current responsibilities (no TaskGraph/DAG split):
1) Task Allocation:
   - assign participants for each task
   - assign Chef / Assistant roles
2) Global Monitoring:
   - task list / participants
   - agent busy-idle status
3) Simple Reassignment:
   - task finished
   - agent idle
   - task blocked(timeout)
4) Dishwasher Maintenance:
   - only dishwasher-capable agent handles wash
   - trigger when clean_dish < threshold and dirty_dish > 0
"""

import json
import os
import re
import textwrap
import traceback
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .a2a_protocol.message import A2AMessage, MessageType


@dataclass
class AssignmentRecord:
    task_id: int
    role: str
    agent_index: int
    assigned_at: int
    last_progress_at: int
    last_action: str = ""
    timeout: int = 8
    status: str = "active"  # active / timeout / done


class GlobalSchedulerBase:
    """Base interface for pluggable global schedulers."""

    def __init__(self, enabled: bool = False, timeout_steps: int = 8, verbose: bool = True):
        self.enabled = enabled
        self.timeout_steps = timeout_steps
        self.verbose = verbose
        self.step_logs: List[Dict[str, Any]] = []

    def step(
        self,
        timestep: int,
        state: Any,
        mdp: Any,
        task_pool: Any,
        agents: Sequence[Any],
    ) -> List[A2AMessage]:
        """Run one scheduler tick and return system A2A messages to deliver."""
        return []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "timeout_steps": self.timeout_steps,
            "logs": self.step_logs,
        }


class NoopScheduler(GlobalSchedulerBase):
    """Fallback scheduler: keeps existing local claim logic unchanged."""

    def __init__(self):
        super().__init__(enabled=False, timeout_steps=0, verbose=False)


class CapabilityBusyGlobalScheduler(GlobalSchedulerBase):
    """
    Centralized assignment:
    1) observe global state
    2) allocate task participants (Chef/Assistant)
    3) monitor busy/idle and timeout-block
    4) reassign when needed
    5) maintain dishwasher task by clean/dirty threshold
    6) emit scheduler reference for evaluation
    """

    DEFAULT_CAPABILITIES = {
        "chef": {"cook_task"},
        "assistant": {"cook_task"},
        "dishwasher": {"wash_task"},
    }

    def __init__(
        self,
        enabled: bool = True,
        timeout_steps: int = 8,
        verbose: bool = True,
        role_capabilities: Optional[Dict[str, List[str]]] = None,
        clean_dish_threshold: int = 2,
    ):
        super().__init__(enabled=enabled, timeout_steps=timeout_steps, verbose=verbose)
        self.role_capabilities = {
            k.lower(): set(v) for k, v in (role_capabilities or {}).items()
        }
        self.assignment_records: Dict[Tuple[int, str], AssignmentRecord] = {}
        self.clean_dish_threshold = int(clean_dish_threshold)
        self.scheduler_reference: List[Dict[str, Any]] = []
        self._last_reference_signature: Optional[Tuple] = None

    # ----------------------------
    # Helpers
    # ----------------------------
    def _caps_of(self, role: str) -> set:
        role_l = (role or "").lower()
        if role_l in self.role_capabilities:
            return self.role_capabilities[role_l]
        return self.DEFAULT_CAPABILITIES.get(role_l, set())

    def _agent_busy(self, agent: Any) -> bool:
        action = getattr(agent, "current_ml_action", None)
        steps = int(getattr(agent, "current_ml_action_steps", 0) or 0)
        if not action:
            return False
        action_l = str(action).lower()
        if action_l.startswith("wait("):
            return False
        return steps > 0 or bool(action)

    def _agent_role(self, agent: Any) -> str:
        return str(getattr(agent, "role", "") or "").lower()

    def _current_action(self, agent: Any) -> str:
        return str(getattr(agent, "current_ml_action", "") or "")

    def _emit_message(
        self,
        *,
        to_agent: int,
        msg_type: MessageType,
        content: Dict[str, Any],
        task_id: Optional[int] = None,
        timestep: int = 0,
    ) -> A2AMessage:
        # from_ = -1 means "system/global scheduler"
        msg = A2AMessage(
            type=msg_type,
            from_=-1,
            to=to_agent,
            task_id=task_id,
            content=content,
            metadata={"timestamp": timestep, "priority": "high", "requires_response": False, "role": "global_scheduler"},
        )
        return msg

    def _task_assignment_snapshot(self, task_pool: Any) -> Dict[int, Dict[str, str]]:
        """Return {task_id: {'chef': 'A0', 'assistant': 'A1'}} snapshot."""
        out: Dict[int, Dict[str, str]] = {}
        for t in getattr(task_pool, "tasks", []):
            if t.get("status") == "completed":
                continue
            row = {"chef": "", "assistant": ""}
            for a_idx, role in t.get("roles", {}).items():
                role_l = str(role).lower()
                if role_l in ("chef", "assistant"):
                    row[role_l] = f"A{a_idx}"
            out[int(t["id"])] = row
        return out

    def _append_reference_if_changed(self, timestep: int, task_pool: Any, events: List[str]):
        snap = self._task_assignment_snapshot(task_pool)
        signature = (
            tuple(
                sorted(
                    (tid, info.get("chef", ""), info.get("assistant", ""))
                    for tid, info in snap.items()
                )
            ),
            tuple(events),
        )
        if signature == self._last_reference_signature:
            return
        self._last_reference_signature = signature
        self.scheduler_reference.append(
            {
                "timestep": timestep,
                "assignments": snap,
                "events": list(events),
            }
        )

    # ----------------------------
    # Core scheduler
    # ----------------------------
    def step(self, timestep: int, state: Any, mdp: Any, task_pool: Any, agents: Sequence[Any]) -> List[A2AMessage]:
        if not self.enabled:
            return []

        events: List[str] = []
        outgoing: List[A2AMessage] = []

        # 1) Observe
        agent_obs: Dict[int, Dict[str, Any]] = {}
        for ag in agents:
            idx = int(getattr(ag, "agent_index", -1))
            if idx < 0:
                continue
            role = self._agent_role(ag)
            busy = self._agent_busy(ag)
            task = task_pool.get_agent_current_task(idx) if task_pool else None
            held = ""
            if state is not None and hasattr(state, "players") and idx < len(state.players):
                p = state.players[idx]
                held = p.get_object().name if p.has_object() else ""
            agent_obs[idx] = {
                "role": role,
                "busy": busy,
                "task_id": task["id"] if task else None,
                "action": self._current_action(ag),
                "held": held,
            }

        # 2) Task allocation slots (Chef / Assistant only)
        slots: List[Tuple[int, str, str]] = []  # (task_id, role, skill_type)
        for t in getattr(task_pool, "tasks", []):
            if t.get("status") == "completed":
                continue
            roles = {str(v).lower() for v in t.get("roles", {}).values()}
            if "chef" not in roles:
                slots.append((t["id"], "chef", "cook_task"))
            if "assistant" not in roles:
                slots.append((t["id"], "assistant", "cook_task"))

        # 4) Dishwasher maintenance trigger:
        # clean_dish < threshold and dirty_dish > 0
        clean_dish = int(getattr(mdp, "clean_dishes_available", 0) or 0)
        dirty_dish = len(task_pool.get_pending_wash_jobs()) if hasattr(task_pool, "get_pending_wash_jobs") else 0
        need_wash = clean_dish < self.clean_dish_threshold and dirty_dish > 0
        if need_wash:
            slots.append((-1, "dishwasher", "wash_task"))

        # 3) Assign by capability + idle state
        for task_id, role_need, skill_need in slots:
            candidates = []
            for idx, obs in sorted(agent_obs.items(), key=lambda kv: kv[0]):
                if obs["role"] != role_need:
                    continue
                if skill_need not in self._caps_of(obs["role"]):
                    continue
                # must be idle and not working on another active task
                if obs["busy"]:
                    continue
                # IMPORTANT:
                # Do not rely on the initial snapshot only. Within one scheduler tick,
                # previous slot allocations may already have claimed this agent.
                live_task = task_pool.get_agent_current_task(idx) if task_pool else None
                live_task_id = live_task["id"] if live_task else None
                if live_task_id is not None and live_task_id != task_id:
                    continue
                candidates.append(idx)

            if not candidates:
                continue

            chosen = candidates[0]
            if task_id >= 0:
                ok = task_pool.claim_task(task_id, chosen, role_need)
                if not ok:
                    continue
                events.append(f"assign task={task_id} role={role_need} -> A{chosen}")
                rec_key = (task_id, role_need)
                self.assignment_records[rec_key] = AssignmentRecord(
                    task_id=task_id,
                    role=role_need,
                    agent_index=chosen,
                    assigned_at=timestep,
                    last_progress_at=timestep,
                    timeout=self.timeout_steps,
                )
                # assignment-level notification (informative; action planning remains agent-level)
                outgoing.append(
                    self._emit_message(
                        to_agent=chosen,
                        msg_type=MessageType.INFORM,
                        task_id=task_id,
                        timestep=timestep,
                        content={
                            "action": "task_assignment",
                            "role": role_need,
                            "reason": f"assigned by global scheduler to task {task_id}",
                        },
                    )
                )
            else:
                # wash slot: send explicit wash request to dishwasher
                events.append(f"assign wash -> A{chosen}")
                outgoing.append(
                    self._emit_message(
                        to_agent=chosen,
                        msg_type=MessageType.REQUEST,
                        task_id=None,
                        timestep=timestep,
                        content={
                            "action": "wash(water0)",
                            "reason": "GlobalScheduler assigned wash job",
                        },
                    )
                )

        # 3) Supervision: timeout(blocked) and reassignment
        # If action string has no change for too long -> cancel role assignment.
        for rec_key, rec in list(self.assignment_records.items()):
            if rec.status != "active":
                continue
            idx = rec.agent_index
            obs = agent_obs.get(idx)
            if not obs:
                continue
            cur_action = obs["action"]
            task = task_pool.get_agent_current_task(idx)
            # mark done when task completed or agent moved away to next task
            if not task or task.get("id") != rec.task_id or task.get("status") == "completed":
                rec.status = "done"
                continue
            if cur_action and cur_action != rec.last_action:
                rec.last_action = cur_action
                rec.last_progress_at = timestep
                continue
            if timestep - rec.last_progress_at >= rec.timeout:
                # timeout -> release and allow reassignment next tick
                if hasattr(task_pool, "tasks") and 0 <= rec.task_id < len(task_pool.tasks):
                    t = task_pool.tasks[rec.task_id]
                    if idx in t.get("claimed_by", []):
                        t["claimed_by"].remove(idx)
                    t.get("roles", {}).pop(idx, None)
                    if t.get("status") != "completed":
                        t["status"] = "pending" if not t.get("claimed_by") else "claimed"
                rec.status = "timeout"
                events.append(f"timeout task={rec.task_id} role={rec.role} A{idx} -> release")
                outgoing.append(
                    self._emit_message(
                        to_agent=idx,
                        msg_type=MessageType.INFORM,
                        task_id=rec.task_id,
                        timestep=timestep,
                        content={
                            "action": "cancel_assignment",
                            "reason": "timeout_no_progress",
                        },
                    )
                )

        # 5) Persist step log
        if events or self.verbose:
            self.step_logs.append(
                {
                    "timestep": timestep,
                    "events": events,
                    "agent_obs": agent_obs,
                    "slots": [{"task_id": s[0], "role": s[1], "skill": s[2]} for s in slots],
                    "clean_dish": clean_dish,
                    "dirty_dish": dirty_dish,
                    "clean_dish_threshold": self.clean_dish_threshold,
                    "outgoing_count": len(outgoing),
                }
            )
        # scheduler reference for evaluation (assignment history)
        self._append_reference_if_changed(timestep, task_pool, events)

        return outgoing

    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update(
            {
                "clean_dish_threshold": self.clean_dish_threshold,
                "scheduler_reference": self.scheduler_reference,
            }
        )
        return base

class LLMGlobalScheduler(GlobalSchedulerBase):
    """
    LLM-driven global task scheduler.

    Uses a language model to make task-level / stage-level assignment decisions.
    Falls back automatically to CapabilityBusyGlobalScheduler on any LLM failure.

    Design boundaries
    -----------------
    * Only reads recipe texts (never reference / evaluation files).
    * Decides who works on which task/stage – not atomic actions.
    * Preserves Chef -> Assistant local collaboration within tasks.
    * Timeout supervision runs every step regardless of LLM trigger.
    """

    DEFAULT_CAPABILITIES: Dict[str, set] = {
        "chef": {"cook_task"},
        "assistant": {"cook_task"},
        "dishwasher": {"wash_task"},
    }

    # Resolved at runtime so it works regardless of working directory.
    _RECIPE_DIR: str = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "prompts", "recipe"
    )

    def __init__(
        self,
        enabled: bool = True,
        model: str = "qwen-plus",
        temperature: float = 0.0,
        timeout_steps: int = 8,
        verbose: bool = True,
        clean_dish_threshold: int = 2,
        fallback_to_rule: bool = True,
        role_capabilities: Optional[Dict[str, List[str]]] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_tokens: int = 512,
    ):
        super().__init__(enabled=enabled, timeout_steps=timeout_steps, verbose=verbose)
        self.model = model
        self.temperature = float(temperature)
        self.clean_dish_threshold = int(clean_dish_threshold)
        self.fallback_to_rule = bool(fallback_to_rule)
        self.role_capabilities: Dict[str, set] = {
            k.lower(): set(v) for k, v in (role_capabilities or {}).items()
        }
        self.api_key: str = api_key or os.environ.get("LLM_API_KEY", "")
        self.base_url: Optional[str] = base_url
        self.max_tokens: int = int(max_tokens)

        # Scheduling state
        self.assignment_records: Dict[Tuple[int, str], AssignmentRecord] = {}
        self.scheduler_reference: List[Dict[str, Any]] = []
        self._last_reference_signature: Optional[Tuple] = None
        self.recipe_cache: Dict[str, str] = {}

        # Rule-based fallback
        self._fallback_scheduler = CapabilityBusyGlobalScheduler(
            enabled=True,
            timeout_steps=timeout_steps,
            verbose=verbose,
            role_capabilities=role_capabilities,
            clean_dish_threshold=clean_dish_threshold,
        )

        # Internal tracking for trigger conditions
        self._was_episode_start: bool = True
        self._prev_agent_actions: Dict[int, str] = {}
        self._no_progress_counts: Dict[Tuple[int, str], int] = {}

    # ------------------------------------------------------------------
    # Recipe loading
    # ------------------------------------------------------------------

    def _load_recipe_text(self, task_name: str) -> str:
        """Return recipe text for *task_name*, with caching.

        Matches by stripping the leading digit prefix from filenames
        (e.g. ``1_baked_egg.txt`` -> ``baked_egg``).
        Never reads reference files.
        """
        if task_name in self.recipe_cache:
            return self.recipe_cache[task_name]

        name_key = task_name.lower().replace(" ", "_")
        recipe_dir = self._RECIPE_DIR

        if not os.path.isdir(recipe_dir):
            return f"(recipe directory not found: {recipe_dir})"

        best_match: Optional[str] = None
        best_score: int = -1

        for fname in os.listdir(recipe_dir):
            if not fname.endswith(".txt"):
                continue
            if "ref" in fname.lower():
                continue  # never read reference files
            name_part = re.sub(r"^\d+_", "", fname[:-4]).lower()
            if name_key == name_part:
                best_match = fname
                break
            score = 0
            if name_key in name_part or name_part in name_key:
                score += 50
            score += sum(1 for ch in name_key if ch in name_part)
            if score > best_score:
                best_score = score
                best_match = fname

        if best_match:
            try:
                with open(os.path.join(recipe_dir, best_match), "r", encoding="utf-8") as fh:
                    text = fh.read()
                self.recipe_cache[task_name] = text
                return text
            except OSError:
                pass

        fallback = f"(recipe not found for: {task_name})"
        self.recipe_cache[task_name] = fallback
        return fallback

    # ------------------------------------------------------------------
    # Task stage estimation
    # ------------------------------------------------------------------

    def _estimate_task_stage(
        self, task: Dict[str, Any], agent_obs: Dict[int, Dict[str, Any]]
    ) -> str:
        """Lightweight heuristic task-stage estimator.

        Returns one of: fetch | prep | bake | cook | plate | wash | done
        """
        if str(task.get("status", "")).lower() == "completed":
            return "done"

        task_id = task.get("id", -1)
        task_agents = [obs for obs in agent_obs.values() if obs.get("task_id") == task_id]

        for obs in task_agents:
            action = str(obs.get("action", "")).lower()
            held = str(obs.get("held", "")).lower()

            if any(k in action for k in ("bake", "oven")):
                return "bake"
            if any(k in action for k in ("cook", "pot", "boil")):
                return "cook"
            if any(k in action for k in ("cut", "chop", "slice", "board")):
                return "prep"
            if any(k in action for k in ("plate", "serve", "dish")):
                return "plate"
            if any(k in action for k in ("wash", "water")):
                return "wash"
            if any(k in held for k in ("cooked", "baked", "boiled")):
                return "plate"
            if any(k in held for k in ("sliced", "diced", "chopped")):
                return "cook"

        return "fetch"

    # ------------------------------------------------------------------
    # Trigger condition
    # ------------------------------------------------------------------

    def _should_trigger_scheduler(
        self,
        timestep: int,
        task_pool: Any,
        agents: Sequence[Any],
        agent_obs: Dict[int, Dict[str, Any]],
        clean_dish: int,
        dirty_dish: int,
    ) -> Tuple[bool, str]:
        """Return ``(should_trigger, reason)`` without calling LLM."""

        if self._was_episode_start:
            self._was_episode_start = False
            return True, "episode_start"

        # Unassigned task slots
        for t in getattr(task_pool, "tasks", []):
            if t.get("status") == "completed":
                continue
            roles = {str(v).lower() for v in t.get("roles", {}).values()}
            if "chef" not in roles or "assistant" not in roles:
                return True, f"unassigned_task={t['id']}"

        # Newly idle agent (was active last tick, now idle and unassigned)
        for idx, obs in agent_obs.items():
            if not obs.get("busy") and obs.get("task_id") is None:
                if self._prev_agent_actions.get(idx):
                    return True, f"idle_agent=A{idx}"

        # Blocked / timeout assignments
        for rec_key, rec in self.assignment_records.items():
            if rec.status != "active":
                continue
            obs = agent_obs.get(rec.agent_index, {})
            cur_action = obs.get("action", "")
            if cur_action and cur_action == rec.last_action:
                count = self._no_progress_counts.get(rec_key, 0) + 1
                self._no_progress_counts[rec_key] = count
                if count >= rec.timeout:
                    return True, f"blocked_task={rec.task_id}_agent=A{rec.agent_index}"
            else:
                self._no_progress_counts[rec_key] = 0

        # Clean dish threshold
        if clean_dish < self.clean_dish_threshold and dirty_dish > 0:
            return True, "low_clean_dishes"

        return False, ""

    # ------------------------------------------------------------------
    # State collection
    # ------------------------------------------------------------------

    def _collect_scheduler_state(
        self,
        timestep: int,
        state: Any,
        mdp: Any,
        task_pool: Any,
        agents: Sequence[Any],
        agent_obs: Dict[int, Dict[str, Any]],
        clean_dish: int,
        dirty_dish: int,
    ) -> Dict[str, Any]:
        """Build a structured snapshot of everything the scheduler needs."""

        # Tasks
        tasks_info: List[Dict[str, Any]] = []
        for t in getattr(task_pool, "tasks", []):
            task_id = int(t.get("id", -1))
            task_name = str(t.get("name", t.get("order", "unknown")))
            tasks_info.append(
                {
                    "task_id": task_id,
                    "task_name": task_name,
                    "status": str(t.get("status", "unknown")),
                    "participants": [f"A{i}" for i in t.get("claimed_by", [])],
                    "claimed_by": [f"A{i}" for i in t.get("claimed_by", [])],
                    "roles": {f"A{i}": str(r) for i, r in t.get("roles", {}).items()},
                    "recipe_text": self._load_recipe_text(task_name),
                    "estimated_stage": self._estimate_task_stage(t, agent_obs),
                }
            )

        # Agents
        agents_info: List[Dict[str, Any]] = []
        for ag in agents:
            idx = int(getattr(ag, "agent_index", -1))
            if idx < 0:
                continue
            obs = agent_obs.get(idx, {})
            role = obs.get("role", "")
            distances: Dict[str, Any] = {}
            if (
                state is not None
                and hasattr(state, "players")
                and idx < len(state.players)
            ):
                pos = state.players[idx].position
                for equip in ("pot", "oven", "board", "water"):
                    d = self._distance_to_equipment(pos, equip, mdp)
                    if d is not None:
                        distances[equip] = d

            agents_info.append(
                {
                    "agent": f"A{idx}",
                    "agent_index": idx,
                    "role": role,
                    "busy": obs.get("busy", False),
                    "idle": not obs.get("busy", True),
                    "current_task_id": obs.get("task_id"),
                    "current_action": obs.get("action", ""),
                    "held_object": obs.get("held", ""),
                    "distances": distances,
                    "capabilities": list(self._caps_of(role)),
                }
            )

        # Resources
        resources: Dict[str, Any] = {
            "pot_busy": self._get_equipment_busy(state, "pot"),
            "oven_busy": self._get_equipment_busy(state, "oven"),
            "board_busy": self._get_equipment_busy(state, "board"),
            "clean_dishes": clean_dish,
            "dirty_dishes": dirty_dish,
            "clean_dish_threshold": self.clean_dish_threshold,
        }

        # Blocked signals
        blocked_signals: List[Dict[str, Any]] = []
        for rec_key, rec in self.assignment_records.items():
            if rec.status == "active":
                no_prog = self._no_progress_counts.get(rec_key, 0)
                if no_prog > 0:
                    blocked_signals.append(
                        {
                            "task_id": rec.task_id,
                            "agent": f"A{rec.agent_index}",
                            "role": rec.role,
                            "no_progress_steps": no_prog,
                            "timeout": rec.timeout,
                        }
                    )

        return {
            "timestep": timestep,
            "tasks": tasks_info,
            "agents": agents_info,
            "resources": resources,
            "blocked_signals": blocked_signals,
        }

    # ------------------------------------------------------------------
    # Equipment helpers
    # ------------------------------------------------------------------

    def _caps_of(self, role: str) -> set:
        role_l = (role or "").lower()
        if role_l in self.role_capabilities:
            return self.role_capabilities[role_l]
        return self.DEFAULT_CAPABILITIES.get(role_l, set())

    def _distance_to_equipment(
        self, player_pos: Any, equip_type: str, mdp: Any
    ) -> Optional[int]:
        """Manhattan distance from *player_pos* to nearest tile of *equip_type*."""
        try:
            terrain_mtx = getattr(mdp, "terrain_mtx", None) or getattr(mdp, "grid", None)
            if terrain_mtx is None:
                return None
            equip_char = {"pot": "P", "oven": "O", "board": "B", "water": "W"}.get(
                equip_type.lower()
            )
            if equip_char is None:
                return None
            px, py = int(player_pos[0]), int(player_pos[1])
            min_dist: Optional[int] = None
            for ri, row in enumerate(terrain_mtx):
                for ci, cell in enumerate(row):
                    cell_str = (
                        str(cell.terrain_type)
                        if hasattr(cell, "terrain_type")
                        else str(cell)
                    )
                    if cell_str == equip_char:
                        d = abs(px - ci) + abs(py - ri)
                        if min_dist is None or d < min_dist:
                            min_dist = d
            return min_dist
        except Exception:
            return None

    def _get_equipment_busy(self, state: Any, equip_type: str) -> Optional[bool]:
        """Return True if any object of *equip_type* is currently cooking."""
        try:
            if state is None:
                return None
            for obj in getattr(state, "objects", {}).values():
                name = str(getattr(obj, "name", "")).lower()
                if equip_type in name and getattr(obj, "is_cooking", False):
                    return True
            return False
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_prompt(self, state_dict: Dict[str, Any]) -> str:
        """Assemble the full scheduling prompt sent to the LLM."""

        system_prompt = (
            "You are a global task scheduler for a multi-agent cooking game. "
            "Your job is to assign cooking tasks to agents at the task-level and "
            "stage-level only. You must follow all scheduling rules strictly."
        )

        rules = textwrap.dedent(
            """\
            SCHEDULING RULES:
            1.  Do NOT use any hidden reference, evaluation target, or scoring data.
                Base decisions ONLY on recipe texts, the task list, agent states, and environment state.
            2.  Preserve existing task assignments; avoid frequent task switches.
            3.  Do NOT reassign busy agents unless their task is completed or they are blocked/timed-out.
            4.  Idle agents (busy=false, current_task_id=null) may be assigned to tasks that need participants.
            5.  Chef role is best suited for oven / pot / cook / bake stages.
            6.  Assistant role is best suited for fetch / cut / prep stages.
            7.  Dishwasher role handles wash tasks ONLY; never assign dishwasher to cooking tasks.
            8.  When multiple candidates exist, prefer the agent closest to the relevant equipment.
            9.  If clean_dishes < clean_dish_threshold AND dirty_dishes > 0, assign a wash task to the dishwasher.
            10. Minimize total completion time; maximize agent utilisation.
            11. Avoid assigning too many agents to one task (resource conflict).
            12. Work at task/stage level ONLY. Never output atomic actions (pickup, cook, put_obj_in_utensil, …).
            13. Preserve the Chef → Assistant local collaboration relationship within each task.
            14. Each cooking task should have at most 1 chef and 1 assistant assigned.
            """
        )

        output_format = textwrap.dedent(
            """\
            OUTPUT FORMAT — return ONLY the JSON below, no markdown fences, no extra text:
            {
              "keep_assignments": [
                {"agent": "A0", "task_id": 0}
              ],
              "new_assignments": [
                {"agent": "A3", "task_id": 1, "role": "assistant", "reason": "idle and suitable"}
              ],
              "reassignments": [
                {"agent": "A1", "from_task": 0, "to_task": 1, "role": "assistant", "reason": "task0 completed"}
              ],
              "wash_assignment": {
                "agent": "A2",
                "reason": "clean dishes below threshold"
              },
              "notes": "short explanation"
            }

            Field rules:
            - Return [] for arrays that have no items.
            - Return null for wash_assignment when no wash is needed.
            - agent must be "A0", "A1", etc.
            - role must be exactly one of: "chef" | "assistant" | "dishwasher".
            - task_id must match an existing task_id from the task list above.
            - Do NOT return markdown code fences or any text outside the JSON object.
            """
        )

        state_json = json.dumps(state_dict, ensure_ascii=False, indent=2)

        return (
            f"{system_prompt}\n\n"
            f"{rules}\n"
            f"{output_format}\n"
            f"CURRENT STATE:\n{state_json}"
        )

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str) -> Optional[str]:
        """Generic OpenAI-compatible LLM call.

        Uses the ``openai`` package when available; falls back to ``requests``
        so the scheduler works even without the openai package installed.
        API key, model, and base_url are taken from __init__ / environment.
        """
        try:
            import openai  # type: ignore
        except ImportError:
            if self.verbose:
                print(
                    "[LLMGlobalScheduler] 'openai' package not installed; "
                    "using requests fallback"
                )
            return self._call_llm_requests(prompt)

        try:
            client_kwargs: Dict[str, Any] = {}
            if self.api_key:
                client_kwargs["api_key"] = self.api_key
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            client = openai.OpenAI(**client_kwargs)
            response = client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content
        except Exception as exc:
            if self.verbose:
                print(f"[LLMGlobalScheduler] openai call failed: {exc}")
            return None

    def _call_llm_requests(self, prompt: str) -> Optional[str]:
        """Pure-requests fallback for OpenAI-compatible endpoints."""
        try:
            import requests  # type: ignore

            base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
            url = f"{base}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            if self.verbose:
                print(f"[LLMGlobalScheduler] requests call failed: {exc}")
            return None

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_llm_response(self, raw_text: Optional[str]) -> Optional[Dict[str, Any]]:
        """Parse the LLM JSON response into a validated decision dict.

        Returns ``None`` on any parse / validation failure (caller must fallback).
        """
        if not raw_text:
            return None

        text = raw_text.strip()
        # Strip accidental markdown fences
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text).strip()

        # Attempt full parse, then try to extract embedded JSON object
        data: Any = None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{[\s\S]*\}", text)
            if m:
                try:
                    data = json.loads(m.group())
                except json.JSONDecodeError:
                    pass

        if not isinstance(data, dict):
            if self.verbose:
                print(
                    f"[LLMGlobalScheduler] JSON parse failed. "
                    f"Raw (first 300 chars):\n{raw_text[:300]}"
                )
            return None

        # Validate each field
        def _valid_list(key: str, required_keys: Tuple[str, ...]) -> List[Dict]:
            items = data.get(key) or []
            if not isinstance(items, list):
                return []
            return [i for i in items if isinstance(i, dict) and all(k in i for k in required_keys)]

        result: Dict[str, Any] = {
            "keep_assignments": _valid_list("keep_assignments", ("agent", "task_id")),
            "new_assignments": _valid_list("new_assignments", ("agent", "task_id", "role")),
            "reassignments": _valid_list("reassignments", ("agent", "to_task", "role")),
            "wash_assignment": None,
            "notes": str(data.get("notes", "")),
        }

        wash = data.get("wash_assignment")
        if isinstance(wash, dict) and "agent" in wash:
            result["wash_assignment"] = wash

        return result

    # ------------------------------------------------------------------
    # Apply decision
    # ------------------------------------------------------------------

    @staticmethod
    def _agent_idx(agent_str: str) -> Optional[int]:
        """Parse ``"A3"`` → ``3``."""
        m = re.match(r"[Aa](\d+)", str(agent_str))
        return int(m.group(1)) if m else None

    def _apply_scheduler_decision(
        self,
        decision: Dict[str, Any],
        timestep: int,
        task_pool: Any,
        agent_obs: Dict[int, Dict[str, Any]],
        events: List[str],
    ) -> List[A2AMessage]:
        """Translate the parsed LLM decision into task claims and A2AMessages."""
        outgoing: List[A2AMessage] = []
        valid_task_ids: Set[int] = {
            int(t["id"])
            for t in getattr(task_pool, "tasks", [])
            if t.get("status") != "completed"
        }

        # -- new_assignments --
        for item in decision.get("new_assignments", []):
            idx = self._agent_idx(item.get("agent", ""))
            task_id = int(item.get("task_id", -1))
            role = str(item.get("role", "")).lower()
            reason = str(item.get("reason", "llm assignment"))

            if idx is None or task_id not in valid_task_ids:
                continue
            if role not in ("chef", "assistant", "dishwasher"):
                continue

            if not task_pool.claim_task(task_id, idx, role):
                continue

            events.append(f"[LLM] assign task={task_id} role={role} -> A{idx} ({reason})")
            rec_key = (task_id, role)
            self.assignment_records[rec_key] = AssignmentRecord(
                task_id=task_id,
                role=role,
                agent_index=idx,
                assigned_at=timestep,
                last_progress_at=timestep,
                timeout=self.timeout_steps,
            )
            outgoing.append(
                self._emit_message(
                    to_agent=idx,
                    msg_type=MessageType.INFORM,
                    task_id=task_id,
                    timestep=timestep,
                    content={
                        "action": "task_assignment",
                        "task_id": task_id,
                        "role": role,
                        "reason": reason,
                    },
                )
            )

        # -- reassignments --
        for item in decision.get("reassignments", []):
            idx = self._agent_idx(item.get("agent", ""))
            from_task = item.get("from_task")
            to_task = int(item.get("to_task", -1))
            role = str(item.get("role", "")).lower()
            reason = str(item.get("reason", "llm reassignment"))

            if idx is None or to_task not in valid_task_ids:
                continue
            if role not in ("chef", "assistant", "dishwasher"):
                continue

            # Release from old task first
            if from_task is not None:
                from_task_id = int(from_task)
                if hasattr(task_pool, "tasks") and 0 <= from_task_id < len(task_pool.tasks):
                    t = task_pool.tasks[from_task_id]
                    if idx in t.get("claimed_by", []):
                        t["claimed_by"].remove(idx)
                    t.get("roles", {}).pop(idx, None)
                    if t.get("status") != "completed":
                        t["status"] = "pending" if not t.get("claimed_by") else "claimed"
                events.append(f"[LLM] release task={from_task_id} <- A{idx}")
                outgoing.append(
                    self._emit_message(
                        to_agent=idx,
                        msg_type=MessageType.INFORM,
                        task_id=from_task_id,
                        timestep=timestep,
                        content={
                            "action": "cancel_assignment",
                            "reason": reason,
                        },
                    )
                )

            if not task_pool.claim_task(to_task, idx, role):
                continue

            events.append(
                f"[LLM] reassign A{idx} -> task={to_task} role={role} ({reason})"
            )
            rec_key = (to_task, role)
            self.assignment_records[rec_key] = AssignmentRecord(
                task_id=to_task,
                role=role,
                agent_index=idx,
                assigned_at=timestep,
                last_progress_at=timestep,
                timeout=self.timeout_steps,
            )
            outgoing.append(
                self._emit_message(
                    to_agent=idx,
                    msg_type=MessageType.INFORM,
                    task_id=to_task,
                    timestep=timestep,
                    content={
                        "action": "task_assignment",
                        "task_id": to_task,
                        "role": role,
                        "reason": reason,
                    },
                )
            )

        # -- wash_assignment --
        wash = decision.get("wash_assignment")
        if wash and isinstance(wash, dict):
            idx = self._agent_idx(wash.get("agent", ""))
            reason = str(wash.get("reason", "llm wash assignment"))
            if idx is not None:
                events.append(f"[LLM] assign wash -> A{idx} ({reason})")
                outgoing.append(
                    self._emit_message(
                        to_agent=idx,
                        msg_type=MessageType.REQUEST,
                        task_id=None,
                        timestep=timestep,
                        content={
                            "action": "wash(water0)",
                            "reason": reason,
                        },
                    )
                )

        return outgoing

    # ------------------------------------------------------------------
    # Shared helpers (mirrors CapabilityBusyGlobalScheduler)
    # ------------------------------------------------------------------

    def _emit_message(
        self,
        *,
        to_agent: int,
        msg_type: MessageType,
        content: Dict[str, Any],
        task_id: Optional[int] = None,
        timestep: int = 0,
    ) -> A2AMessage:
        return A2AMessage(
            type=msg_type,
            from_=-1,
            to=to_agent,
            task_id=task_id,
            content=content,
            metadata={
                "timestamp": timestep,
                "priority": "high",
                "requires_response": False,
                "role": "global_scheduler",
            },
        )

    def _task_assignment_snapshot(self, task_pool: Any) -> Dict[int, Dict[str, str]]:
        out: Dict[int, Dict[str, str]] = {}
        for t in getattr(task_pool, "tasks", []):
            if t.get("status") == "completed":
                continue
            row: Dict[str, str] = {"chef": "", "assistant": ""}
            for a_idx, role in t.get("roles", {}).items():
                role_l = str(role).lower()
                if role_l in ("chef", "assistant"):
                    row[role_l] = f"A{a_idx}"
            out[int(t["id"])] = row
        return out

    def _append_reference_if_changed(
        self, timestep: int, task_pool: Any, events: List[str]
    ) -> None:
        snap = self._task_assignment_snapshot(task_pool)
        signature = (
            tuple(
                sorted(
                    (tid, info.get("chef", ""), info.get("assistant", ""))
                    for tid, info in snap.items()
                )
            ),
            tuple(events),
        )
        if signature == self._last_reference_signature:
            return
        self._last_reference_signature = signature
        self.scheduler_reference.append(
            {"timestep": timestep, "assignments": snap, "events": list(events)}
        )

    # ------------------------------------------------------------------
    # Timeout supervision (runs every step, independent of LLM)
    # ------------------------------------------------------------------

    def _supervise_timeouts(
        self,
        timestep: int,
        task_pool: Any,
        agent_obs: Dict[int, Dict[str, Any]],
        events: List[str],
    ) -> List[A2AMessage]:
        outgoing: List[A2AMessage] = []
        for rec_key, rec in list(self.assignment_records.items()):
            if rec.status != "active":
                continue
            idx = rec.agent_index
            obs = agent_obs.get(idx)
            if not obs:
                continue
            cur_action = obs["action"]
            task = task_pool.get_agent_current_task(idx)
            if (
                not task
                or task.get("id") != rec.task_id
                or task.get("status") == "completed"
            ):
                rec.status = "done"
                continue
            if cur_action and cur_action != rec.last_action:
                rec.last_action = cur_action
                rec.last_progress_at = timestep
                self._no_progress_counts[rec_key] = 0
                continue
            if timestep - rec.last_progress_at >= rec.timeout:
                if hasattr(task_pool, "tasks") and 0 <= rec.task_id < len(task_pool.tasks):
                    t = task_pool.tasks[rec.task_id]
                    if idx in t.get("claimed_by", []):
                        t["claimed_by"].remove(idx)
                    t.get("roles", {}).pop(idx, None)
                    if t.get("status") != "completed":
                        t["status"] = "pending" if not t.get("claimed_by") else "claimed"
                rec.status = "timeout"
                self._no_progress_counts.pop(rec_key, None)
                events.append(
                    f"timeout task={rec.task_id} role={rec.role} A{idx} -> release"
                )
                outgoing.append(
                    self._emit_message(
                        to_agent=idx,
                        msg_type=MessageType.INFORM,
                        task_id=rec.task_id,
                        timestep=timestep,
                        content={
                            "action": "cancel_assignment",
                            "reason": "timeout_no_progress",
                        },
                    )
                )
        return outgoing

    # ------------------------------------------------------------------
    # Main step
    # ------------------------------------------------------------------

    def step(
        self,
        timestep: int,
        state: Any,
        mdp: Any,
        task_pool: Any,
        agents: Sequence[Any],
    ) -> List[A2AMessage]:
        if not self.enabled:
            return []

        events: List[str] = []
        outgoing: List[A2AMessage] = []

        # 1) Observe agent states
        agent_obs: Dict[int, Dict[str, Any]] = {}
        for ag in agents:
            idx = int(getattr(ag, "agent_index", -1))
            if idx < 0:
                continue
            role = str(getattr(ag, "role", "") or "").lower()
            action = str(getattr(ag, "current_ml_action", "") or "")
            steps_left = int(getattr(ag, "current_ml_action_steps", 0) or 0)
            busy = bool(
                action and not action.lower().startswith("wait(") and steps_left > 0
            )
            task = task_pool.get_agent_current_task(idx) if task_pool else None
            held = ""
            if state is not None and hasattr(state, "players") and idx < len(state.players):
                p = state.players[idx]
                held = p.get_object().name if p.has_object() else ""
            agent_obs[idx] = {
                "role": role,
                "busy": busy,
                "task_id": task["id"] if task else None,
                "action": action,
                "held": held,
            }

        # 2) Resource counts
        clean_dish = int(getattr(mdp, "clean_dishes_available", 0) or 0)
        dirty_dish = (
            len(task_pool.get_pending_wash_jobs())
            if hasattr(task_pool, "get_pending_wash_jobs")
            else 0
        )

        # 3) Always run timeout supervision (no LLM needed)
        outgoing.extend(self._supervise_timeouts(timestep, task_pool, agent_obs, events))

        # 4) Check trigger condition
        should_trigger, trigger_reason = self._should_trigger_scheduler(
            timestep, task_pool, agents, agent_obs, clean_dish, dirty_dish
        )

        llm_used = False
        if should_trigger:
            try:
                state_dict = self._collect_scheduler_state(
                    timestep, state, mdp, task_pool, agents,
                    agent_obs, clean_dish, dirty_dish,
                )
                prompt = self._build_prompt(state_dict)
                raw_response = self._call_llm(prompt)
                decision = self._parse_llm_response(raw_response)

                if decision is None:
                    raise ValueError("LLM response could not be parsed")

                llm_msgs = self._apply_scheduler_decision(
                    decision, timestep, task_pool, agent_obs, events
                )
                outgoing.extend(llm_msgs)
                llm_used = True
                events.append(
                    f"[LLM] trigger={trigger_reason} "
                    f"notes={decision.get('notes', '')[:80]}"
                )

            except Exception as exc:
                if self.verbose:
                    print(
                        f"[LLMGlobalScheduler] error at t={timestep}: {exc}\n"
                        + traceback.format_exc()
                    )
                if self.fallback_to_rule:
                    events.append(
                        f"[LLM->fallback] trigger={trigger_reason} "
                        f"error={str(exc)[:60]}"
                    )
                    fallback_msgs = self._fallback_scheduler.step(
                        timestep, state, mdp, task_pool, agents
                    )
                    outgoing.extend(fallback_msgs)

        # 5) Update previous-action tracker
        for idx, obs in agent_obs.items():
            self._prev_agent_actions[idx] = obs.get("action", "")

        # 6) Persist step log
        if events or self.verbose:
            self.step_logs.append(
                {
                    "timestep": timestep,
                    "trigger": trigger_reason if should_trigger else None,
                    "llm_used": llm_used,
                    "events": events,
                    "agent_obs": agent_obs,
                    "clean_dish": clean_dish,
                    "dirty_dish": dirty_dish,
                    "clean_dish_threshold": self.clean_dish_threshold,
                    "outgoing_count": len(outgoing),
                }
            )

        # 7) Reference snapshot for evaluation
        self._append_reference_if_changed(timestep, task_pool, events)

        return outgoing

    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update(
            {
                "model": self.model,
                "clean_dish_threshold": self.clean_dish_threshold,
                "scheduler_reference": self.scheduler_reference,
            }
        )
        return base


def build_scheduler_from_config(cfg: Optional[Dict[str, Any]]) -> GlobalSchedulerBase:
    """Factory for pluggable scheduler."""
    cfg = cfg or {}
    enabled = bool(cfg.get("enabled", False))
    if not enabled:
        return NoopScheduler()

    mode = str(cfg.get("mode", "capability_busy")).lower()
    timeout_steps = int(cfg.get("timeout_steps", 8))
    verbose = bool(cfg.get("verbose", True))
    role_caps = cfg.get("role_capabilities", None)
    clean_dish_threshold = int(cfg.get("clean_dish_threshold", 2))

    if mode in ("capability_busy", "global", "heuristic"):
        return CapabilityBusyGlobalScheduler(
            enabled=True,
            timeout_steps=timeout_steps,
            verbose=verbose,
            role_capabilities=role_caps,
            clean_dish_threshold=clean_dish_threshold,
        )

    if mode in ("llm", "prompt_scheduler", "recipe_llm"):
        model = str(cfg.get("model", "qwen-plus"))
        temperature = float(cfg.get("temperature", 0.0))
        fallback_to_rule = bool(cfg.get("fallback_to_rule", True))
        api_key = cfg.get("api_key") or None
        base_url = cfg.get("base_url") or None
        max_tokens = int(cfg.get("max_tokens", 512))
        return LLMGlobalScheduler(
            enabled=True,
            model=model,
            temperature=temperature,
            timeout_steps=timeout_steps,
            verbose=verbose,
            clean_dish_threshold=clean_dish_threshold,
            fallback_to_rule=fallback_to_rule,
            role_capabilities=role_caps,
            api_key=api_key,
            base_url=base_url,
            max_tokens=max_tokens,
        )

    return NoopScheduler()

