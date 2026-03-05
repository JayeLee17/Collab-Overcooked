# 多任务 + 多Agent认领机制 — 改进设计文档

## 一、当前系统架构分析

### 1.1 地图布局
```
XXXXXPX      (P=锅, O=烤箱, C=砧板, B=搅拌机, W=水槽)
X3I4X1X      (数字=玩家起始位, I=食材取放, S=上菜口, D=盘子)
W C2X X
X D X5O
XXXBXSX
```

### 1.2 当前角色分配（default.yaml）
| Agent索引 | 地图位号 | 当前配置角色 | 设计角色 |
|-----------|---------|-------------|---------|
| agent_0 (P0) | 1号位（右上区） | Chef | **Chef** |
| agent_1 (P1) | 4号位（左上区） | Assistant | **Assistant** |
| agent_2 (P2) | 2号位（中间区） | Assistant | **Assistant** |
| agent_3 (P3) | 3号位（左上区） | Assistant | **Dishwasher** |
| agent_4 (P4) | 5号位（右下区） | Chef | **Chef** |

### 1.3 当前系统的核心问题

#### ❌ 问题1：只有2个角色（Chef / Assistant）
- `self.name = "Chef" if self.actor == "chef" else "Assistant"` → 所有非chef都叫"Assistant"
- 提示词模板只区分 `chef_skill.txt` 和 `assistant_skill.txt`
- `environment_rule.txt` 写死 "two players (the chef and assistant)"

#### ❌ 问题2：Observation只看到1个队友
- `generate_state_prompt()` 中 `teammate = state.players[1 - self.agent_index]` → 只能看到1个队友
- `generate_layout_prompt()` 只输出 "Chef space:" 和 "Assistant space:" 两个区域

#### ❌ 问题3：通讯是1对1的
- `communication()` 方法只在 `self` ↔ `self.teammate` 之间来回对话
- 无法实现多Agent之间的消息广播或定向通讯

#### ❌ 问题4：工具分配是按 Chef/Assistant 二分的
- `build_access_utensil()` 硬编码 `index_list = [0, 1]`，只为 `utensil_list_chef` 和 `utensil_list_assist` 分配
- 不支持5个agent各自有不同的可达工具列表

#### ❌ 问题5：无任务管理机制
- 当前是 `one_task_mode = True` 单任务模式
- 没有任务池、任务认领、任务完成检测的概念

---

## 二、目标设计

### 2.1 角色体系
```
Chef (厨师)       → agent_0, agent_4 → 负责烹饪（锅/烤箱）和上菜
Assistant (助手)  → agent_1, agent_2 → 负责取材、切菜、搅拌、传递
Dishwasher (洗碗) → agent_3          → 负责在W(水槽)清洗盘子
```

### 2.2 多任务发布
- 启动时同时发布 N 个订单（如3个 boiled_egg）
- 所有订单进入**公共任务池** `TaskPool`
- 每个任务有状态：`pending` → `claimed` → `in_progress` → `completed`

### 2.3 任务认领机制
- 每个Agent有属性 `claimed_task`: 当前认领的任务（None = 空闲）
- **认领规则**：
  - Agent只能在 `claimed_task == None` 时认领新任务
  - 认领按 agent_index 顺序优先（低序号优先）
  - 同角色的Agent不会重复认领同一个任务
  - Dishwasher不认领烹饪任务，只响应洗碗需求
- 当手头任务完成后，`claimed_task` 置空，才能认领下一个

---

## 三、需要修改的文件和具体改动

### 3.1 新增：任务管理模块 ⭐
**文件**: `collab_overcooked/task_manager.py`（新建）

