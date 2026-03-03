## Why

L3 ReAct 引擎目前缺乏上下文管理机制：单次执行内的 tool results 无限累积，跨轮对话的中间步骤全部丢失，导致长任务上下文断裂和模型 context window 溢出风险（实测上限 98,304 token）。当前以 `max_iterations=20` 和 `[-10:]` 硬截断作为粗暴替代，无法支撑复杂多轮分析场景。

## What Changes

- **新增** L3 中间步骤持久化：每步 `assistant(tool_calls)` + `tool(result)` 写入 PG，支持跨轮上下文连续
- **新增** Level 1 持续剪枝：每轮对话结束后无条件运行，将超出 PRUNE_PROTECT(20K) 的旧 tool results 标记 `compacted=True`，发给 LLM 时替换为占位符
- **新增** Level 2 摘要截断：prompt_tokens 超 90% 阈值（88,473）时触发，生成结构化摘要存入 PG（`is_compaction=True`），作为新的历史起点
- **修改** 历史消息加载：`load_history` 遇到 `is_compaction=True` 消息停止往前读；`compacted=True` 的 tool results 替换为占位符
- **修改** `_build_initial_messages()`：history 包含完整中间步骤（含 tool calls/results），移除 `[-10:]` 硬截断，改为基于 token 估算的动态加载
- **修改** `_compress_stale_tool_results()`：升级为基于 token 估算（chars // 2）的 PRUNE_PROTECT 边界计算，替代现有按步骤数的窗口逻辑
- **新增** PG schema：`chat_messages` 表新增 `is_compaction` 字段；新增 `l3_steps` 表存储中间步骤

## Capabilities

### New Capabilities

- `l3-step-persistence`: L3 ReAct 中间步骤（tool calls/results）持久化到 PG，支持跨轮加载还原
- `context-pruning`: Level 1 持续剪枝——每轮结束后对旧 tool results 打 compacted 标记，发给 LLM 时替换占位符
- `context-compaction`: Level 2 摘要截断——溢出时生成结构化摘要，设立新历史起点（genesis block）

### Modified Capabilities

- `memory-system`: WorkingMemory 的历史加载逻辑变更——支持 compaction 节点过滤、compacted tool results 替换、动态 token 边界加载
- `execution-engine`: L3 initial messages 构建逻辑变更——历史包含中间步骤，压缩逻辑升级为 token 估算

## Impact

**代码改动：**
- `app/execution/l3/react_engine.py`：`_build_initial_messages()`、`_compress_stale_tool_results()`、新增 `_compact_messages()`
- `app/memory/working_memory.py`：`load_history()` 加载逻辑、新增剪枝调用
- `app/memory/schemas.py`：`Message` 模型新增 `is_compaction`、`compacted` 字段
- `app/memory/chat_persistence.py`：支持写入/读取 l3_steps
- `app/api/chat.py`：每轮结束后触发 Level 1 剪枝

**数据库：**
- `chat_messages` 表：新增 `is_compaction BOOLEAN DEFAULT FALSE`
- 新增 `l3_steps` 表：存储每步 tool call/result，关联 session_id + message_id
- Alembic 迁移

**配置（config.py）：**
- `MODEL_CONTEXT_LIMIT = 98_304`
- `CONTEXT_PRUNE_TRIGGER = 73_728`（75%）
- `CONTEXT_SUMMARIZE_TRIGGER = 88_473`（90%）
- `PRUNE_PROTECT_TOKENS = 20_000`
- `COMPRESS_MIN_SAVING = 10_000`
- `COMPACTION_MAX_TOKENS = 2_000`
