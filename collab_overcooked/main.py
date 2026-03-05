import time
import datetime
import os
import json
import datetime
import uuid
from argparse import ArgumentParser
from pathlib import Path
import numpy as np
from rich import print as rprint
import copy
from collections import deque

import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'  
work_dir = os.getcwd()
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*cuBLAS factory.*")

from distutils.util import strtobool

def boolean_argument(value):
    """Convert a string value to boolean."""
    return bool(strtobool(value))

def check_recipe_parse(variant):
    """验证 order 或 orders 列表中每个食谱名是否有对应的 recipe prompt 文件"""
    recipe_name_list = os.listdir(PROMPT_DIR+'/recipe/')
    # 获取所有需要验证的 order
    orders_to_check = variant.get('orders', [variant['order']]) if variant.get('orders') else [variant['order']]
    
    for order_name in orders_to_check:
        found = False
        for r in recipe_name_list:
            if order_name in r.lower():
                found = True
                break
        if not found:
            raise ValueError(f"Not valid order name: '{order_name}'! Available recipes: {[r[2:-4] for r in recipe_name_list]}")
    return True

# Load YAML for new configuration system
try:
    import yaml
except ImportError:
    print("PyYAML not installed. Install with: pip install PyYAML")
    yaml = None

cwd = os.getcwd()
PROMPT_DIR = os.path.join(os.path.dirname(__file__), "prompts")

from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld, OvercookedState
from overcooked_ai_py.mdp.overcooked_env import OvercookedEnv
from overcooked_ai_py.agents.agent import AgentGroup
from overcooked_ai_py.mdp.actions import Action
from .reward import ProcessRewardTracker
from .task_manager import TaskPool

# Import from new modular system
try:
    from .agents import statistics_dict, turn_statistics_dict
    from .agents.web_util import output_to_port, check_port_in_use, change_port
    from .utils import make_agent, get_example_embedding, combine_statistic_dict, combine_statistic_dict_multi
    
    # Define make_agent_from_config for new system
    def make_agent_from_config(agent_config, mdp, layout, history_window=0, reward_tracker=None):
        """Create agent from YAML configuration using existing LLMAgents"""
        from .agents.collab import LLMAgents
        from overcooked_ai_py.planning.planners import MediumLevelPlanner

        # Prepare MLAM parameters just like legacy make_agent
        mlam_params = {
            "start_orientations": False,
            "wait_allowed": True,
            "counter_goals": [],
            "counter_drop": [],
            "counter_pickup": [],
            "same_motion_goals": True,
        }
        counter_locations = mdp.get_counter_locations()
        mlam_params["counter_goals"] = counter_locations
        mlam_params["counter_drop"] = counter_locations
        mlam_params["counter_pickup"] = counter_locations

        # Build planner with proper counter awareness
        # Use cache if available (force_compute=False) to avoid recomputing for multi-agent scenarios
        # For 5 players, computing from scratch can take a very long time
        print(f"[Planner] 正在加载/计算 MediumLevelPlanner (玩家数: {mdp.num_players})...")
        mlam = MediumLevelPlanner.from_pickle_or_compute(
            mdp, mlam_params, force_compute=False
        )
        print(f"[Planner] MediumLevelPlanner 加载完成")

        # Map role to actor name (supports Chef / Assistant / Dishwasher)
        role = agent_config.get("role", "Chef")
        role_to_actor = {"chef": "chef", "assistant": "assistant", "dishwasher": "dishwasher"}
        actor = role_to_actor.get(role.lower(), "assistant")

        # Backward-compatible config fields
        retrival_method = agent_config.get(
            "retrieval_method", agent_config.get("retrival_method", "recent_k")
        )
        history_k = int(agent_config.get("history_k", agent_config.get("K", 1)))
        local_server_api = agent_config.get(
            "base_url", agent_config.get("local_server_api", "http://localhost:8000/v1")
        )

        agent_history_window = agent_config.get("history_window", history_window)

        agent = LLMAgents(
            mlam,
            layout,
            model=agent_config.get("model", "gpt-3.5-turbo"),
            model_dirname=agent_config.get("model_dirname", "~/"),
            local_server_api=local_server_api,
            timeout=agent_config.get("timeout"),
            retrival_method=retrival_method,
            K=history_k,
            actor=actor,
            auto_unstuck=agent_config.get("auto_unstuck", False),
            controller_mode=agent_config.get("controller_mode", "new"),
            debug_mode=agent_config.get("debug_mode", "Y"),
            outdir=agent_config.get("outdir"),
            history_window=agent_history_window,
            reward_tracker=reward_tracker,
            response_language=agent_config.get("response_language", agent_config.get("language")),
        )

        if agent_config.get("api_key"):
            agent.api_key = agent_config["api_key"]

        agent.set_mdp(mdp)
        return agent