```python
class TaskPool:
    """公共任务池，管理所有订单的生命周期"""
    
    def __init__(self, order_list):
        self.tasks = []  # [{"id": 0, "order": "boiled_egg", "status": "pending", "claimed_by": None}, ...]
        for i, order in enumerate(order_list):
            self.tasks.append({
                "id": i,
                "order": order,
                "status": "pending",      # pending / claimed / in_progress / completed
                "claimed_by": None,       # agent_index
                "start_time": None,
                "complete_time": None,
            })
    
    def get_available_tasks(self, role=None):
        """获取可认领的任务列表"""
        return [t for t in self.tasks if t["status"] == "pending"]
    
    def claim_task(self, task_id, agent_index):
        """Agent认领任务"""
        task = self.tasks[task_id]
        if task["status"] != "pending":
            return False
        task["status"] = "claimed"
        task["claimed_by"] = agent_index
        return True
    
    def start_task(self, task_id):
        task = self.tasks[task_id]
        task["status"] = "in_progress"
    
    def complete_task(self, task_id, timestep):
        task = self.tasks[task_id]
        task["status"] = "completed"
        task["complete_time"] = timestep
    
    def get_agent_current_task(self, agent_index):
        """获取Agent当前认领的任务"""
        for t in self.tasks:
            if t["claimed_by"] == agent_index and t["status"] in ("claimed", "in_progress"):
                return t
        return None
    
    def all_done(self):
        return all(t["status"] == "completed" for t in self.tasks)
```

**核心逻辑**：
- 在 `main.py` 启动时创建 `TaskPool`
- 传递给每个 Agent
- Agent 在 `action()` 开头检查是否需要认领/切换任务

---

### 3.2 修改：`collab_overcooked/agents/collab.py` ⭐⭐⭐

这是改动量最大的文件，需要修改以下部分：

#### (a) 角色系统扩展
**当前**：`self.name = "Chef" if self.actor == "chef" else "Assistant"`  
**改为**：支持 Chef / Assistant / Dishwasher 三种角色

```python
# __init__ 中
self.actor = actor  # "chef" / "assistant" / "dishwasher"
ROLE_NAMES = {"chef": "Chef", "assistant": "Assistant", "dishwasher": "Dishwasher"}
self.name = ROLE_NAMES.get(self.actor, "Agent")
```

#### (b) Observation多Agent扩展
**当前（第449行）**：`teammate = state.players[1 - self.agent_index]` → 只看1个队友  
**改为**：遍历所有队友，生成多人状态描述

```python
# generate_state_prompt() 中
# 替换单个 teammate 的逻辑为遍历 self.teammates
agent_state_parts = []
for tm in self.teammates:
    tm_player = state.players[tm.agent_index]
    tm_object = tm_player.held_object.name if tm_player.held_object else "nothing"
    tm_action = tm.current_ml_action or "[EMPTY]"
    agent_state_parts.append(
        f"<{tm.name}(P{tm.agent_index})> holds {tm_object}. "
        f"Current action: [{tm_action}]"
    )
teammate_state_prompt = " ".join(agent_state_parts)
```

#### (c) Layout描述多区域扩展
**当前（第420-438行）**：只输出 "Chef space:" 和 "Assistant space:" 两行  
**改为**：按每个Agent的可达工具列表描述

```python
# generate_layout_prompt() 中
# 不再只分 Chef/Assistant 两个空间，而是描述 self 和每个 teammate 的可达工具
layout_prompt = f"My({self.name}) accessible utensils: {self.accessible_utensils}\n"
for tm in self.teammates:
    layout_prompt += f"{tm.name}(P{tm.agent_index}) accessible utensils: {tm.accessible_utensils}\n"
```

#### (d) build_access_utensil 支持多Agent
**当前（第695-716行）**：硬编码只为 index 0 和 1 计算可达工具  
**改为**：为所有 agent 计算各自的可达工具列表

```python
def build_access_utensil(self, state):
    # 为每个agent计算可达工具，存储在 mdp.utensil_access[agent_index] 中
    if not hasattr(self.mdp, 'utensil_access'):
        self.mdp.utensil_access = {}
    
    if self.agent_index not in self.mdp.utensil_access:
        accessible = []
        player = state.players[self.agent_index]
        for utensil in self.mdp.utensil_list:
            motion_goals = self.mlam.ml_action_manager.go_to_utensil_actions(
                state, utensil, self.agent_index
            )
            motion_goals = [mg for mg in motion_goals 
                          if self.mlam.mp.is_valid_motion_start_goal_pair(player.pos_and_or, mg)]
            if motion_goals:
                accessible.append(utensil)
        self.mdp.utensil_access[self.agent_index] = accessible
        self.accessible_utensils = accessible
```

