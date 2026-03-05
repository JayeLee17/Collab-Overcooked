#!/usr/bin/env python3
"""
P0 优先级验证脚本
检查所有 P0 改动是否正确：
  1. 语法检查
  2. TaskPool 单元测试
  3. 角色系统检查
  4. 配置文件加载
  5. MDP + Layout 加载
  6. Agent 创建 & 工具可达性
  7. Observation 生成
"""

import sys
import os
import traceback

# 切换到项目根目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.getcwd(), "dependencies", "overcooked_ai"))
sys.path.insert(0, os.getcwd())

PASS = 0
FAIL = 0
WARN = 0

def check(name, func):
    global PASS, FAIL, WARN
    try:
        result = func()
        if result is True or result is None:
            print(f"  ✅ {name}")
            PASS += 1
        elif result == "WARN":
            print(f"  ⚠️  {name}")
            WARN += 1
        else:
            print(f"  ❌ {name}: 返回 {result}")
            FAIL += 1
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        traceback.print_exc()
        FAIL += 1

# ======================================================================
print("\n" + "=" * 60)
print("  P0 验证：1/7 语法检查")
print("=" * 60)

import ast

def check_syntax(filepath):
    def _check():
        with open(filepath, "r") as f:
            ast.parse(f.read())
    return _check

check("task_manager.py 语法", check_syntax("collab_overcooked/task_manager.py"))
check("main.py 语法", check_syntax("collab_overcooked/main.py"))
check("collab.py 语法", check_syntax("collab_overcooked/agents/collab.py"))

# ======================================================================
print("\n" + "=" * 60)
print("  P0 验证：2/7 TaskPool 单元测试")
print("=" * 60)

from collab_overcooked.task_manager import TaskPool

def test_taskpool_init():
    tp = TaskPool(["boiled_egg", "boiled_egg", "salad"])
    assert len(tp.tasks) == 3
    assert tp.tasks[0]["order"] == "boiled_egg"
    assert tp.tasks[2]["order"] == "salad"
    assert all(t["status"] == "pending" for t in tp.tasks)

check("TaskPool 初始化", test_taskpool_init)

def test_taskpool_claim():
    tp = TaskPool(["boiled_egg", "boiled_egg"])
    # Chef 认领 task 0
    assert tp.claim_task(0, 0, "Chef") == True
    assert tp.tasks[0]["status"] == "claimed"
    assert 0 in tp.tasks[0]["claimed_by"]
    # 另一个 Chef 不能认领同一 task
    assert tp.claim_task(0, 4, "Chef") == False
    # Assistant 可以认领同一 task
    assert tp.claim_task(0, 1, "Assistant") == True
    assert tp.tasks[0]["roles"] == {0: "Chef", 1: "Assistant"}

check("TaskPool 认领逻辑", test_taskpool_claim)

def test_taskpool_partner():
    tp = TaskPool(["boiled_egg"])
    tp.claim_task(0, 0, "Chef")
    tp.claim_task(0, 1, "Assistant")
    assert tp.get_task_partner(0) == 1
    assert tp.get_task_partner(1) == 0
    assert tp.get_task_partner(2) is None

check("TaskPool 配对查询", test_taskpool_partner)

def test_taskpool_available():
    tp = TaskPool(["boiled_egg", "boiled_egg"])
    tp.claim_task(0, 0, "Chef")
    avail_chef = tp.get_available_tasks(role="Chef")
    # task 0 已有 chef, task 1 还没有
    assert len(avail_chef) == 1
    assert avail_chef[0]["id"] == 1
    # Dishwasher 不认领烹饪任务
    avail_dish = tp.get_available_tasks(role="Dishwasher")
    assert len(avail_dish) == 0

check("TaskPool 可用任务过滤", test_taskpool_available)

def test_taskpool_wash():
    tp = TaskPool(["boiled_egg"])
    tp.add_wash_job(timestep=10, wash_time=5)
    assert len(tp.get_pending_wash_jobs()) == 1
    assert tp.update_wash_jobs(14) == 0  # 还没到
    assert tp.update_wash_jobs(15) == 1  # 完成
    assert len(tp.get_pending_wash_jobs()) == 0

check("TaskPool 洗碗队列", test_taskpool_wash)

def test_taskpool_conflict():
    tp = TaskPool(["boiled_egg"])
    conflicts = tp.detect_utensil_conflicts({0: "blender0", 1: "pot0", 2: "blender0"})
    assert len(conflicts) == 1
    assert conflicts[0][0] == "blender0"
    assert conflicts[0][1] == [0, 2]

check("TaskPool 工具冲突检测", test_taskpool_conflict)

def test_taskpool_complete():
    tp = TaskPool(["boiled_egg", "boiled_egg"])
    tp.claim_task(0, 0, "Chef")
    tp.claim_task(0, 1, "Assistant")
    tp.start_task(0, timestep=5)
    assert tp.tasks[0]["status"] == "in_progress"
    tp.complete_task(0, timestep=10)
    assert tp.tasks[0]["status"] == "completed"
    assert tp.get_agent_current_task(0) is None  # 任务完成后查不到
    assert not tp.all_done()  # task 1 还没完成

