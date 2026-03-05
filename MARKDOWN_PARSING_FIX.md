# Markdown 格式解析修复

## 🎯 问题描述

A0 (Chef) 发送的 Collab 指令格式有问题，导致解析失败：

### 问题现象

1. **LLM 输出包含 Markdown 格式**：
   - `**Recent Goal:**`
   - `**Action:**`
   - `Action: **` (导致解析失败)
   - `[** `collab(request(a1, place_obj_on_counter()))]`

2. **解析失败**：
   - `Action: **` 被解析为动作，但 `**` 不是有效动作
   - 导致 `parse_ml_action_top` 返回 `wait(1)`
   - Collab 指令没有被正确提取
   - A1 拿着蛋等待，但 Chef 陷入格式错误循环

3. **错误信息**：
   ```
   Action: **
   Error: Please ensure the Action field lists semicolon-separated function calls without extra narration.
   ```

## ✅ 修复方案

### 1. 增强 `_sanitize_action_text` 方法

**文件**: `collab_overcooked/agents/collab.py` (第1578-1590行)

**修改内容**：
- 添加 Markdown 格式移除逻辑
- 移除 `**bold**`、`*italic*`、`_underline_` 等格式
- 移除独立的 `**` 和 `*` 符号

**代码**：
```python
def _sanitize_action_text(self, text: Optional[str]) -> str:
    if not isinstance(text, str):
        return ""
    cleaned = text.replace("```", "").replace("```]", "")
    cleaned = cleaned.replace("[```", "").replace("```", "")
    cleaned = cleaned.strip()
    # Remove Markdown formatting: **bold**, *italic*, _underline_, etc.
    cleaned = re.sub(r'\*\*([^*]+)\*\*', r'\1', cleaned)  # Remove **bold**
    cleaned = re.sub(r'\*([^*\s]+)\*', r'\1', cleaned)  # Remove *italic*
    cleaned = re.sub(r'_\b([^_]+)\b_', r'\1', cleaned)  # Remove _underline_
    cleaned = re.sub(r'\*\*', '', cleaned)  # Remove any remaining **
    cleaned = re.sub(r'(?<!\w)\*(?!\w)', '', cleaned)  # Remove standalone *
    # ... (rest of the function)
    cleaned = cleaned.strip()
    return cleaned
```

### 2. 改进 `parse_response` 的 Action 提取逻辑

**文件**: `collab_overcooked/agents/collab.py` (第2525-2543行)

**修改内容**：
- 添加对 `**Action:**` 格式的支持
- 改进正则表达式，更好地处理 Markdown 格式
- 在提取后验证内容不为空

**代码**：
```python
elif mode == "action":
    action_block = sections.get("action")
    if action_block:
        cleaned = self._sanitize_action_text(action_block)
        return f"Action: {cleaned}"
    # Try to extract Action field, handling Markdown formatting
    # Pattern 1: "Action:" or "**Action:**" followed by content
    action_pattern = r"(?:\*\*)?Action\s*:?\s*(?:\*\*)?\s*(.*?)(?=\n\s*(?:Recent Goal|Think|$))"
    match = re.search(action_pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        action_content = match.group(1).strip()
        action_content = re.sub(r'\*\*\s*$', '', action_content)  # Remove trailing **
        cleaned = self._sanitize_action_text(action_content)
        if cleaned:  # Only return if we found actual content
            return f"Action: {cleaned}"
    # Pattern 2: Simple "Action: ..." without markdown
    # ... (fallback patterns)
```

## 📊 修复效果

### 修复前

**LLM 输出**：
```
**Action:**

Collab(request(A1, place_obj_on_counter()))
```

**解析结果**：
- `Action: **` → 无效动作 → `wait(1)`
- Collab 指令丢失

### 修复后

**LLM 输出**：
```
**Action:**

Collab(request(A1, place_obj_on_counter()))
```

**解析结果**：
- `**Action:**` → 提取 `Collab(request(A1, place_obj_on_counter()))`
- `_sanitize_action_text` 移除 `**` → `Collab(request(A1, place_obj_on_counter()))`
- 正确识别 Collab 指令 ✅

## 🔍 测试场景

修复后应该能正确处理以下格式：

1. **标准格式**：
   ```
   Action: Collab(request(A1, place_obj_on_counter()))
   ```

2. **Markdown 格式**：
   ```
   **Action:**
   Collab(request(A1, place_obj_on_counter()))
   ```

3. **混合格式**：
   ```
   Action: **
   Collab(request(A1, place_obj_on_counter()))
   ```

4. **带代码块**：
   ```
   Action:
   ```
   Collab(request(A1, place_obj_on_counter()))
   ```
   ```

## ⚠️ 注意事项

1. **向后兼容**：修复保持向后兼容，不影响正常格式的解析
2. **性能影响**：添加了正则表达式处理，但影响很小
3. **边界情况**：如果 LLM 输出完全混乱，可能仍需要格式纠正机制

## 📝 相关代码位置

- `_sanitize_action_text`: 第1578-1590行
- `parse_response` (action mode): 第2525-2543行
- `parse_ml_action_top`: 第2882-2922行