#### (e) 通讯机制扩展
**当前**：`communication()` 只支持 self ↔ self.teammate 一对一  
**改为**：基于任务的定向通讯

```
设计方案：
1. 同一任务的 Chef 和 Assistant 之间通讯（协作完成一道菜）
2. 广播机制：某个Agent完成任务后通知其他Agent
3. Dishwasher 独立工作，不参与烹饪通讯
```

#### (f) 任务认领逻辑（新增到 action() 方法）
```python
def action(self, state):
    # 1. 检查当前任务状态
    current_task = self.task_pool.get_agent_current_task(self.agent_index)
    
    if current_task is None:
        # 没有任务 → 尝试认领
        available = self.task_pool.get_available_tasks()
        if available and self.actor != "dishwasher":
            task = available[0]  # 按序认领
            self.task_pool.claim_task(task["id"], self.agent_index)
            self.order = task["order"]
            current_task = task
    
    if current_task:
        self.order = current_task["order"]
    
    # 2. 后续正常的 action 流程...
```

---

### 3.3 修改：`collab_overcooked/main.py`

#### (a) 创建TaskPool并传递给Agent
```python
from .task_manager import TaskPool

# 创建多任务订单
if not mdp.one_task_mode:
    order_list = mdp.start_order_list  # 如 ["boiled_egg", "boiled_egg", "boiled_egg"]
    task_pool = TaskPool(order_list)
else:
    task_pool = TaskPool([variant['order']])

# 创建Agent时传入 task_pool
agent = make_agent_from_config(agent_config, mdp, layout, ...)
agent.task_pool = task_pool
```

#### (b) 修改agent创建时的角色映射
```python
# 角色映射（从 YAML 配置读取）
role = agent_config.get("role", "Chef")
role_to_actor = {
    "Chef": "chef",
    "Assistant": "assistant", 
    "Dishwasher": "dishwasher"
}
actor = role_to_actor.get(role, "assistant")
```

#### (c) 多任务模式下的循环逻辑
```python
# main loop 中增加检查
if task_pool.all_done():
    print("所有任务完成！")
    break
```

---

### 3.4 修改：`configs/default.yaml`

```yaml
environment:
  horizon: 40   # 多任务需要更长时间
  order: "boiled_egg"
  layout: "multi_agent_map"
  num_tasks: 3  # 同时发布的任务数量（新增）

agents:
  num_agents: 5
  agent_0:
    role: "Chef"       # P0 - 1号位（右上区，靠近锅/烤箱）
  agent_1:
    role: "Assistant"   # P1 - 4号位（左上区，靠近食材/砧板）
  agent_2:
    role: "Assistant"   # P2 - 2号位（中间区，靠近砧板/水槽）
  agent_3:
    role: "Dishwasher"  # P3 - 3号位（左上区，靠近水槽W）
  agent_4:
    role: "Chef"        # P4 - 5号位（右下区，靠近烤箱/上菜口）
```

---

### 3.5 修改：提示词模板 (`prompts/gpt/`)

#### (a) `environment_rule.txt`
```diff
- The Overcooked_AI game requires two players (the chef and assistant)
+ The Overcooked_AI game requires multiple players with different roles
+   (Chef, Assistant, Dishwasher) to work together
```

#### (b) 新增 `dishwasher_skill.txt`
```
**Skill**:
def wash_dishes():
    """ Go to water sink (W) and wash dirty dishes """
    return

def wait(num):
    if isinstance(num, int) and 0 < num <= 20:
        return
```

#### (c) `communication_rule.txt`
```diff
- Agent State: what you and your teammate are holding or doing.
+ Agent State: what you and all teammates are holding or doing.
```