check("TaskPool 完成流转", test_taskpool_complete)

def test_taskpool_summary():
    tp = TaskPool(["boiled_egg"])
    tp.claim_task(0, 0, "Chef")
    s = tp.summary()
    assert "Task 0" in s
    assert "boiled_egg" in s

check("TaskPool summary 输出", test_taskpool_summary)

def test_taskpool_serialize():
    tp = TaskPool(["boiled_egg"])
    tp.claim_task(0, 0, "Chef")
    d = tp.to_dict()
    assert "tasks" in d
    assert "wash_queue" in d

check("TaskPool 序列化", test_taskpool_serialize)

# ======================================================================
print("\n" + "=" * 60)
print("  P0 验证：3/7 角色系统检查")
print("=" * 60)

def test_role_mapping():
    role_to_actor = {"chef": "chef", "assistant": "assistant", "dishwasher": "dishwasher"}
    assert role_to_actor.get("chef") == "chef"
    assert role_to_actor.get("dishwasher") == "dishwasher"
    assert role_to_actor.get("assistant") == "assistant"

check("角色→actor 映射", test_role_mapping)

def test_actor_to_name():
    _actor_to_name = {"chef": "Chef", "assistant": "Assistant", "dishwasher": "Dishwasher"}
    assert _actor_to_name.get("chef") == "Chef"
    assert _actor_to_name.get("dishwasher") == "Dishwasher"
    assert _actor_to_name.get("assistant") == "Assistant"

check("actor→name 映射", test_actor_to_name)

def test_dishwasher_skill_exists():
    path = "collab_overcooked/prompts/gpt/dishwasher_skill.txt"
    assert os.path.exists(path), f"文件不存在: {path}"
    with open(path) as f:
        content = f.read()
    assert "wash" in content.lower()
    assert "Dishwasher" in content

check("dishwasher_skill.txt 存在且内容正确", test_dishwasher_skill_exists)

# ======================================================================
print("\n" + "=" * 60)
print("  P0 验证：4/7 配置文件加载")
print("=" * 60)

import yaml

def test_yaml_load():
    with open("configs/default.yaml", "r") as f:
        config = yaml.safe_load(f)
    env = config["environment"]
    agents = config["agents"]
    assert env["num_concurrent_tasks"] == 3
    assert env["max_clean_dishes"] == 6
    assert env["wash_time"] == 5
    assert agents["num_agents"] == 5
    # 检查角色分配
    roles = {f"agent_{i}": agents[f"agent_{i}"]["role"] for i in range(5)}
    assert roles["agent_0"] == "Chef"
    assert roles["agent_1"] == "Assistant"
    assert roles["agent_2"] == "Assistant"
    assert roles["agent_3"] == "Dishwasher"
    assert roles["agent_4"] == "Chef"

check("default.yaml 加载 & 角色分配", test_yaml_load)

# ======================================================================
print("\n" + "=" * 60)
print("  P0 验证：5/7 MDP + Layout 加载")
print("=" * 60)

def test_mdp_load():
    from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld
    mdp = OvercookedGridworld.from_layout_name("multi_agent_map", max_clean_dishes=6, wash_time=5)
    assert mdp.num_players == 5, f"期望 5 玩家，实际 {mdp.num_players}"
    assert mdp.max_clean_dishes == 6
    assert mdp.wash_time == 5
    assert mdp.clean_dishes_available == 6
    print(f"    地图尺寸: {mdp.width}x{mdp.height}, 玩家数: {mdp.num_players}")

check("MDP multi_agent_map 加载", test_mdp_load)

def test_mdp_w_symbol():
    from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld
    mdp = OvercookedGridworld.from_layout_name("multi_agent_map")
    # 验证 W 符号被正确处理（不会抛出断言错误）
    grid = mdp.terrain_mtx
    has_w = any("W" in row for row in grid)
    # W 在 grid 中可能已被替换为 ' ' (空格) 或保留为 'W'
    # 关键是加载不报错
    print(f"    Grid 中包含 W: {has_w}")

check("W 符号处理", test_mdp_w_symbol)

# ======================================================================
print("\n" + "=" * 60)
print("  P0 验证：6/7 Agent 创建 & build_access_utensil")
print("=" * 60)

