# L3 Step Persistence Spec — 中间步骤持久化

## 1. 概述

L3 中间步骤持久化将每次 L3 ReAct 执行产生的所有中间步骤（assistant tool_calls 消息 + tool result 消息）写入独立的 `l3_steps` 表，实现跨轮 context 连续性，支持 Level 1 DB 级剪枝。

---

## 2. 数据收集

### 非流式执行（`execute()`）

```python
collected_steps: list[dict] = []
for step in range(max_iterations):
    act_result = await self.actor.act(think_result)
    for msg in act_result.messages:
        collected_steps.append(msg)  # 原始 LLM 格式消息

result.l3_steps = self._convert_steps(collected_steps)  # 挂载到 ExecutionResult
```

### 流式执行（`execute_stream()`）

```python
collected_steps: list[dict] = []
# ... 循环中收集 ...
finally:
    self.last_l3_steps = self._convert_steps(collected_steps)  # 存引擎实例变量
```

`chat_stream()` 在 SSE 流完成后读取 `execution_router.l3.last_l3_steps` 触发持久化，与非流式路径对称。

---

## 3. 格式转换（`_convert_steps()`）

原始 LLM 格式 → l3_steps 存储格式：

| 消息类型 | role | content | tool_name | tool_call_id |
|---------|------|---------|-----------|-------------|
| assistant tool_calls | assistant | JSON(tool_calls)（若 content 为空） | null | null |
| tool result | tool | result 内容 | 工具名 | call_id |

---

## 4. 写入 PG

```python
# 发送后即忘，失败不影响主流程
chat_persistence.save_l3_steps_background(session_id, message_id, l3_step_objs)
```

- 关联 `message_id`（本轮 assistant 最终消息的 ID）
- `step_index` 从 0 递增，按顺序排列
- 写入后触发 Level 1 DB 剪枝：`asyncio.create_task(_prune_l3_steps(session_id))`

---

## 5. 跨轮加载（`load_history()` + `load_l3_steps()`）

```python
# load_history() 流程：
# 1. 倒序扫描 chat_messages，遇到 is_compaction=True 停止
# 2. 记录 genesis block 的 created_at 时间戳
# 3. 加载 genesis block 之后的 l3_steps（load_l3_steps(session_id, after_created_at=...)）
# 4. 将 l3_steps 还原为 LLM messages 格式（compacted=True → 占位符）
# 5. 老会话（无 l3_steps）降级为 user/assistant 文本 + [-10:] 模式
```

还原的 l3_steps 作为 `history_messages` 的一部分，注入 L3 初始 messages，实现跨轮推理连续性。

---

## 6. 存储策略

| 方面 | 决策 |
|------|------|
| 独立 l3_steps 表 | 不污染 chat_messages，关注点分离 |
| 写入时机 | 每次 L3 执行完成后（非 L1/L2 路由） |
| 失败处理 | 异步 fire-and-forget，捕获异常记录 warning |
| 保留策略 | 与 chat_messages 同生命周期（无独立 TTL） |
| 索引 | `(session_id, created_at)`、`(session_id, compacted)` |