except ImportError:
    # Fallback to old system  
    from .agents.modules import statistics_dict, turn_statistics_dict
    from .agents.web_util import output_to_port, check_port_in_use, change_port
    from .utils import make_agent, get_example_embedding, combine_statistic_dict, combine_statistic_dict_multi
    make_agent_from_config = None

import socket


def load_config_from_yaml(config_path):
    """Load configuration from YAML file"""
    if not yaml:
        raise ImportError("PyYAML is required for YAML configuration. Install with: pip install PyYAML")
    
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    return config


def convert_yaml_to_variant(config):
    """Convert YAML config to old-style variant dict"""
    env_config = config.get('environment', {})
    agents_config = config.get('agents', {})
    run_config = config.get('run', {})
    
    # 支持 orders (列表) 和 order (单个字符串) 两种配置方式
    orders_raw = env_config.get('orders', None)
    order_single = env_config.get('order', 'boiled_egg')
    if orders_raw and isinstance(orders_raw, list):
        orders_list = orders_raw
    else:
        orders_list = [order_single]

    variant = {
        'layout': env_config.get('layout', 'cramped_room'),
        'horizon': env_config.get('horizon', 10),
        'order': orders_list[0],          # 兼容旧代码：第一个 order
        'orders': orders_list,            # 新字段：完整 orders 列表
        'episode': config.get('episode', run_config.get('episode', 1)),
        'mode': config.get('mode', run_config.get('mode', 'exp')),
        'test_mode': config.get('test_mode', run_config.get('test_mode', 'single_task')),
        'p0': config.get('p0', run_config.get('p0', 'LLMPair')),
        'p1': config.get('p1', run_config.get('p1', 'LLMPair')),
        'collab_mode': config.get('collab_mode', run_config.get('collab_mode', 'llm')),
        'llm_model': config.get(
            'llm_model',
            run_config.get(
                'llm_model',
                config.get('gpt_model', 'gpt-3.5-turbo'),
            ),
        ),
        'reward': config.get('reward', run_config.get('reward', {})),
        'history_window': config.get('history_window', run_config.get('history_window', 0)),
        'agent_configs': agents_config,
        'use_new_system': True,
        'run_id': run_config.get('run_id', config.get('run_id')),
        'results_root': run_config.get('results_root', config.get('results_root', 'results')),
        # 盘子管理参数（传递给 MDP）
        'max_clean_dishes': env_config.get('max_clean_dishes'),
        'wash_time': env_config.get('wash_time'),
    }
    
    return variant


