## Why

当前上下文压缩机制（"双层漏斗"）存在设计缺陷：`_select_history_by_token()` 复用 `PRUNE_PROTECT_TOKENS = 20,000` 作为历史加载预算，导致入口处消息量被限死在 ~26K token（仅占 98K 上下文的 26%），75% 和 90% 两个阈值检查永远无法触发，Level 2 摘要截断沦为死代码。此外双阈值架构（75% L1 额外触发 + 90% L2 触发）过度复杂，偏离了 OpenCode 参考设计的简洁模型。

## What Changes

- **修改** `_select_history_by_token()`：使用独立的历史加载预算（基于 `MODEL_CONTEXT_LIMIT` 减去系统预留），不再复用 `PRUNE_PROTECT_TOKENS`
- **移除** 75% 阈值（`CONTEXT_PRUNE_TRIGGER`）：去掉 Think 后的 L1 额外触发，因为 L1 每步 Act 后已无条件运行
- **简化** 90% 阈值为溢出检测：`remaining_tokens < COMPACTION_BUFFER` 时触发 Level 2，概念更清晰
- **移除** `CONTEXT_PRUNE_TRIGGER` 配置常量
- **新增** `COMPACTION_BUFFER` 配置常量（替代百分比阈值，语义更明确）
- **新增** `HISTORY_TOKEN_BUDGET` 配置常量（历史加载独立预算）
- **移除** `CONTEXT_SUMMARIZE_TRIGGER` 配置常量（被 `COMPACTION_BUFFER` 替代）
- **更新** Level 1 内存级剪枝：保留每步 Act 后无条件运行（对 98K 上下文必要），但去掉阈值触发路径
- **更新** Level 1 DB 级剪枝：每轮对话结束后无条件运行（不变）
- **新增** 上下文用量推送：每步 Think 后计算 `context_usage`（prompt_tokens / remaining / percent / limit），流式通过 SSE `context_usage` 事件推送，非流式通过 `ChatResponse.context_usage` 返回，供前端显示剩余 token 百分比

## Capabilities

### New Capabilities

- `context-usage-reporting`：前端可感知的上下文用量推送（SSE 事件 + API 响应字段）

### Modified Capabilities

- `context-compaction`：Level 2 触发条件从百分比阈值改为剩余空间检测
- `context-pruning`：移除 75% 阈值触发路径，保留每步 Act 后无条件运行
- `execution-engine`：简化 ReAct 循环内的压缩流程，移除双阈值架构

## Impact

**代码改动：**
- `app/execution/l3/react_engine.py`：简化循环内压缩逻辑、修改 `_select_history_by_token()` 预算、Think 后 yield/存储 `context_usage`
- `app/execution/schemas.py`：`ExecutionResult` 新增 `context_usage` 字段
- `app/api/chat.py`：流式转发 `context_usage` SSE 事件、非流式 `ChatResponse` 新增 `context_usage` 字段
- `app/config.py`：移除 `CONTEXT_PRUNE_TRIGGER` / `CONTEXT_SUMMARIZE_TRIGGER`，新增 `COMPACTION_BUFFER` / `HISTORY_TOKEN_BUDGET`

**配置变更：**
- 移除：`CONTEXT_PRUNE_TRIGGER = 73_728`、`CONTEXT_SUMMARIZE_TRIGGER = 88_473`
- 新增：`COMPACTION_BUFFER = 20_000`、`HISTORY_TOKEN_BUDGET = 60_000`
- 不变：`MODEL_CONTEXT_LIMIT`、`PRUNE_PROTECT_TOKENS`、`COMPRESS_MIN_SAVING`、`COMPACTION_MAX_TOKENS`

**Spec 更新：**
- `context-compaction.md`、`context-pruning.md`、`execution-engine.md` 需同步更新
- 新增 `context-usage-reporting` spec