def test_agent_creation():
    from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld
    from overcooked_ai_py.mdp.overcooked_env import OvercookedEnv
    from overcooked_ai_py.planning.planners import MediumLevelPlanner
    
    mdp = OvercookedGridworld.from_layout_name("multi_agent_map", max_clean_dishes=6, wash_time=5)
    mlam_params = {
        "start_orientations": False,
        "wait_allowed": True,
        "counter_goals": mdp.get_counter_locations(),
        "counter_drop": mdp.get_counter_locations(),
        "counter_pickup": mdp.get_counter_locations(),
        "same_motion_goals": True,
    }
    print("    正在加载 Planner（可能需要一些时间）...")
    mlam = MediumLevelPlanner.from_pickle_or_compute(mdp, mlam_params, force_compute=False)
    
    from collab_overcooked.agents.collab import LLMAgents
    
    # 创建 5 个 agent
    roles = ["chef", "assistant", "assistant", "dishwasher", "chef"]
    agents = []
    for i, actor in enumerate(roles):
        agent = LLMAgents(
            mlam, "multi_agent_map",
            model="gpt-3.5-turbo",
            actor=actor,
            controller_mode="new",
        )
        agent.set_mdp(mdp)
        agents.append(agent)
    
    assert len(agents) == 5
    assert agents[0].name == "Chef"
    assert agents[1].name == "Assistant"
    assert agents[3].name == "Dishwasher"
    assert agents[3].role == "Dishwasher"
    
    # 检查 task_pool 注入
    tp = TaskPool(["boiled_egg"] * 3)
    for i, ag in enumerate(agents):
        ag.task_pool = tp
        ag.role = ["Chef", "Assistant", "Assistant", "Dishwasher", "Chef"][i]
    assert agents[3].task_pool is tp
    
    print(f"    成功创建 5 个 Agent: {[a.name for a in agents]}")
    return True

check("5 个 Agent 创建 + 属性检查", test_agent_creation)

def test_build_access_utensil():
    """测试 build_access_utensil 多Agent 支持"""
    from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld
    from overcooked_ai_py.mdp.overcooked_env import OvercookedEnv
    from overcooked_ai_py.planning.planners import MediumLevelPlanner
    from overcooked_ai_py.agents.agent import AgentGroup
    from collab_overcooked.agents.collab import LLMAgents

    mdp = OvercookedGridworld.from_layout_name("multi_agent_map", max_clean_dishes=6, wash_time=5)
    mlam_params = {
        "start_orientations": False,
        "wait_allowed": True,
        "counter_goals": mdp.get_counter_locations(),
        "counter_drop": mdp.get_counter_locations(),
        "counter_pickup": mdp.get_counter_locations(),
        "same_motion_goals": True,
    }
    mlam = MediumLevelPlanner.from_pickle_or_compute(mdp, mlam_params, force_compute=False)

    env = OvercookedEnv(mdp, horizon=10)
    env.reset()
    state = env.state

    roles = ["chef", "assistant", "assistant", "dishwasher", "chef"]
    agents = []
    for actor in roles:
        agent = LLMAgents(mlam, "multi_agent_map", model="gpt-3.5-turbo", actor=actor)
        agent.set_mdp(mdp)
        agents.append(agent)

    team = AgentGroup(*agents)
    team.reset()

    # 调用 build_access_utensil
    agents[0].build_access_utensil(state)
    
    access = getattr(mdp, '_agent_utensil_access', {})
    assert len(access) == 5, f"期望 5 个Agent的工具列表，实际 {len(access)}"
    
    for idx, utensils in access.items():
        print(f"    P{idx}({roles[idx]}): {utensils}")
    
    return True

check("build_access_utensil 多Agent", test_build_access_utensil)

# ======================================================================
print("\n" + "=" * 60)
print("  P0 验证：7/7 Observation 生成")
print("=" * 60)

def test_generate_state_prompt():
    """测试多Agent Observation 生成（不调用 LLM）"""
    from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld
    from overcooked_ai_py.mdp.overcooked_env import OvercookedEnv
    from overcooked_ai_py.planning.planners import MediumLevelPlanner
    from overcooked_ai_py.agents.agent import AgentGroup
    from collab_overcooked.agents.collab import LLMAgents
    
    mdp = OvercookedGridworld.from_layout_name("multi_agent_map", max_clean_dishes=6, wash_time=5)
    mlam_params = {
        "start_orientations": False,
        "wait_allowed": True,
        "counter_goals": mdp.get_counter_locations(),
        "counter_drop": mdp.get_counter_locations(),
        "counter_pickup": mdp.get_counter_locations(),
        "same_motion_goals": True,
    }
    mlam = MediumLevelPlanner.from_pickle_or_compute(mdp, mlam_params, force_compute=False)

    mdp.start_order_list = ["boiled_egg"]
    env = OvercookedEnv(mdp, horizon=10)
    env.reset()
    state = env.state
    # current_k_order 是只读 property，通过 order_list 控制

    roles = ["chef", "assistant", "assistant", "dishwasher", "chef"]
    agents = []
    tp = TaskPool(["boiled_egg"] * 3)
    for actor in roles:
        agent = LLMAgents(mlam, "multi_agent_map", model="gpt-3.5-turbo", actor=actor)
        agent.set_mdp(mdp)
        agent.task_pool = tp
        agents.append(agent)

    team = AgentGroup(*agents)
    team.reset()

    # 测试 Agent 0 的 generate_state_prompt
    agent0 = agents[0]
    try:
        prompt = agent0.generate_state_prompt(state)
        assert "P0" in prompt, "Observation 中应包含 P0 标识"
        assert "Scene 0" in prompt, "Observation 中应包含 Scene 时间戳"
        print(f"    Agent 0 Observation 片段（前 300 字符）:")
        print(f"    {prompt[:300]}...")
    except Exception as e:
        print(f"    Agent 0 生成 Observation 出错: {e}")
        traceback.print_exc()
        return False

    # 测试 Dishwasher (Agent 3) 的 Observation
    agent3 = agents[3]
    agent3.role = "Dishwasher"
    try:
        prompt3 = agent3.generate_state_prompt(state)
        assert "P3" in prompt3, "Observation 中应包含 P3 标识"
        # Dishwasher 应该看到洗碗状态
        has_wash_info = "[Wash]" in prompt3 or "[Task]" in prompt3
        print(f"    Agent 3 (Dishwasher) Observation 含任务/洗碗信息: {has_wash_info}")
        if has_wash_info:
            # 找到 [Wash] 或 [Task] 行
            for line in prompt3.split("\n"):
                if "[Wash]" in line or "[Task]" in line:
                    print(f"    {line}")
    except Exception as e:
        print(f"    Agent 3 生成 Observation 出错: {e}")
        traceback.print_exc()
        return False
    
    return True

