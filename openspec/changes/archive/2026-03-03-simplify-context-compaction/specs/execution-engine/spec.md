## MODIFIED Requirements

### Requirement: Context 压缩机制

防止长对话 context 膨胀，参照 OpenCode 的简洁模型：

**Level 1 内存级剪枝（`_compress_stale_tool_results()`，每步 Act 后无条件运行）**

基于 token 估算（`len(content) // 2`）的保护区边界：
- 从 messages 尾部往前累加 tool result 的 token 估算
- 超出 `PRUNE_PROTECT_TOKENS`（20,000）的 tool result 内容替换为占位符
- `skill_call` 的 tool result 始终保留（不被替换）
- 每步 Act 后无条件执行（每步保洁）

**Level 2 摘要截断（`_compact_messages()`，上下文剩余空间不足时触发）**

- Think 后计算 `remaining = MODEL_CONTEXT_LIMIT - prompt_tokens`
- `remaining < COMPACTION_BUFFER`（20,000）时触发
- 识别保护区（从尾部累积 PRUNE_PROTECT_TOKENS），提取可压缩区
- 调用 LLM 生成结构化摘要（max_tokens=2,000）
- 重建 messages：`[system] → [user: 历史摘要] → [保护区消息]`
- 摘要暂存于 `self.last_compaction_summary`，供 chat.py 持久化为 genesis block

**Token 计数策略（混合精确+估算）**

| 用途 | 方法 |
|------|------|
| Level 2 触发判断 | `think_result.usage["prompt_tokens"]`（服务端精确值） |
| 保护区边界计算 | `len(content) // 2`（字符估算，允许 ±20% 误差） |

#### Scenario: ReAct 循环内简化压缩流程
- **WHEN** L3 ReAct 循环每步执行
- **THEN** Think 后仅检查 Level 2 溢出（`remaining < COMPACTION_BUFFER`），Act 后仅执行无条件 Level 1，无其他阈值检查

#### Scenario: 历史加载充分利用上下文
- **WHEN** `_build_initial_messages()` 加载历史
- **THEN** 使用 `HISTORY_TOKEN_BUDGET`（60,000）作为预算，允许首次 Think 看到 ~66K prompt_tokens

### Requirement: L3 配置参数

| 参数 | 默认值 | 说明 |
|------|-------|------|
| `L3_MAX_ITERATIONS` | 20 | ReAct 循环最大步数 |
| `L3_TIMEOUT_SECONDS` | 300.0 | 整体超时（秒） |
| `L3_MAX_LLM_CALLS` | 50 | LLM 调用次数上限 |
| `MODEL_CONTEXT_LIMIT` | 98,304 | 模型 context 上限（token，实测精确值） |
| `COMPACTION_BUFFER` | 20,000 | Level 2 触发阈值（剩余空间低于此值触发摘要） |
| `PRUNE_PROTECT_TOKENS` | 20,000 | 保护区 token 数（最近步骤不被剪枝） |
| `HISTORY_TOKEN_BUDGET` | 60,000 | 历史消息加载预算 |
| `COMPRESS_MIN_SAVING` | 10,000 | Level 2 摘要后至少需节省的 token 数 |
| `COMPACTION_MAX_TOKENS` | 2,000 | 摘要生成 max_tokens |

#### Scenario: 配置参数变更
- **WHEN** 系统升级到简化压缩机制
- **THEN** `CONTEXT_PRUNE_TRIGGER` 和 `CONTEXT_SUMMARIZE_TRIGGER` 被移除，由 `COMPACTION_BUFFER` 和 `HISTORY_TOKEN_BUDGET` 替代

### Requirement: 流式执行 SSE 事件

SSE 事件格式（新增 `context_usage`）：
```json
{"event": "thought",        "data": {"step": 0, "content": "我需要先搜索..."}}
{"event": "context_usage",  "data": {"prompt_tokens": 52000, "remaining": 46304, "percent": 52.9, "limit": 98304}}
{"event": "tool_call",      "data": {"step": 0, "name": "web_search", "args": {...}}}
{"event": "tool_result",    "data": {"step": 0, "name": "web_search", "result": "..."}}
{"event": "delta",          "data": "最终回答的文本片段"}
{"event": "finish",         "data": {"iterations": 3, "llm_calls": 5}}
```

`context_usage` 事件在每步 Think 后、tool_call 之前推送。

#### Scenario: 流式事件顺序
- **WHEN** L3 流式执行一步完整迭代
- **THEN** 事件顺序为：`thought` → `context_usage` → `tool_call` → `tool_result`

### Requirement: ExecutionResult 上下文用量

`ExecutionResult` 新增字段：

```python
context_usage: dict | None = None  # 最后一步 Think 的上下文用量快照
```

#### Scenario: L3 执行携带 context_usage
- **WHEN** L3 非流式执行完成
- **THEN** `ExecutionResult.context_usage` 包含最后一步 Think 的 `prompt_tokens / remaining / percent / limit`

## REMOVED Requirements

### Requirement: 双阈值触发架构（75% L1 + 90% L2）
**Reason**: 75% 阈值触发在 L1 每步无条件运行的前提下多余；90% 百分比阈值被更直观的 `remaining < COMPACTION_BUFFER` 替代。
**Migration**: 移除循环内 `if prompt_tokens > CONTEXT_PRUNE_TRIGGER` 分支，将 `if prompt_tokens > CONTEXT_SUMMARIZE_TRIGGER` 替换为 `if remaining < COMPACTION_BUFFER`。
