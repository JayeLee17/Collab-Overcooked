# 本地 Git 提交指南

## 📋 快速执行

### 方法 1: 使用脚本（推荐）

```bash
cd /Users/lijiayi/Desktop/毕业设计/code/Collab-Overcooked
./GIT_COMMIT_COMMANDS.sh
```

### 方法 2: 手动执行命令

```bash
cd /Users/lijiayi/Desktop/毕业设计/code/Collab-Overcooked

# 1. 添加修改的文件
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

# 2. 提交到本地
git commit -m "修复路径规划和 motion goal 过滤问题

- 修复 pickup_obj_actions 和 go_to_utensil_actions，确保只生成在 agent 可移动范围内的 motion goals
- 修复 find_path 函数，支持 block_other_agent=False，允许 agent 共享位置
- 修复 real_time_planner，使用动态路径规划替代静态连通性检查
- 添加 TaskPool 多任务管理系统
- 支持多 agent（5个）和多任务并发
- 添加 Dishwasher 角色支持
- 更新地图为 9 列布局"

# 3. 查看提交结果
git log --oneline -1
```

## 🌿 推送到远程新分支（可选）

如果之后需要推送到远程，可以创建新分支：

```bash
# 创建新分支
git checkout -b fix/motion-goal-filtering-and-pathfinding

# 推送到远程
git push -u origin fix/motion-goal-filtering-and-pathfinding
```

### 推荐的分支名

- `fix/motion-goal-filtering-and-pathfinding` - 描述性
- `fix/agent-movement-range` - 简洁
- `feature/multi-agent-pathfinding-fix` - 功能导向

## ⚠️ 如果遇到 git lock 问题

如果出现 `fatal: Unable to create '.git/index.lock'` 错误：

```bash
# 删除锁文件（如果确定没有其他 git 操作在进行）
rm -f .git/index.lock

# 然后重新执行提交命令
```

## ✅ 验证提交

提交后可以验证：

```bash
# 查看提交历史
git log --oneline -5

# 查看当前状态
git status

# 查看提交的更改
git show HEAD
```
