## 1. 配置变更

- [x] 1.1 `config.py`：移除 `CONTEXT_PRUNE_TRIGGER = 73_728` 和 `CONTEXT_SUMMARIZE_TRIGGER = 88_473`
- [x] 1.2 `config.py`：新增 `COMPACTION_BUFFER: int = 20_000` 和 `HISTORY_TOKEN_BUDGET: int = 60_000`

## 2. 核心逻辑简化

- [x] 2.1 `react_engine.py`：移除 Think 后的 `if prompt_tokens > CONTEXT_PRUNE_TRIGGER` 分支（L1 额外触发）
- [x] 2.2 `react_engine.py`：将 Think 后的 `if prompt_tokens > CONTEXT_SUMMARIZE_TRIGGER` 改为 `if remaining < COMPACTION_BUFFER`（溢出检测）
- [x] 2.3 `react_engine.py`：确认每步 Act 后无条件 L1 保留不变

## 3. 历史加载修复

- [x] 3.1 `react_engine.py`：`_select_history_by_token()` 将预算从 `PRUNE_PROTECT_TOKENS` 改为 `HISTORY_TOKEN_BUDGET`

## 4. 上下文用量推送（context_usage）

- [x] 4.1 `react_engine.py`：Think 后计算 `context_usage` dict（prompt_tokens / remaining / percent / limit）
- [x] 4.2 `react_engine.py`：`execute_stream()` 中 Think 后 yield `{"event": "context_usage", "data": context_usage}`
- [x] 4.3 `react_engine.py`：`execute()` 中记录最后一步 context_usage，挂载到 `ExecutionResult`
- [x] 4.4 `schemas.py`：`ExecutionResult` 新增 `context_usage: dict | None = None` 字段
- [x] 4.5 `chat.py`：`chat_stream()` 转发 `context_usage` SSE 事件（与 thought/tool_call 同级）
- [x] 4.6 `chat.py`：`ChatResponse` 新增 `context_usage: dict | None = None` 字段，非流式 chat() 透传

## 5. 验证

- [x] 5.1 检查 `CONTEXT_PRUNE_TRIGGER` 和 `CONTEXT_SUMMARIZE_TRIGGER` 在全项目无残留引用
- [x] 5.2 验证 FastAPI 启动正常
- [x] 5.3 走读简化后的 ReAct 循环逻辑，确认流程正确：Think → context_usage yield → 溢出检查 → is_done → Act → L1 无条件
