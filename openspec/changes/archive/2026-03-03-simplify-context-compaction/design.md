## Context

**当前状态：**
- L3 ReAct 引擎采用"双层漏斗"压缩架构：75% 阈值触发 L1 额外剪枝 + 90% 阈值触发 L2 摘要截断
- `_select_history_by_token()` 复用 `PRUNE_PROTECT_TOKENS = 20,000` 作为历史加载预算
- 实际运行时消息量始终 ~26K（26% of 98K），75% 和 90% 阈值永远不触发
- Level 2 摘要截断是死代码

**参考设计：** OpenCode（Claude 200K 上下文）采用简洁模型：
- 每轮结束后无条件 prune（tool outputs > 40K 时标记旧的）
- 溢出时触发 compaction（剩余空间不足时 LLM 摘要）
- 无百分比阈值，无双层触发

**约束：**
- 我们的模型上下文仅 98K（OpenCode 为 200K），单次大文件读取（20K+）即可消耗 20% 上下文
- 因此不能完全去掉循环内的 L1 剪枝（否则几步 Act 就会溢出），需保留每步 Act 后的无条件 L1
- 改动应最小化：保留现有 `_compress_stale_tool_results()` 和 `_compact_messages()` 的核心逻辑，只改触发条件和历史加载预算

---

## Goals / Non-Goals

**Goals:**
- 修复 `_select_history_by_token()` 的预算问题，让历史加载能充分利用上下文空间
- 简化压缩触发逻辑：去掉双阈值，改为单一溢出检测
- 让 Level 2 摘要截断在实际运行中可被触发
- 配置语义更清晰：`COMPACTION_BUFFER`（剩余空间阈值）替代 `CONTEXT_SUMMARIZE_TRIGGER`（百分比阈值）

**Non-Goals:**
- 不改变 `_compress_stale_tool_results()` 的核心算法（token 估算 + 保护区）
- 不改变 `_compact_messages()` 的核心逻辑（LLM 摘要 + messages 重建）
- 不改变 l3_steps 持久化机制
- 不改变 DB 级 Level 1 剪枝（每轮结束后标记 compacted）
- 不改变 genesis block 存储格式

---

## Decisions

### Decision 1：历史加载预算独立化

**选择：** 新增 `HISTORY_TOKEN_BUDGET = 60_000` 常量，`_select_history_by_token()` 使用该值

**为什么 60K：**
- 98K 总上下文 - 5K system prompt - 1K user input - 20K 预留给 ReAct 步骤 - 12K 安全余量 ≈ 60K
- 允许首次 Think 看到 ~66K prompt_tokens，为 Level 2 触发留出空间
- 对比旧值 20K：历史容量扩大 3 倍，长对话上下文连续性显著提升

**为什么不直接用 MODEL_CONTEXT_LIMIT 动态计算：**
- system prompt 长度受 todo 注入、plugin context 等影响，运行时不确定
- 固定值更可预测，且 COMPACTION_BUFFER 提供了溢出安全网

---

### Decision 2：单一溢出检测替代双阈值

**选择：** 移除 `CONTEXT_PRUNE_TRIGGER`（75%）和 `CONTEXT_SUMMARIZE_TRIGGER`（90%），新增 `COMPACTION_BUFFER = 20_000`

**新触发逻辑（Think 后）：**
```python
prompt_tokens = think_result.usage.get("prompt_tokens", 0)
remaining = settings.MODEL_CONTEXT_LIMIT - prompt_tokens
if remaining < settings.COMPACTION_BUFFER:
    messages = await self._compact_messages(messages)
```

**为什么只保留一个检查：**
- 旧 75% 检查：L1 已在每步 Act 后无条件运行，额外触发多余
- 旧 90% 检查：语义上就是"快溢出了"，`remaining < buffer` 表达更直接
- 一个检查 = 一个决策点 = 更容易理解和调试

**为什么 COMPACTION_BUFFER = 20K：**
- 需要为 Level 2 的 LLM 摘要调用本身预留空间（摘要 prompt + 输入 + 输出）
- 20K 与 OpenCode 的 `COMPACTION_BUFFER = 20_000` 一致
- 触发时机：`prompt_tokens > 78,304`（≈80% of 98K），比旧 90% 更早触发，安全性更高

---

### Decision 3：保留每步 Act 后无条件 L1

**选择：** 保留 `_compress_stale_tool_results(messages)` 在每步 Act 后无条件运行

**为什么不像 OpenCode 一样只在轮间运行：**
- OpenCode 模型 200K 上下文，一次执行内即使 20 步也只占 ~30% 上下文
- 我们 98K 上下文，一次 `read_file` 大文件 = 20K，3-4 步就可能溢出
- 每步清理是 98K 上下文的必要保护，代价极低（纯内存操作）

**变化点：** 只是去掉了 Think 后的 75% 额外触发（因为 Act 后已无条件运行），L1 的核心行为不变。

---

### Decision 4：上下文用量前端推送（context_usage）

**选择：** 流式 + 非流式双通道推送，数据在 Think 后计算

**数据结构：**
```python
context_usage = {
    "prompt_tokens": 52000,     # 本次 Think 实际消耗
    "remaining": 46304,         # 剩余可用
    "percent": 52.9,            # 已用百分比
    "limit": 98304,             # 模型上限
}
```

**流式（SSE）：** 每步 Think 后 yield 新事件类型：
```python
yield {"event": "context_usage", "data": context_usage}
```
前端每步收到即更新进度条，可实时观察 context 消耗变化。

**非流式：**
- `ExecutionResult` 新增 `context_usage: dict | None` 字段，存储最后一步 Think 的用量
- `ChatResponse` 新增 `context_usage: dict | None` 字段，直接透传给前端

**为什么用最后一步的值（非流式）：**
- 非流式无法逐步推送，最后一步的 `prompt_tokens` 是最具参考价值的（反映执行结束时的 context 状态）
- 前端可据此判断"本轮对话后，上下文还剩多少空间"

**为什么不新建独立端点查询：**
- context_usage 与执行过程强绑定（只有 Think 返回后才有精确值）
- 嵌入现有事件流/响应比独立端点更自然、延迟更低

---

## Risks / Trade-offs

**[风险 1] 历史加载过大导致首次 Think 就触发 Level 2** → 如果历史消息恰好填满 60K 预算，加上 system prompt 和 user input，首次 Think 可能 ~66K。距离触发点 78K 还有 12K 余量，正常情况下不会触发。即使触发，Level 2 摘要本身就是正确行为（历史太长需要压缩）。

**[风险 2] HISTORY_TOKEN_BUDGET = 60K 对某些场景偏大或偏小** → 作为固定值，无法适应所有场景。但配合 COMPACTION_BUFFER 安全网，偏大只意味着更早触发 Level 2（非灾难性），偏小意味着历史较短（可后续调优）。

**[Trade-off] 与 OpenCode 的差异** → 因 98K 限制保留了每步 L1，这是与 OpenCode（轮间 prune only）的唯一差异。如果未来切换到更大上下文的模型，可以考虑去掉每步 L1。

---

## Migration Plan

1. **配置变更**：`config.py` 移除 2 个常量、新增 2 个常量（纯加减法，无依赖）
2. **代码改动**：`react_engine.py` 修改循环内逻辑 + `_select_history_by_token()` 预算
3. **无数据库变更**：不涉及表结构或迁移
4. **回滚**：恢复旧常量和旧逻辑即可，无持久化副作用