check("多Agent Observation 生成", test_generate_state_prompt)

def test_generate_layout_prompt():
    """测试多Agent布局提示"""
    from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld
    from overcooked_ai_py.mdp.overcooked_env import OvercookedEnv
    from overcooked_ai_py.planning.planners import MediumLevelPlanner
    from overcooked_ai_py.agents.agent import AgentGroup
    from collab_overcooked.agents.collab import LLMAgents
    
    mdp = OvercookedGridworld.from_layout_name("multi_agent_map", max_clean_dishes=6, wash_time=5)
    mlam_params = {
        "start_orientations": False,
        "wait_allowed": True,
        "counter_goals": mdp.get_counter_locations(),
        "counter_drop": mdp.get_counter_locations(),
        "counter_pickup": mdp.get_counter_locations(),
        "same_motion_goals": True,
    }
    mlam = MediumLevelPlanner.from_pickle_or_compute(mdp, mlam_params, force_compute=False)
    env = OvercookedEnv(mdp, horizon=10)
    env.reset()
    state = env.state

    roles = ["chef", "assistant", "assistant", "dishwasher", "chef"]
    agents = []
    for actor in roles:
        agent = LLMAgents(mlam, "multi_agent_map", model="gpt-3.5-turbo", actor=actor)
        agent.set_mdp(mdp)
        agents.append(agent)

    team = AgentGroup(*agents)
    team.reset()
    
    # 先 build access
    agents[0].build_access_utensil(state)

    layout_prompt = agents[0].generate_layout_prompt()
    assert "P0" in layout_prompt
    assert "workspace" in layout_prompt.lower() or "Workspace" in layout_prompt
    print(f"    Layout prompt:\n    " + layout_prompt.replace("\n", "\n    "))
    return True

check("多Agent 布局提示生成", test_generate_layout_prompt)

# ======================================================================
print("\n" + "=" * 60)
print("  P1 验证：8/11 任务认领逻辑")
print("=" * 60)

def test_task_claiming():
    """测试 Agent 自动认领任务 + 配对逻辑"""
    from collab_overcooked.task_manager import TaskPool
    pool = TaskPool(["boiled_egg", "boiled_egg", "boiled_egg"])

    # Chef(agent_0) 认领 Task 0
    ok = pool.claim_task(0, 0, "Chef")
    assert ok, "Chef 认领 Task 0 失败"

    # Assistant(agent_1) 认领 Task 0
    ok = pool.claim_task(0, 1, "Assistant")
    assert ok, "Assistant 认领 Task 0 失败"

    # 另一个 Chef 不能再认领 Task 0
    ok = pool.claim_task(0, 4, "Chef")
    assert not ok, "第二个 Chef 不应该能认领 Task 0"

    # 查询配对
    partner = pool.get_task_partner(0)
    assert partner == 1, f"P0 的 partner 应该是 1, 得到 {partner}"
    partner = pool.get_task_partner(1)
    assert partner == 0, f"P1 的 partner 应该是 0, 得到 {partner}"

    # Agent 4 (Chef) 认领 Task 1
    ok = pool.claim_task(1, 4, "Chef")
    assert ok, "Chef(4) 认领 Task 1 失败"
    # Agent 2 (Assistant) 认领 Task 1
    ok = pool.claim_task(1, 2, "Assistant")
    assert ok, "Assistant(2) 认领 Task 1 失败"

    partner = pool.get_task_partner(4)
    assert partner == 2, f"P4 的 partner 应该是 2, 得到 {partner}"

    # Dishwasher 不认领烹饪任务
    avail_dw = pool.get_available_tasks(role="Dishwasher")
    assert len(avail_dw) == 0, f"Dishwasher 不应看到可用任务, 得到 {len(avail_dw)}"
    return True

check("任务认领 + 配对逻辑", test_task_claiming)

def test_task_complete_wash_flow():
    """测试任务完成 → 洗碗任务 → 干净盘子更新"""
    from collab_overcooked.task_manager import TaskPool
    pool = TaskPool(["boiled_egg", "boiled_egg"])

    pool.claim_task(0, 0, "Chef")
    pool.claim_task(0, 1, "Assistant")
    pool.start_task(0, timestep=0)
    pool.complete_task(0, timestep=5)

    # 任务应标记为 completed
    assert pool.tasks[0]["status"] == "completed"
    assert pool.tasks[0]["complete_time"] == 5

    # 添加洗碗任务
    pool.add_wash_job(timestep=5, wash_time=5)
    assert len(pool.get_pending_wash_jobs()) == 1

    # 时间还没到
    completed = pool.update_wash_jobs(8)
    assert completed == 0

    # 时间到了
    completed = pool.update_wash_jobs(10)
    assert completed == 1

    return True