---

### 3.6 修改：`overcooked_mdp.py`

#### 多任务订单发布
```python
# 在 get_state_transition 或 resolve_interacts 中
# 当一个任务完成后，不再移除订单（one_task_mode=False时）
# 而是标记为已完成，让 TaskPool 管理
```

---

## 四、执行流程概览

```
┌─────────────────────────────────────────────────────────────────┐
│                        main.py 主循环                            │
│                                                                  │
│  1. 创建 MDP + 环境                                              │
│  2. 创建 TaskPool（含3个boiled_egg任务）                          │
│  3. 创建5个Agent，各自绑定角色和TaskPool                          │
│  4. for t in range(horizon):                                     │
│     ├─ state = env.state                                         │
│     ├─ team.joint_action(state)                                  │
│     │   ├─ P0(Chef):    检查任务→认领task_0→规划→执行             │
│     │   ├─ P1(Asst):    检查任务→认领task_0→与P0通讯→执行         │
│     │   ├─ P2(Asst):    检查任务→认领task_1→独立规划→执行         │
│     │   ├─ P3(Dishwash): 检查洗碗队列→执行洗碗→wait              │
│     │   └─ P4(Chef):    检查任务→认领task_1→规划→执行             │
│     ├─ env.step(actions)                                         │
│     ├─ 更新 TaskPool 状态                                        │
│     └─ if task_pool.all_done(): break                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 五、Agent之间的协作模式

### 5.1 Chef + Assistant 协作（完成烹饪任务）
```
Task: boiled_egg (任务池中的一个)
  
  Agent_1(Assistant) ←认领→ Task_0
  Agent_0(Chef)     ←认领→ Task_0
  
  流程:
  1. Assistant 从 I(食材柜) 拿 egg
  2. Assistant 放 egg 到 P(锅)
  3. Chef 启动 cook(pot0)
  4. 等待烹饪完成
  5. Chef 拿 dish (如果需要) + 取成品
  6. Chef deliver_soup() → 上菜口 S
  7. Task_0 标记完成 → 触发洗碗任务
  8. 双方释放任务 → 可认领新任务
```

### 5.2 Dishwasher 独立工作
```
  Agent_3(Dishwasher):
  1. 监听 delivery 事件 → washing_jobs 队列增加
  2. 在 W(水槽) 执行 wash_dishes()
  3. wash_time 步后 → clean_dishes_available += 1
  4. 重复等待下一个洗碗任务
```

### 5.3 同角色Agent的任务分配
```
  同时发布3个 boiled_egg:
  
  Task_0: P0(Chef) + P1(Assistant) → 协作完成
  Task_1: P4(Chef) + P2(Assistant) → 协作完成
  Task_2: 等待 Task_0 或 Task_1 完成后，空闲的Agent认领
  
  P3(Dishwasher): 持续处理洗碗
