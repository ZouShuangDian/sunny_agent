# Context Compaction Spec — Level 2 摘要截断

## 1. 概述

Level 2 摘要截断（Context Compaction）在 L3 ReAct 单次执行的 context 剩余空间不足时触发，通过 LLM 生成结构化摘要压缩历史，重建 messages 列表以避免 context 溢出。

---

## 2. 触发条件

每步 Think 完成后，读取 `think_result.usage["prompt_tokens"]`，计算剩余空间：

```python
remaining = MODEL_CONTEXT_LIMIT - prompt_tokens
if remaining < COMPACTION_BUFFER:
    messages = await self._compact_messages(messages)
```

触发值使用服务端精确值（非估算），保证触发准确。

`COMPACTION_BUFFER`（20,000）定义"剩余空间不足"的阈值，语义为"预留给后续操作的最小空间"。实际触发点为 `prompt_tokens > 78,304`（≈80% of 98,304）。

---

## 3. 摘要生成

### 保护区划定

从 messages 尾部往前累积 token 估算（`len(content) // 2`），累积量 ≤ `PRUNE_PROTECT_TOKENS`（20,000）的消息为保护区，其余为可压缩区。

### LLM 调用

```python
summary_messages = [{"role": "user", "content": f"{COMPACTION_PROMPT}\n\n{history_text}"}]
result = await self.llm.chat(messages=summary_messages, max_tokens=COMPACTION_MAX_TOKENS)
```

摘要 prompt（COMPACTION_PROMPT）结构化要求：
1. 任务目标
2. 已完成的操作步骤
3. 重要发现和结论
4. 操作过的文件、路径、数据
5. 涉及的业务实体（产品型号、工单号、设备编号、指标名称等）
6. 当前状态与下一步计划

### 失败降级

摘要 LLM 调用失败（超时/网络错误/空内容）时：
- 记录 warning 日志
- 跳过压缩，返回原 messages 继续执行（接受超限风险）

---

## 4. Messages 重建

```
重建后结构：[system prompt] → [user: 历史摘要] → [保护区消息]
```

摘要以 `role=user` 注入（非 assistant），携带明确框架说明：
```
【系统自动生成的历史摘要】
以下内容由系统生成，帮助你了解之前的对话背景，请基于此继续执行任务。

{summary_content}

---
请继续基于以上历史背景执行当前任务。
```

使用 `role=user` 的原因：保护区第一条消息通常是 `assistant(tool_calls)`，若摘要也用 `role=assistant` 会产生连续 assistant 消息，违反 API 格式。

---

## 5. Genesis Block 持久化

摘要生成成功后：
- 存储于 `self.last_compaction_summary`（引擎实例变量）
- `chat.py` 在执行完成后读取并写入 PG：
  - `role=assistant`，`is_compaction=True`（PG 存储格式）
  - write-behind 异步写入
- 该记录成为新历史起点，下次 `load_history()` 遇到此记录即停止往前加载

---

## 6. 配置参数

| 参数 | 值 | 说明 |
|------|---|------|
| `MODEL_CONTEXT_LIMIT` | 98,304 | 模型 context 上限（token） |
| `COMPACTION_BUFFER` | 20,000 | 剩余空间阈值（低于此值触发 Level 2） |
| `PRUNE_PROTECT_TOKENS` | 20,000 | 保护区 token 数 |
| `COMPACTION_MAX_TOKENS` | 2,000 | 摘要生成 max_tokens |
| `COMPRESS_MIN_SAVING` | 10,000 | 摘要后至少节省 token 数 |