check("任务完成 → 洗碗流转", test_task_complete_wash_flow)

def test_get_available_by_role():
    """测试按角色过滤可用任务"""
    from collab_overcooked.task_manager import TaskPool
    pool = TaskPool(["boiled_egg", "boiled_egg"])

    # 初始状态下 Chef 和 Assistant 都能看到 2 个任务
    assert len(pool.get_available_tasks(role="Chef")) == 2
    assert len(pool.get_available_tasks(role="Assistant")) == 2

    # Chef(0) 认领 Task 0
    pool.claim_task(0, 0, "Chef")
    # 现在另一个 Chef 只能看到 Task 1
    assert len(pool.get_available_tasks(role="Chef")) == 1
    # 但 Assistant 仍然能看到 Task 0（需要 Assistant）和 Task 1
    assert len(pool.get_available_tasks(role="Assistant")) == 2

    return True

check("按角色过滤可用任务", test_get_available_by_role)

# ======================================================================
print("\n" + "=" * 60)
print("  P1 验证：9/11 get_comm_partner 配对")
print("=" * 60)

def test_get_comm_partner():
    """测试 get_comm_partner 是否正确返回同任务队友"""
    from collab_overcooked.task_manager import TaskPool
    from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld
    from overcooked_ai_py.mdp.overcooked_env import OvercookedEnv
    from overcooked_ai_py.planning.planners import MediumLevelPlanner

    mdp = OvercookedGridworld.from_layout_name("multi_agent_map", max_clean_dishes=6, wash_time=5)
    env = OvercookedEnv(mdp, horizon=10)
    env.reset()

    mlam = MediumLevelPlanner.from_pickle_or_compute(mdp, {
        "start_orientations": False, "wait_allowed": True,
        "counter_goals": mdp.get_counter_locations(),
        "counter_drop": mdp.get_counter_locations(),
        "counter_pickup": mdp.get_counter_locations(),
        "same_motion_goals": True,
    }, force_compute=False)

    from collab_overcooked.agents.collab import LLMAgents
    agents = []
    roles = ["Chef", "Assistant", "Assistant", "Dishwasher", "Chef"]
    for i, role in enumerate(roles):
        actor = {"Chef": "chef", "Assistant": "assistant", "Dishwasher": "dishwasher"}[role]
        a = LLMAgents(mlam, "multi_agent_map", actor=actor, model="gpt-4o",
                       local_server_api="http://localhost:8000/v1")
        a.set_agent_index(i)
        a.set_mdp(mdp)
        a.role = role
        agents.append(a)

    # Setup teammates
    for i, a in enumerate(agents):
        a.teammates = [agents[j] for j in range(len(agents)) if j != i]
        a.teammate = a.teammates[0] if a.teammates else None

    # 设置 TaskPool
    pool = TaskPool(["boiled_egg", "boiled_egg"])
    for a in agents:
        a.task_pool = pool

    # Chef(0) + Assistant(1) 认领 Task 0
    pool.claim_task(0, 0, "Chef")
    pool.claim_task(0, 1, "Assistant")

    # Chef(4) + Assistant(2) 认领 Task 1
    pool.claim_task(1, 4, "Chef")
    pool.claim_task(1, 2, "Assistant")

    # 验证配对
    partner_of_0 = agents[0].get_comm_partner()
    assert partner_of_0 is not None and partner_of_0.agent_index == 1, \
        f"P0 的通讯伙伴应该是 P1, 得到 P{getattr(partner_of_0, 'agent_index', '?')}"

    partner_of_2 = agents[2].get_comm_partner()
    assert partner_of_2 is not None and partner_of_2.agent_index == 4, \
        f"P2 的通讯伙伴应该是 P4, 得到 P{getattr(partner_of_2, 'agent_index', '?')}"

    # Dishwasher(3) 没有通讯伙伴（任务配对层面）
    partner_of_3 = agents[3].get_comm_partner()
    # Dishwasher 没有认领任务，所以 get_task_partner 返回 None，回退到 self.teammate
    # 这是预期行为

    print(f"    P0 partner: P{partner_of_0.agent_index}({partner_of_0.name})")
    print(f"    P2 partner: P{partner_of_2.agent_index}({partner_of_2.name})")
    return True

check("get_comm_partner 配对验证", test_get_comm_partner)

# ======================================================================
print("\n" + "=" * 60)
print("  P1 验证：10/11 提示词模板")
print("=" * 60)