```

---

## 六、实现优先级

| 优先级 | 任务 | 复杂度 | 涉及文件 |
|--------|------|--------|---------|
| 🔴 P0 | 新建 TaskPool 任务管理器 | 低 | `task_manager.py`（新建） |
| 🔴 P0 | 角色系统扩展（3种角色） | 中 | `collab.py`, `default.yaml` |
| 🔴 P0 | build_access_utensil 多Agent | 中 | `collab.py` |
| 🔴 P0 | Observation 显示所有队友 | 高 | `collab.py` |
| 🟡 P1 | 任务认领逻辑集成到 action() | 高 | `collab.py`, `main.py` |
| 🟡 P1 | 提示词模板适配多角色 | 中 | `prompts/gpt/*.txt` |
| 🟡 P1 | Chef-Assistant 配对通讯 | 高 | `collab.py` |
| 🟢 P2 | Dishwasher 自动化工作流 | 中 | `collab.py`, `overcooked_mdp.py` |
| 🟢 P2 | main.py 多任务循环和统计 | 中 | `main.py` |
| 🟢 P2 | 动态任务重分配（空闲认领） | 中 | `task_manager.py`, `collab.py` |

---

## 七、关键设计决策（已确认 ✅）

### Q1: Chef和Assistant如何配对？ → ✅ 方案A：按认领同一任务自动配对
- 两个Agent认领同一个task_id → 自动成为协作对
- 通讯只发生在同一任务的Agent之间
- 任务完成后配对关系解除，各自认领新任务时可能形成新配对

### Q2: 谁先认领任务？ → ✅ 同区域Agent按agent_index顺序认领
- `action()` 在 `joint_action()` 中按 agent_index 顺序调用
- 同区域的Agent按index从小到大优先认领
- 例如：P0(Chef)先认领task_0 → P1(Asst)发现task_0已有Chef → 自动跟进配对

### Q3: Dishwasher 是否需要 LLM？ → ✅ 需要LLM
- Dishwasher 仍然使用 LLM 驱动
- 给 Dishwasher 提供专用提示词 `dishwasher_skill.txt`
- LLM 自主判断何时去水槽洗碗、何时等待
- 好处：保持所有Agent统一架构，日志输出格式一致

### Q4: 通讯范围？ → ✅ 分层通讯机制
**层级1 — 同任务协作通讯**：
- 同任务的 Chef ↔ Assistant：正常对话（request/ack/seek/deny）
- 这是主要通讯通道

**层级2 — 工具冲突协调通讯**：
- 跨任务的Agent之间默认不通讯（各干各的）
- **但是**：当两个Agent需要使用同一工具时（如 P2 和 P4 都需要搅拌机），触发冲突协调通讯
- 冲突协调规则：按 agent_index 小者优先使用，大者等待
- 冲突通讯格式示例：
  ```
  P2(Assistant): Collab(notify(P4, "I'm using blender0, please wait"))
  P4(Chef): Collab(ack(P2, "OK, I'll wait for blender0"))
  ```

**层级3 — Dishwasher 独立工作**：
- Dishwasher 不参与烹饪对话
- 但可以接收 delivery 事件通知（系统自动触发，不走 LLM 通讯）

---

## 八、工具冲突协调详细设计

### 8.1 冲突检测
在每个 timestep 开始时，`TaskPool` 检查所有 `in_progress` 任务中Agent的目标工具：

```python
class TaskPool:
    def detect_utensil_conflicts(self, agent_plans):
        """检测工具使用冲突
        agent_plans: {agent_index: target_utensil_name}
        返回: [(utensil, [agent_idx_1, agent_idx_2, ...]), ...]
        """
        utensil_to_agents = defaultdict(list)
        for agent_idx, utensil in agent_plans.items():
            if utensil:
                utensil_to_agents[utensil].append(agent_idx)
        return [(u, agents) for u, agents in utensil_to_agents.items() if len(agents) > 1]
```

### 8.2 冲突解决策略
```
规则：agent_index 小的优先使用
  
例：P2(index=2) 和 P4(index=4) 都要用 blender0
  → P2 优先操作 blender0
  → P4 被告知等待（LLM 收到通知后决定 wait(N) 或先做其他事）
  → P2 完成后，P4 的 Observation 中显示 blender0 可用
```

### 8.3 冲突感知 Prompt 增强
在工具冲突发生时，给被等待的Agent增加 Observation：
```
[Utensil Conflict] blender0 is currently being used by P2(Assistant). 
You need to wait or perform other tasks first.
```

### 8.4 共享工具列表（按地图分析）
```
地图: XXXXXPX
      X3I4X1X
      W C2X X
      X D X5O
      XXXBXSX

可能的工具冲突点：
- chopping_board(C): P1(Asst), P2(Asst), P3(Dishwash) 都可达 → 潜在冲突
- blender(B): P1(Asst), P3(Dishwash) 可达 → 潜在冲突
- pot(P): P0(Chef) 可达 → 无冲突（单人独占）
- oven(O): P4(Chef) 可达 → 无冲突（单人独占）
- water(W): P2(Asst), P3(Dishwash) 可达 → Dishwasher 优先
```
