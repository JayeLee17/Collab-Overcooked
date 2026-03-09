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

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
    return NoopScheduler()