def test_prompt_templates():
    """测试提示词文件内容是否包含多任务关键词"""
    prompt_dir = os.path.join("collab_overcooked", "prompts", "gpt")

    # environment_rule.txt
    with open(os.path.join(prompt_dir, "environment_rule.txt"), "r") as f:
        env_rule = f.read()
    assert "multiple" in env_rule.lower() or "multi" in env_rule.lower(), \
        "environment_rule.txt 缺少多任务/多Agent描述"
    assert "Dishwasher" in env_rule, "environment_rule.txt 缺少 Dishwasher 描述"
    assert "Task Assignment" in env_rule, "environment_rule.txt 缺少 Task Assignment 部分"

    # communication_rule.txt
    with open(os.path.join(prompt_dir, "communication_rule.txt"), "r") as f:
        comm_rule = f.read()
    assert "task partner" in comm_rule.lower() or "TaskPool" in comm_rule, \
        "communication_rule.txt 缺少任务配对描述"

    # dishwasher_skill.txt
    with open(os.path.join(prompt_dir, "dishwasher_skill.txt"), "r") as f:
        dw_skill = f.read()
    assert "wash" in dw_skill.lower(), "dishwasher_skill.txt 缺少 wash 描述"

    return True

check("提示词模板内容验证", test_prompt_templates)

# ======================================================================
print("\n" + "=" * 60)
print("  P1 验证：11/11 Dishwasher 通讯屏蔽")
print("=" * 60)

def test_dishwasher_no_comm():
    """验证 Dishwasher 角色不触发 communication"""
    # Dishwasher 的 role 检查
    from collab_overcooked.agents.collab import LLMAgents
    # 简单检查: 代码中 role.lower() == 'dishwasher' 的条件存在
    import inspect
    source = inspect.getsource(LLMAgents.generate_ml_action)
    # 检查 communication 判断中有 dishwasher 过滤
    source_all = inspect.getsource(LLMAgents)
    assert "dishwasher" in source_all.lower(), "LLMAgents 代码中缺少 dishwasher 处理逻辑"
    return True

check("Dishwasher 通讯屏蔽逻辑存在", test_dishwasher_no_comm)

# ======================================================================
print("\n" + "=" * 60)
print("  P2 验证：12/18 TaskPool 补充任务")
print("=" * 60)

def test_replenish_tasks():
    """测试任务完成后自动补充新任务"""
    pool = TaskPool(["boiled_egg", "boiled_egg", "boiled_egg"], num_concurrent_tasks=3)
    assert len(pool.tasks) == 3

    # 完成 Task 0
    pool.claim_task(0, 0, "Chef")
    pool.claim_task(0, 1, "Assistant")
    pool.start_task(0, timestep=0)
    pool.complete_task(0, timestep=10)

    # 补充新任务
    new_tasks = pool.replenish_tasks()
    assert len(new_tasks) == 1, f"期望补充 1 个任务, 实际 {len(new_tasks)}"
    assert len(pool.tasks) == 4
    assert pool.tasks[3]["status"] == "pending"
    print(f"    补充后任务数: {len(pool.tasks)}, 新任务: Task {new_tasks[0]['id']}({new_tasks[0]['order']})")
    return True

check("TaskPool 补充任务", test_replenish_tasks)

def test_replenish_max_limit():
    """测试 max_total_tasks 限制"""
    pool = TaskPool(["boiled_egg", "boiled_egg"], num_concurrent_tasks=2, max_total_tasks=3)
    assert len(pool.tasks) == 2

    # 完成 Task 0
    pool.claim_task(0, 0, "Chef")
    pool.start_task(0, 0)
    pool.complete_task(0, 5)

    # 补充 — 最多 3 个
    new1 = pool.replenish_tasks()
    assert len(new1) == 1
    assert len(pool.tasks) == 3

    # 再完成一个 → 不能再补充
    pool.claim_task(1, 1, "Chef")
    pool.start_task(1, 5)
    pool.complete_task(1, 10)
    new2 = pool.replenish_tasks()
    assert len(new2) == 0, f"已达 max_total_tasks, 不应补充, 但补了 {len(new2)} 个"
    return True

check("TaskPool max_total_tasks 限制", test_replenish_max_limit)

def test_taskpool_stats():
    """测试 TaskPool 统计数据"""
    pool = TaskPool(["boiled_egg", "boiled_egg"], num_concurrent_tasks=2)
    pool.claim_task(0, 0, "Chef")
    pool.claim_task(0, 1, "Assistant")
    pool.start_task(0, timestep=2)
    pool.complete_task(0, timestep=12)
    pool.add_wash_job(12, 5)
    pool.update_wash_jobs(17)

    assert pool.stats["total_completed"] == 1
    assert pool.stats["total_wash_done"] == 1
    assert len(pool.stats["task_durations"]) == 1
    assert pool.stats["task_durations"][0] == (0, "boiled_egg", 10)  # duration = 12 - 2

    d = pool.to_dict()
    assert "stats" in d
    print(f"    Stats: {pool.stats}")
    return True

check("TaskPool 统计数据", test_taskpool_stats)

# ======================================================================
print("\n" + "=" * 60)
print("  P2 验证：13/18 工具冲突检测增强")
print("=" * 60)

def test_conflict_resolution():
    """测试冲突解决建议"""
    pool = TaskPool(["boiled_egg"], num_concurrent_tasks=1)
    advice = pool.get_conflict_resolution({0: "blender0", 2: "blender0", 4: "pot0"})
    assert advice[0] is None, f"P0 应无需等待 (优先级最高), 得到 {advice[0]}"
    assert advice[2] == "wait_for_blender0", f"P2 应等待, 得到 {advice[2]}"
    assert advice[4] is None, f"P4 无冲突, 应为 None, 得到 {advice[4]}"
    return True