def main(variant=None, config_path=None):
    """
    Main function supporting both old variant dict and new YAML config
    """
    
    # Handle new YAML configuration
    if config_path:
        config = load_config_from_yaml(config_path)
        variant = convert_yaml_to_variant(config)
        variant['yaml_config'] = config
    
    if variant is None:
        raise ValueError("Either variant dict or config_path must be provided")

    statistics_dict.setdefault("process_rewards", [])
    statistics_dict["process_rewards"].clear()
    statistics_dict["prompt_templates"] = {}

    layout = variant['layout']
    horizon = variant['horizon']
    episode = variant['episode']
    # 多 order 时用下划线连接作为目录名
    orders_for_name = variant.get('orders', [variant.get('order', 'task')])
    order_name = "_".join(orders_for_name) if len(orders_for_name) <= 3 else f"{orders_for_name[0]}_x{len(orders_for_name)}"

    mode = variant.get('mode', 'exp')
    collab_mode = variant.get('collab_mode', 'llm').lower()
    llm_model_name = variant.get('llm_model', 'gpt-3.5-turbo')

    run_id = variant.get('run_id')
    if not run_id:
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        run_id = f"{timestamp}-{uuid.uuid4().hex[:6]}"
        variant['run_id'] = run_id
    print(f"[Run] 使用 run_id: {run_id}")

    results_root = Path(variant.get('results_root', variant.get('statistics_save_dir', 'results')))
    try:
        history_window = max(0, int(variant.get('history_window', 0)))
    except (TypeError, ValueError):
        history_window = 0
    
    # 创建 MDP，传递盘子管理参数（如果提供）
    mdp_params = {}
    if variant.get('max_clean_dishes') is not None:
        mdp_params['max_clean_dishes'] = variant['max_clean_dishes']
    if variant.get('wash_time') is not None:
        mdp_params['wash_time'] = variant['wash_time']
    
    mdp = OvercookedGridworld.from_layout_name(layout, **mdp_params)

    reward_tracker = None
    reward_reference_dir = Path(PROMPT_DIR) / "reference"
    reward_settings = variant.get('reward', {})
    try:
        reward_tracker = ProcessRewardTracker(
            order=variant['order'],
            mdp=mdp,
            reference_dir=reward_reference_dir,
            settings=reward_settings,
        )
    except Exception as exc:
        print(f"[ProcessRewardTracker] disabled: {exc}")
        reward_tracker = None

    # 获取 orders 列表（支持多种不同的 order）
    orders_list = variant.get('orders', [variant['order']] if variant.get('order') else ['boiled_egg'])
    
    #set order according to parser — 验证所有 order 都有对应的 recipe 文件
    if orders_list and orders_list[0] != "" and check_recipe_parse(variant):
        mdp.start_order_list = list(orders_list)  # MDP 记录所有 order
        mdp.one_task_mode = False  # 多任务模式

    env = OvercookedEnv(mdp, horizon=horizon)
    env.reset()

    # --- 创建 TaskPool ---
    num_concurrent_tasks = variant.get('yaml_config', {}).get('environment', {}).get('num_concurrent_tasks', 3)
    max_total_tasks = variant.get('yaml_config', {}).get('environment', {}).get('max_total_tasks', 0)
    # 用 orders 列表初始化 TaskPool（直接使用配置的 orders，不再重复）
    task_pool = TaskPool(orders_list, num_concurrent_tasks=num_concurrent_tasks, max_total_tasks=max_total_tasks)
    print(f"\n[TaskPool] 初始化 {len(orders_list)} 个任务 (并发={num_concurrent_tasks}, max_total={max_total_tasks}): {orders_list}")
    print(task_pool.summary())
    
    p0_algo = variant.get('p0', 'LLMPair')
    p1_algo = variant.get('p1', 'LLMPair')
    print(f"\n===P0 agent: {p0_algo} | P1 agent: {p1_algo}===\n")

    start_time = time.time()
    results = []

    actor_num = 0
    actor_list = ['chef','assistant']
    for i in range(episode):  
        if reward_tracker:
            reward_tracker.reset()

        agents_list = []

        episode_stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        
        # Handle save directory for new config system
        if variant.get('use_new_system'):
            save_dir = results_root / f"{run_id}_{order_name}"
        else:
            stats_dir = Path(variant.get('statistics_save_dir', 'data'))
            save_dir = stats_dir / llm_model_name / order_name
        
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = save_dir / f"experiment_{episode_stamp}_{order_name}.json"

        if mode == 'develop':
            """
            You can customize the 'action_list' and 'parm' to test the environment
            """
            action_list = []
            parm = []

            env.reset()
            r_total = 0
            for t in range(horizon):
                s_t = env.state
                # print(s_t.timestep, env.t)
                print(f'\n>>>>>>>>>>>>>time: {t}<<<<<<<<<<<<<<<<<<<<<\n')
                print(env.mdp.state_string(s_t).replace('ø', 'o'))


                obs, reward, done, env_info = env.step(action_list[t], parm[t])
                print(env.mdp.get_utensil_states(s_t))
                ml_actions = obs.ml_actions
                skills = f""
                for p_idx, ml_action in enumerate(ml_actions):
                    if ml_action == None:
                        continue
                    skills += f"P{p_idx} finished <{ml_action}>. "
                print(skills)

                r_total += reward
                rprint("[red]" + f'r: {reward} | total: {r_total}\n\n')
            break

        
        # Create agents - support both old and new systems
        if variant.get('use_new_system') and make_agent_from_config:
            # Use new configuration system
            agent_configs = variant.get('agent_configs', {})
            num_agents_config = agent_configs.get('num_agents', 0)
            
            # 按顺序创建智能体（agent_0, agent_1, agent_2, ...）
            for i in range(num_agents_config):
                agent_id = f'agent_{i}'
                if agent_id not in agent_configs:
                    raise ValueError(f"配置文件中缺少 {agent_id} 的定义，但 num_agents={num_agents_config}")
                
                agent_config = agent_configs[agent_id]
                print(f"\n----创建 {agent_id}: {agent_config.get('model', 'unknown')} ({agent_config.get('type', 'unknown')})----\n")
                agent = make_agent_from_config(
                    agent_config,
                    mdp,
                    layout,
                    history_window=history_window,
                    reward_tracker=reward_tracker,
                )
                agents_list.append(agent)
            
            # 验证智能体数量与地图玩家数量匹配
            if len(agents_list) != mdp.num_players:
                print(f"[警告] 创建的智能体数量 ({len(agents_list)}) 与地图玩家数量 ({mdp.num_players}) 不匹配！")
                print(f"地图需要 {mdp.num_players} 个玩家，但配置了 {len(agents_list)} 个智能体")

            # --- 注入 TaskPool 和角色信息到每个 Agent ---
            for idx, agent in enumerate(agents_list):
                agent.task_pool = task_pool
                agent_config = agent_configs.get(f'agent_{idx}', {})
                agent.role = agent_config.get('role', 'Chef')
                print(f"  A{idx}({agent.role}): task_pool 已注入")
        else:
            # Use old system
            for alg in [p0_algo, p1_algo]:
                if alg == "LLMPair":
                    if collab_mode != "human":
                        assert llm_model_name is not None, print('you should choose a llm model')
                    if mode == "OpenSource":
                        assert os.path.exists(variant.get('model_dirname', '')), print(f"you should input right open-source model absolute path")
                    display_name = "Human" if collab_mode == "human" else llm_model_name
                    print(f"\n----Use {display_name} ({collab_mode})----\n")
                    if collab_mode == "human":
                        assert check_port_in_use(variant.get("local_server_api", "http://localhost:8080")), print(f"port {variant.get('local_server_api', 'http://localhost:8080')} is busy")
                        change_port(variant.get("local_server_api", "http://localhost:8080"))
                    
                    gpt_model = "human" if collab_mode == "human" else llm_model_name
                    model_dirname = variant.get('model_dirname', '~/')
                    local_server_api = variant.get('local_server_api', 'http://localhost:8000/v1')
                    retrival_method = variant.get('retrival_method', 'recent_k')
                    K = variant.get('K', 3)
                    
                    agent = make_agent(
                        alg,
                        mdp,
                        layout,
                        model=gpt_model,
                        model_dirname=model_dirname,
                        local_server_api=local_server_api,
                        retrival_method=retrival_method,
                        K=K,
                        actor=actor_list[actor_num],
                        history_window=history_window,
                        reward_tracker=reward_tracker,
                    )
                else:
                    agent = make_agent(alg, mdp, layout)
                agents_list.append(agent)
                actor_num += 1

        team = AgentGroup(*agents_list)
        team.reset()

        env.reset()
        r_total = 0

        
        if mode == 'exp':
            # 第一个时间步：扫描并分配任务，直到所有 Assistant 和 Chef 都有任务
            print("\n" + "="*60)
            print("[初始任务分配] 开始扫描并分配任务...")
            print("="*60)
            
            s_t = env.state
            max_rounds = 10  # 最多扫描 10 轮，避免无限循环
            for round_num in range(max_rounds):
                # 让所有 agent 尝试认领任务
                for agent_idx, agent in enumerate(team.agents):
                    if hasattr(agent, '_try_claim_task'):
                        agent._try_claim_task()
                
                # 检查是否所有 Assistant 和 Chef 都有任务
                all_assigned = True
                for agent_idx, agent in enumerate(team.agents):
                    role = getattr(agent, 'role', '').lower()
                    if role in ('assistant', 'chef'):
                        task = task_pool.get_agent_current_task(agent_idx)
                        if task is None:
                            all_assigned = False
                            break
                
                if all_assigned:
                    print(f"[初始任务分配] 所有 Assistant 和 Chef 都已分配任务（第 {round_num + 1} 轮）")
                    break
                
                if round_num < max_rounds - 1:
                    print(f"[初始任务分配] 第 {round_num + 1} 轮：仍有未分配任务的 Agent，继续扫描...")
            
            # 输出当前各个智能体的任务状态
            print("\n" + "="*60)
            print("[任务分配状态] 当前各个智能体的任务:")
            print("="*60)
            for agent_idx, agent in enumerate(team.agents):
                role = getattr(agent, 'role', 'Unknown')
                task = task_pool.get_agent_current_task(agent_idx)
                if task:
                    partners = task_pool.get_task_teammates(agent_idx)
                    partner_str = ", ".join(f"A{p}" for p in partners) if partners else "none"
                    print(f"  A{agent_idx} ({role}): Task {task['id']}({task['order']}) - 伙伴: {partner_str}")
                else:
                    print(f"  A{agent_idx} ({role}): 无任务")
            print("="*60 + "\n")
            
            for t in range(horizon):
                s_t = env.state
                # print(s_t.timestep, env.t)
                print(f'\n>>>>>>>>>>>>>time: {t}<<<<<<<<<<<<<<<<<<<<<\n')
                map = env.mdp.state_string(s_t).replace('ø', 'o')
                print(map)
                # P1: 每 timestep 打印 TaskPool 状态
                print(task_pool.summary())
                a_t, ingredient_for_pickup = team.joint_action(s_t) 
                print(a_t)
                dialogue_t = team.reset_dialogue()
                print(f"\n-----------Controller-----------\n")    
                # Support multiple agents - dynamically print all agent actions
                action_str = " | ".join([f"A{i} {Action.to_char(a_t[i])}" for i in range(len(a_t))])
                print(f"action: {action_str}")
                parm = ingredient_for_pickup

                obs, reward, done, env_info = env.step(a_t,parm)

                ml_actions = obs.ml_actions
                skills = f""
                for p_idx, ml_action in enumerate(ml_actions):
                    if ml_action == None:
                        continue
                    skills += f"P{p_idx} finished <{ml_action}>. "
                print(skills)

                reward_info = None
                if reward_tracker:
                    # 记录过程奖励，便于日志与可视化分析
                    reward_info = reward_tracker.after_step(t, ml_actions, env.state)
                    statistics_dict["process_rewards"].append(reward_info)

                r_total += reward

                # P1: 每个 timestep 更新 TaskPool 洗碗任务
                wash_time = variant.get('wash_time') or getattr(mdp, 'wash_time', 5)
                newly_clean = task_pool.update_wash_jobs(t)
                if newly_clean > 0:
                    mdp.clean_dishes_available = min(
                        mdp.clean_dishes_available + newly_clean,
                        mdp.max_clean_dishes,
                    )
                    print(f"[Wash] {newly_clean} 盘洗好，当前干净盘子: {mdp.clean_dishes_available}/{mdp.max_clean_dishes}")

                if reward > 0:
                    statistics_dict['total_order_finished'].append(s_t.current_k_order[0])
                    # P1: 完成任务 + 触发洗碗
                    # 找到刚完成 deliver 的 agent，标记其 task 完成
                    for agent_idx, agent in enumerate(team.agents):
                        agent_task = task_pool.get_agent_current_task(agent_idx)
                        if agent_task is not None and agent_task["status"] in ("claimed", "in_progress"):
                            task_pool.complete_task(agent_task["id"], t)
                            task_pool.add_wash_job(t, wash_time)
                            print(f"[TaskComplete] Task {agent_task['id']}({agent_task['order']}) 完成! 洗碗任务已加入队列")
                            # P2-c: 补充新任务
                            new_tasks = task_pool.replenish_tasks()
                            if new_tasks:
                                new_str = ", ".join(f"Task {nt['id']}({nt['order']})" for nt in new_tasks)
                                print(f"[TaskPool] 补充新任务: {new_str}")
                            print(task_pool.summary())
                            break  # 一次 reward 只完成一个任务

                rprint("[red]" + f'r: {reward} | total: {r_total}\n\n')
                # Print behavior for all agents (supporting multiple agents)
                for agent_idx, agent in enumerate(team.agents):
                    if hasattr(agent, 'teammate_ml_actions'):
                        print(f"A{agent_idx}'s real behavior: {agent.teammate_ml_actions}")


                #save statistics - support multiple agents
                num_agents = len(team.agents)
                turn_statistics_dicts = [agent.turn_statistics_dict for agent in team.agents]
                
                # Combine statistics for all agents
                if num_agents == 2:
                    # Backward compatibility: use old combine_statistic_dict for 2 agents
                    turn_statistics_dict_both = combine_statistic_dict(
                        turn_statistics_dicts[0], turn_statistics_dicts[1], map, reward
                    )
                else:
                    # For multiple agents, combine all statistics
                    turn_statistics_dict_both = combine_statistic_dict_multi(
                        turn_statistics_dicts, map, reward
                    )
                
                if reward_info:
                    turn_statistics_dict_both["statistical_data"]["process_reward"] = reward_info

                statistics_dict['total_timestamp'].append(t)
                statistics_dict['total_score'] = r_total
                # Store action lists for all agents
                statistics_dict['total_action_list'] = []
                for agent_idx, agent in enumerate(team.agents):
                    if hasattr(agent, 'teammate_ml_actions'):
                        statistics_dict['total_action_list'].append(agent.teammate_ml_actions)
                    else:
                        statistics_dict['total_action_list'].append([])
                statistics_dict['content'].append(turn_statistics_dict_both)
                # P2-c: 保存 TaskPool 状态到统计中
                statistics_dict['task_pool'] = task_pool.to_dict()
                # A2A Protocol: 保存各 agent 的 A2A 消息日志（旁路记录，不影响实验逻辑）
                statistics_dict['a2a_protocol_log'] = [
                    agent._a2a_protocol.to_dict()
                    for agent in team.agents
                    if hasattr(agent, '_a2a_protocol')
                ]
                with open(filename, 'w') as f:
                    json.dump(statistics_dict,f,indent=4)
                
                if variant['test_mode'] == 'fix_task':
                    # P1: 多任务模式下，所有任务完成才算成功
                    if task_pool.all_done():
                        print(f"All {len(task_pool.tasks)} tasks completed!")
                        if collab_mode == "human":
                            for a in range(len(team.agents)):
                                output_to_port(
                                    f"agent{a}",
                                    "Success!",
                                    mission="success",
                                    port=variant.get('local_server_api', "http://localhost:8080"),
                                )
                        break
            #Human-eval: set task failed message
            if collab_mode == "human":
                for a in range(len(team.agents)):
                    output_to_port(
                        f"agent{a}",
                        "Fail to finish task in time!",
                        mission="fail",
                        port=variant.get('local_server_api', "http://localhost:8080"),
                    )
        print(f"Episode {i+1}/{episode}: {r_total}\n====\n\n")
        results.append(r_total)
   
    end_time = time.time()
    print(f"Cost time : {end_time - start_time:.3f}s-----\n\n")


    
