#!/bin/bash
# 创建新分支并提交多智能体系统拓展的所有更改

cd /Users/lijiayi/Desktop/毕业设计/code/Collab-Overcooked

echo "=== 步骤 1: 创建新分支 ==="
git checkout -b feature/multi-agent-system-expansion

echo ""
echo "=== 步骤 2: 添加核心代码文件 ==="
git add collab_overcooked/agents/collab.py
git add collab_overcooked/main.py
git add collab_overcooked/task_manager.py
git add collab_overcooked/prompts/gpt/communication_rule.txt
git add collab_overcooked/prompts/gpt/environment_rule.txt
git add collab_overcooked/prompts/gpt/dishwasher_skill.txt
git add collab_overcooked/reward/tracker.py
git add collab_overcooked/training/snapshots.py
git add collab_overcooked/utils/utils.py
git add configs/default.yaml
git add dependencies/overcooked_ai/overcooked_ai_py/agents/agent.py
git add dependencies/overcooked_ai/overcooked_ai_py/mdp/overcooked_mdp.py
git add dependencies/overcooked_ai/overcooked_ai_py/planning/planners.py
git add dependencies/overcooked_ai/overcooked_ai_py/planning/search.py
git add dependencies/overcooked_ai/overcooked_ai_py/data/layouts/multi_agent_map.layout

echo ""
echo "=== 步骤 3: 添加文档文件 ==="
git add AGENT_COLLISION_RULE.md
git add AGENT_STUCK_ANALYSIS.md
git add LATEST_LOG_ANALYSIS.md
git add LATEST_LOG_ANALYSIS_V2.md
git add MAP_INTERACTION_ISSUE.md
git add MOTION_GOAL_FIX.md
git add MOTION_GOAL_FIX_V2.md
git add MULTI_TASK_DESIGN.md
git add PATHFINDING_FIX.md
git add analysis_report.md
git add analysis_report_v2.md
git add bug_fix_summary.md

echo ""
echo "=== 步骤 4: 添加其他相关文件 ==="
git add GIT_COMMIT_COMMANDS.sh
git add 本地Git提交指南.md
git add 推送到远程新分支.md
git add test_p0.py
git add run_background.sh

echo ""
echo "=== 步骤 5: 查看暂存区状态 ==="
git status --short

echo ""
echo "=== 步骤 6: 提交到本地 ==="
git commit -m "多智能体系统拓展：支持多任务和多agent协作

核心功能：
- 支持多智能体系统（从2个扩展到5个agent）
- 实现多任务并发执行机制（TaskPool）
- 添加角色系统（Chef、Assistant、Dishwasher）
- 实现任务认领和配对机制

路径规划和运动目标修复：
- 修复 pickup_obj_actions 和 go_to_utensil_actions，确保只生成在 agent 可移动范围内的 motion goals
- 修复 find_path 函数，支持 block_other_agent=False，允许 agent 共享位置
- 修复 real_time_planner，使用动态路径规划替代静态连通性检查
- 解决 A1/A3 无法到达 I(ingredient_dispenser) 的问题

地图和环境：
- 更新地图为 9 列布局（multi_agent_map）
- 添加 W(water sink) 清洗功能
- 实现盘子管理系统（clean_dishes_available）
- 支持洗碗任务队列

任务管理：
- 实现 TaskPool 多任务管理系统
- 支持任务认领、配对、完成流程
- 实现任务补充机制（replenish_tasks）
- 添加工具冲突检测和协调

代码改进：
- 修复多agent环境下的统计收集
- 修复 reward tracker 支持多agent
- 优化 planner 缓存机制
- 添加详细的调试和日志输出

文档：
- 添加多任务设计文档（MULTI_TASK_DESIGN.md）
- 添加路径规划修复文档（PATHFINDING_FIX.md, MOTION_GOAL_FIX_V2.md）
- 添加agent碰撞规则文档（AGENT_COLLISION_RULE.md）
- 添加问题分析和修复总结文档"

echo ""
echo "=== 步骤 7: 查看提交结果 ==="
git log --oneline -1

echo ""
echo "=== 步骤 8: 查看当前分支 ==="
git branch

echo ""
echo "✅ 本地提交完成！"
echo ""
echo "📤 推送到远程新分支，执行："
echo "   git push -u origin feature/multi-agent-system-expansion"
