# 后台运行命令指南

## 方法1: 使用 nohup（推荐）

### 基本用法
```bash
nohup python -m collab_overcooked.main --config configs/default.yaml > output.log 2>&1 &
```

### 说明
- `nohup`: 让程序在后台运行，即使终端关闭也不会停止
- `> output.log`: 将标准输出重定向到 output.log 文件
- `2>&1`: 将错误输出也重定向到同一个文件
- `&`: 在后台运行

### 查看输出
```bash
# 实时查看日志
tail -f output.log

# 查看最后100行
tail -n 100 output.log

# 查看全部日志
cat output.log
```

### 查看进程
```bash
# 查看运行中的进程
ps aux | grep "collab_overcooked.main"

# 或者
pgrep -f "collab_overcooked.main"
```

### 停止进程
```bash
# 找到进程ID
ps aux | grep "collab_overcooked.main"

# 停止进程（替换 PID 为实际进程ID）
kill PID

# 强制停止
kill -9 PID
```

## 方法2: 使用 screen（适合长时间运行）

### 安装 screen（如果未安装）
```bash
# macOS
brew install screen

# Linux
sudo apt-get install screen
```

### 使用 screen
```bash
# 创建新的 screen 会话
screen -S overcooked

# 在 screen 中运行程序
python -m collab_overcooked.main --config configs/default.yaml

#  detach（分离，程序继续运行）
# 按 Ctrl+A，然后按 D

# 重新连接
screen -r overcooked

# 列出所有 screen 会话
screen -ls

# 结束 screen 会话（在 screen 内）
exit
```

## 方法3: 使用 tmux（更现代的选择）

### 安装 tmux（如果未安装）
```bash
# macOS
brew install tmux

# Linux
sudo apt-get install tmux
```

### 使用 tmux
```bash
# 创建新的 tmux 会话
tmux new -s overcooked

# 在 tmux 中运行程序
python -m collab_overcooked.main --config configs/default.yaml

# detach（分离，程序继续运行）
# 按 Ctrl+B，然后按 D

# 重新连接
tmux attach -t overcooked

# 列出所有 tmux 会话
tmux ls

# 结束 tmux 会话（在 tmux 内）
exit
```

## 方法4: 简单的后台运行（不推荐用于长时间运行）

```bash
# 后台运行，输出到文件
python -m collab_overcooked.main --config configs/default.yaml > output.log 2>&1 &

# 查看进程ID
echo $!

# 查看输出
tail -f output.log
```

## 推荐方案

### 对于你的情况（计算 planner 需要很长时间）

**推荐使用 nohup**：

```bash
# 1. 进入项目目录
cd /Users/lijiayi/Desktop/毕业设计/code/Collab-Overcooked

# 2. 后台运行
nohup python -m collab_overcooked.main --config configs/default.yaml > planner_compute.log 2>&1 &

# 3. 记录进程ID（方便后续管理）
echo $! > planner.pid

# 4. 实时查看进度
tail -f planner_compute.log
```

### 查看进度
```bash
# 实时查看（推荐）
tail -f planner_compute.log

# 查看最后50行
tail -n 50 planner_compute.log

# 搜索特定内容（如进度）
grep "进度" planner_compute.log
```

### 检查是否完成
```bash
# 检查进程是否还在运行
ps -p $(cat planner.pid)

# 或者
ps aux | grep "collab_overcooked.main" | grep -v grep

# 检查日志中是否有完成信息
grep "计算完成\|已保存" planner_compute.log
```

### 停止程序
```bash
# 如果保存了进程ID
kill $(cat planner.pid)

# 或者找到进程ID后停止
ps aux | grep "collab_overcooked.main" | grep -v grep | awk '{print $2}' | xargs kill
```

## 完整示例脚本

创建一个 `run_background.sh` 脚本：

```bash
#!/bin/bash

# 进入项目目录
cd /Users/lijiayi/Desktop/毕业设计/code/Collab-Overcooked

# 创建日志目录
mkdir -p logs

# 生成日志文件名（带时间戳）
LOG_FILE="logs/planner_$(date +%Y%m%d_%H%M%S).log"

# 后台运行
nohup python -m collab_overcooked.main --config configs/default.yaml > "$LOG_FILE" 2>&1 &

# 保存进程ID
echo $! > planner.pid

echo "程序已在后台运行"
echo "进程ID: $(cat planner.pid)"
echo "日志文件: $LOG_FILE"
echo ""
echo "查看日志: tail -f $LOG_FILE"
echo "停止程序: kill \$(cat planner.pid)"
```

使用：
```bash
chmod +x run_background.sh
./run_background.sh
```

## 注意事项

1. **磁盘空间**：确保有足够的磁盘空间保存日志文件
2. **内存**：5个玩家的 planner 计算可能需要较多内存
3. **时间**：首次计算可能需要10-15分钟，请耐心等待
4. **检查完成**：计算完成后，缓存文件会保存在 `dependencies/overcooked_ai/overcooked_ai_py/data/planners/multi_agent_map_am.pkl`

## 快速命令总结

```bash
# 后台运行
nohup python -m collab_overcooked.main --config configs/default.yaml > planner.log 2>&1 &

# 查看进度
tail -f planner.log

# 检查是否完成
grep "计算完成\|已保存" planner.log

# 停止程序
pkill -f "collab_overcooked.main"
```