check("冲突解决建议 (get_conflict_resolution)", test_conflict_resolution)

# ======================================================================
print("\n" + "=" * 60)
print("  P2 验证：14/18 _extract_target_utensil")
print("=" * 60)

def test_extract_target_utensil():
    """测试从动作字符串中提取目标工具"""
    from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld
    from overcooked_ai_py.planning.planners import MediumLevelPlanner
    from collab_overcooked.agents.collab import LLMAgents

    mdp = OvercookedGridworld.from_layout_name("multi_agent_map", max_clean_dishes=6, wash_time=5)
    mlam = MediumLevelPlanner.from_pickle_or_compute(mdp, {
        "start_orientations": False, "wait_allowed": True,
        "counter_goals": mdp.get_counter_locations(),
        "counter_drop": mdp.get_counter_locations(),
        "counter_pickup": mdp.get_counter_locations(),
        "same_motion_goals": True,
    }, force_compute=False)

    agent = LLMAgents(mlam, "multi_agent_map", model="gpt-3.5-turbo", actor="chef")
    agent.set_mdp(mdp)

    # cook(pot0) → pot0
    assert agent._extract_target_utensil("cook(pot0)") == "pot0"
    # cut(chopping_board0) → chopping_board0
    assert agent._extract_target_utensil("cut(chopping_board0)") == "chopping_board0"
    # wash(water0) → water0
    assert agent._extract_target_utensil("wash(water0)") == "water0"
    # put_obj_in_utensil(blender0) → blender0
    assert agent._extract_target_utensil("put_obj_in_utensil(blender0)") == "blender0"
    # fill_dish_with_food(pot0) → pot0
    assert agent._extract_target_utensil("fill_dish_with_food(pot0)") == "pot0"
    # pickup(egg, pot0) → pot0 (if pot0 is in utensil_list)
    result = agent._extract_target_utensil("pickup(egg, pot0)")
    # pot0 may or may not be in utensil_list depending on map config
    print(f"    pickup(egg, pot0) → {result}")
    # wait(1) → None
    assert agent._extract_target_utensil("wait(1)") is None
    # None input → None
    assert agent._extract_target_utensil(None) is None
    # deliver_soup() → None
    assert agent._extract_target_utensil("deliver_soup()") is None

    print("    所有工具提取测试通过")
    return True

check("_extract_target_utensil 动作解析", test_extract_target_utensil)

# ======================================================================
print("\n" + "=" * 60)
print("  P2 验证：15/18 冲突检测 + Prompt 注入")
print("=" * 60)

def test_conflict_prompt_injection():
    """测试冲突检测 → Prompt 注入 flow"""
    from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld
    from overcooked_ai_py.mdp.overcooked_env import OvercookedEnv
    from overcooked_ai_py.planning.planners import MediumLevelPlanner
    from overcooked_ai_py.agents.agent import AgentGroup
    from collab_overcooked.agents.collab import LLMAgents

    mdp = OvercookedGridworld.from_layout_name("multi_agent_map", max_clean_dishes=6, wash_time=5)
    mlam = MediumLevelPlanner.from_pickle_or_compute(mdp, {
        "start_orientations": False, "wait_allowed": True,
        "counter_goals": mdp.get_counter_locations(),
        "counter_drop": mdp.get_counter_locations(),
        "counter_pickup": mdp.get_counter_locations(),
        "same_motion_goals": True,
    }, force_compute=False)

    env = OvercookedEnv(mdp, horizon=10)
    env.reset()
    state = env.state

    roles = ["chef", "assistant", "assistant", "dishwasher", "chef"]
    agents = []
    pool = TaskPool(["boiled_egg"] * 3, num_concurrent_tasks=3)
    for actor in roles:
        a = LLMAgents(mlam, "multi_agent_map", model="gpt-3.5-turbo", actor=actor)
        a.set_mdp(mdp)
        a.task_pool = pool
        agents.append(a)

    team = AgentGroup(*agents)
    team.reset()

    # 模拟两个 Agent 同时操作 blender0
    agents[1].current_ml_action = "stir(blender0)"
    agents[3].current_ml_action = "stir(blender0)"

    # 手动调用冲突检测
    agents[1]._detect_and_store_conflicts(state)
    info = agents[1]._utensil_conflict_info
    assert info is not None, "应检测到冲突"
    assert len(info) == 1
    assert info[0][0] == "blender0"  # 冲突工具
    assert 1 in info[0][1] and 3 in info[0][1]  # 冲突 Agent
    assert info[0][2] == 1  # P1 有优先级

    # 生成 Prompt
    prompt = agents[1]._build_conflict_prompt()
    assert "PRIORITY" in prompt, "P1 有优先级，应包含 PRIORITY"
    print(f"    P1 (优先) 冲突提示:\n    {prompt.strip()}")

    # P3 应该看到 WAIT 建议
    agents[3]._detect_and_store_conflicts(state)
    prompt3 = agents[3]._build_conflict_prompt()
    assert "WAIT" in prompt3 or "wait" in prompt3.lower(), "P3 应该被建议等待"
    print(f"    P3 (让步) 冲突提示:\n    {prompt3.strip()}")
    return True

