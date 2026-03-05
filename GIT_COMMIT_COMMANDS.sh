#!/bin/bash
# Git 提交命令脚本
# 用于本地提交和创建新分支推送到远程

cd /Users/lijiayi/Desktop/毕业设计/code/Collab-Overcooked

echo "=== 步骤 1: 添加修改的文件 ==="
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
echo "=== 步骤 2: 查看暂存区状态 ==="
git status --short

echo ""
echo "=== 步骤 3: 提交到本地 ==="
git commit -m "修复路径规划和 motion goal 过滤问题

- 修复 pickup_obj_actions 和 go_to_utensil_actions，确保只生成在 agent 可移动范围内的 motion goals
- 修复 find_path 函数，支持 block_other_agent=False，允许 agent 共享位置
- 修复 real_time_planner，使用动态路径规划替代静态连通性检查
- 添加 TaskPool 多任务管理系统
- 支持多 agent（5个）和多任务并发
- 添加 Dishwasher 角色支持
- 更新地图为 9 列布局"

echo ""
echo "=== 步骤 4: 查看提交结果 ==="
git log --oneline -1

echo ""
echo "✅ 本地提交完成！"
echo ""
echo "如果需要推送到远程新分支，执行以下命令："
echo "  git checkout -b fix/motion-goal-filtering-and-pathfinding"
echo "  git push -u origin fix/motion-goal-filtering-and-pathfinding"
