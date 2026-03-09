# Context Pruning Spec — Level 1 双层剪枝

## 1. 概述

Level 1 剪枝（Context Pruning）通过标记而非删除的方式持续压缩旧 tool result，维持 context 在可控范围内。包含内存级（单次执行内）和 DB 级（跨轮持久化）两个维度。

---

## 2. 内存级剪枝

### 触发时机

- **每步 Act 后无条件执行**（每步保洁，低成本）

### 算法（token 估算边界，替代旧的步骤计数）

```python
def _compress_stale_tool_results(messages: list[dict]) -> list[dict]:
    # 从 messages 尾部往前累加 tool result 的 len(content) // 2
    # 累积超出 PRUNE_PROTECT_TOKENS（20,000）的 tool result 替换为占位符
    # skill_call 的 tool result 始终保留（不被替换）
    placeholder = "[已处理] {tool_name} 输出已压缩（原始内容保留在历史记录中）"
```

### skill_call 保护

`skill_call` 工具的 tool result 始终完整保留，因为 Skill 的执行结果通常是工作流关键输出，不应被压缩。

---

## 3. 历史加载预算

`_select_history_by_token()` 使用独立的 `HISTORY_TOKEN_BUDGET`（60,000）作为历史消息加载预算，不再复用 `PRUNE_PROTECT_TOKENS`。

```python
def _select_history_by_token(self, history_messages: list[dict]) -> list[dict]:
    budget = settings.HISTORY_TOKEN_BUDGET  # 60,000（独立预算）
    selected = []
    accumulated = 0
    for msg in reversed(history_messages):
        token_est = self._estimate_tokens(msg.get("content") or "")
        if accumulated + token_est <= budget:
            accumulated += token_est
            selected.insert(0, msg)
        else:
            break
    return selected
```

---

## 4. DB 级剪枝

### 触发时机

每次 `/chat` 请求完成、l3_steps 写入 PG 后，无条件运行。

### 算法

```python
async def _prune_l3_steps(session_id: str) -> None:
    steps = await chat_persistence.load_l3_steps(session_id)
    # 从最新步骤往前累加 tool result 的 len(content) // 2
    # 超出 PRUNE_PROTECT_TOKENS 的步骤调用 mark_steps_compacted()
    # 已 compacted 步骤按占位符长度计算（约 10 token）
```

- 批量 UPDATE `compacted=True`（单次 DB 操作）
- 发后即忘（fire-and-forget），失败不影响主流程
- 原始内容保留在 DB，UI 回看历史时可展示原始内容

### compacted 内容注入 LLM 时的替换

`load_history()` 还原 l3_steps 为 LLM messages 时，`compacted=True` 的 tool result 替换为：
```
[已处理] {tool_name} 输出已压缩（原始内容保留在历史记录中）
```

---

## 5. Token 估算策略

| 场景 | 方法 |
|------|------|
| Level 2 触发判断 | `think_result.usage["prompt_tokens"]`（服务端精确值） |
| 保护区边界计算 | `len(content) // 2`（字符估算，中文 ≈ 1.92 chars/token） |

---

## 6. 配置参数

| 参数 | 值 | 说明 |
|------|---|------|
| `PRUNE_PROTECT_TOKENS` | 20,000 | 保护区 token 数（最近步骤不被剪枝） |
| `HISTORY_TOKEN_BUDGET` | 60,000 | 历史消息加载预算（独立于保护区） |