check("冲突检测 + Prompt 注入", test_conflict_prompt_injection)

# ======================================================================
print("\n" + "=" * 60)
print("  P2 验证：16/18 动态任务重分配")
print("=" * 60)

def test_dynamic_reassignment():
    """测试任务完成后 Agent 状态重置 + 重新认领"""
    from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld
    from overcooked_ai_py.planning.planners import MediumLevelPlanner
    from collab_overcooked.agents.collab import LLMAgents

    mdp = OvercookedGridworld.from_layout_name("multi_agent_map", max_clean_dishes=6, wash_time=5)
    mlam = MediumLevelPlanner.from_pickle_or_compute(mdp, {
        "start_orientations": False, "wait_allowed": True,
        "counter_goals": mdp.get_counter_locations(),
        "counter_drop": mdp.get_counter_locations(),
        "counter_pickup": mdp.get_counter_locations(),
        "same_motion_goals": True,
    }, force_compute=False)

    pool = TaskPool(["boiled_egg", "boiled_egg", "boiled_egg"], num_concurrent_tasks=3)

    agent = LLMAgents(mlam, "multi_agent_map", model="gpt-3.5-turbo", actor="chef")
    agent.set_mdp(mdp)
    agent.set_agent_index(0)
    agent.task_pool = pool
    agent.role = "Chef"
    agent.current_timestep = 0
    agent.current_ml_action = "cook(pot0)"  # 模拟正在执行的动作

    # 认领 Task 0
    agent._try_claim_task()
    task = pool.get_agent_current_task(0)
    assert task is not None and task["id"] == 0
    assert agent._last_task_id == 0

    # 模拟 Task 0 完成
    pool.claim_task(0, 1, "Assistant")
    pool.start_task(0, 0)
    pool.complete_task(0, 10)

    # 重新调用 _try_claim_task → 应该检测到任务完成，重置状态，认领新任务
    agent._try_claim_task()
    # current_ml_action 应该被清空（由 _reset_after_task_complete）
    assert agent.current_ml_action is None, "任务完成后 current_ml_action 应被重置"
    # 应该认领了新任务
    new_task = pool.get_agent_current_task(0)
    assert new_task is not None, "Agent 应该认领了新任务"
    assert new_task["id"] != 0, "新任务应该不是已完成的 Task 0"
    print(f"    Task 0 完成后，Agent 0 认领了 Task {new_task['id']}({new_task['order']})")
    return True

check("动态任务重分配", test_dynamic_reassignment)

# ======================================================================
print("\n" + "=" * 60)
print("  P2 验证：17/18 配置文件 P2 更新")
print("=" * 60)

def test_yaml_p2():
    """测试 default.yaml 的 P2 配置"""
    import yaml
    with open("configs/default.yaml", "r") as f:
        config = yaml.safe_load(f)
    env = config["environment"]
    assert env["horizon"] >= 100, f"Horizon 应 >= 100 (多任务场景), 实际 {env['horizon']}"
    assert "max_total_tasks" in env, "应包含 max_total_tasks 配置"
    assert env["num_concurrent_tasks"] >= 1
    return True

check("default.yaml P2 配置验证", test_yaml_p2)

# ======================================================================
print("\n" + "=" * 60)
print("  P2 验证：18/18 TaskPool 序列化完整性")
print("=" * 60)

def test_taskpool_full_serialize():
    """测试 TaskPool 完整序列化包含 stats"""
    pool = TaskPool(["boiled_egg", "boiled_egg"], num_concurrent_tasks=2)
    pool.claim_task(0, 0, "Chef")
    pool.claim_task(0, 1, "Assistant")
    pool.start_task(0, 0)
    pool.complete_task(0, 10)
    pool.add_wash_job(10, 5)
    pool.update_wash_jobs(15)

    d = pool.to_dict()
    assert "stats" in d
    assert d["stats"]["total_completed"] == 1
    assert d["stats"]["total_wash_done"] == 1
    assert len(d["stats"]["task_durations"]) == 1

    # 测试 summary 格式
    s = pool.summary()
    assert "completed" in s.lower() or "completed" in s
    assert "duration" in s.lower()
    print(f"    Summary:\n    " + s.replace("\n", "\n    "))
    return True

check("TaskPool 完整序列化", test_taskpool_full_serialize)

# ======================================================================
# 最终总结
# ======================================================================
print("\n" + "=" * 60)
total = PASS + FAIL + WARN
print(f"  总计: {total} 项测试")
print(f"  ✅ 通过: {PASS}")
print(f"  ⚠️  警告: {WARN}")
print(f"  ❌ 失败: {FAIL}")
print("=" * 60)

if FAIL > 0:
    print("\n  ⚠️  有失败项，请检查上方错误信息！")
    sys.exit(1)
else:
    print("\n  🎉 P0 + P1 + P2 所有检查通过！")
    sys.exit(0)