if __name__ == '__main__':

    parser = ArgumentParser(description='OvercookedAI Experiment')

    # these are basis parses
    parser.add_argument('--layout', '-l', type=str, default='new_env', choices=['new_env'])
    parser.add_argument('--p0',  type=str, default='LLMPair', choices=['LLMPair', 'Human'], help='Algorithm for P0 agent 0')
    parser.add_argument('--p1', type=str, default='LLMPair', choices=['LLMPair', 'Human'], help='Algorithm for P1 agent 1')
    parser.add_argument('--horizon', type=int, default=120, help='Horizon steps in one game')
    parser.add_argument('--episode', type=int, default=1, help='Number of episodes')

    # these parsers are only required when using LLMPair.

    parser.add_argument('--collab_mode', type=str, default='llm', choices=['llm', 'human'], help='Whether collaborators are LLMS or humans')
    parser.add_argument('--llm_model', '--gpt_model', dest='llm_model', type=str, default='gpt-3.5-turbo-0125',
                        help='LLM identifier when collab_mode=llm')
    
    parser.add_argument('--retrival_method', type=str, default="recent_k", choices=['recent_k', 'bert_topk'], help='Use similarity-based(BERT, CLIP) retrieval or retrieve recent K history in dialog.')
    parser.add_argument('--K', type=int, default=0, help="The number of dialogues you want to retrieve.")
    parser.add_argument('--history_window', type=int, default=0, help='Number of past decision snippets to include (0 disables history)')

    # 
    parser.add_argument('--model_dirname', type=str, default='.', help='absolute path of open-source model')      
    parser.add_argument('--local_server_api', type=str, default= "http://localhost:8000/v1", help='IP and port address to connect with local open source llm')     
    parser.add_argument('--mode', type=str, default='exp', choices=['exp', 'debug_validator', 'develop'], help='exp mode run step-by-step, demo mode run via traj')                                
    parser.add_argument('--test_mode', type=str, default='fix_task', choices=['fix_task', 'fix_time'])
    parser.add_argument('--save', type=boolean_argument, default=True, help='Whether save the result')
    parser.add_argument('--log_dir', type=str, default=None, help='dir to save result')
    parser.add_argument('--debug', type=boolean_argument, default=True, help='debug mode')
    parser.add_argument('--order', type=str, default="", help='1 task order name')
    parser.add_argument('--run_id', type=str, default=None, help='Unique run identifier (optional)')

    #
    parser.add_argument('--statistics_save_dir', type=str, default='data', help='save directory of LLM statistics')
    parser.add_argument('--config_path', type=str, default=None, help='Path to YAML config (overrides CLI arguments)')


    args = parser.parse_args()

    start_time = time.time()
    if args.config_path:
        main(config_path=args.config_path)
    else:
        variant = vars(args)
        variant.pop('config_path', None)
        main(variant)
    end_time = time.time()
    print(f"\n=======Finshed all=========\n")
    print(f"Cost time : {end_time - start_time:.3f}s-----\n\n")
