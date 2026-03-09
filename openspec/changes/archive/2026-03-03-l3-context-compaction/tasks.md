## 1. 配置与数据库准备

- [x] 1.1 `config.py` 新增 6 个常量：`MODEL_CONTEXT_LIMIT`、`CONTEXT_PRUNE_TRIGGER`、`CONTEXT_SUMMARIZE_TRIGGER`、`PRUNE_PROTECT_TOKENS`、`COMPRESS_MIN_SAVING`、`COMPACTION_MAX_TOKENS`
- [x] 1.2 新建 Alembic 迁移：`chat_messages` 表新增 `is_compaction BOOLEAN DEFAULT FALSE NOT NULL`
- [x] 1.3 新建 Alembic 迁移：新增 `l3_steps` 表（session_id、message_id、step_index、role、content、tool_name、tool_call_id、compacted、created_at）
- [x] 1.4 新增 `l3_steps` 表索引：`(session_id, created_at)`、`(session_id, compacted)`
- [x] 1.5 执行迁移并验证表结构

## 2. 数据模型更新

- [x] 2.1 `app/memory/schemas.py`：`Message` 模型新增 `is_compaction: bool = False` 字段
- [x] 2.2 `app/memory/schemas.py`：新增 `L3Step` Pydantic 模型（对应 l3_steps 表结构）
- [x] 2.3 `app/memory/schemas.py`：`ConversationHistory.to_llm_messages()` 支持 `is_compaction` 消息识别（作为加载停止标志）

## 3. 持久化层：l3_steps 读写

- [x] 3.1 `app/memory/chat_persistence.py`：新增 `save_l3_steps(session_id, message_id, steps: list[L3Step])` 方法（write-behind 异步）
- [x] 3.2 `app/memory/chat_persistence.py`：新增 `load_l3_steps(session_id, after_message_id=None)` 方法（加载指定消息之后的步骤）
- [x] 3.3 `app/memory/chat_persistence.py`：新增 `mark_steps_compacted(step_ids: list[int])` 方法（批量 UPDATE compacted=True）
- [x] 3.4 `app/memory/chat_persistence.py`：`load_history()` 修改——倒序扫描遇 `is_compaction=True` 停止，同时加载对应时间范围的 l3_steps
- [x] 3.5 `app/memory/chat_persistence.py`：`load_history()` 对无 l3_steps 记录的老会话降级为旧逻辑（兼容）

## 4. L3 执行结果持久化

- [x] 4.1 `app/execution/schemas.py`：`ExecutionResult` 新增 `l3_steps: list[dict] | None` 字段（存储原始中间步骤消息）
- [x] 4.2 `app/execution/l3/react_engine.py`：`execute()` 循环中收集每步 act_result.messages，执行结束后挂载到 `ExecutionResult.l3_steps`
- [x] 4.3 `app/api/chat.py`：`exec_result` 返回后，调用 `chat_persistence.save_l3_steps()` 写入中间步骤（write-behind）

## 5. Level 1 剪枝：内存级

- [x] 5.1 `app/execution/l3/react_engine.py`：新增 `_estimate_tokens(content: str) -> int` 静态方法（`len(content) // 2`）
- [x] 5.2 `app/execution/l3/react_engine.py`：重写 `_compress_stale_tool_results()`——改为从尾部按 token 估算累积，超出 `PRUNE_PROTECT_TOKENS` 的 tool result 替换为占位符（保护 skill_call 输出）
- [x] 5.3 `app/execution/l3/react_engine.py`：在 Think 完成后检查 `prompt_tokens > CONTEXT_PRUNE_TRIGGER`，触发内存级剪枝（复用 5.2 的逻辑）

## 6. Level 1 剪枝：DB 持久化

- [x] 6.1 `app/api/chat.py`：每轮 chat 请求完成（l3_steps 写入后），调用剪枝逻辑——从最新步骤往前累加 token 估算，超出 `PRUNE_PROTECT_TOKENS` 的步骤调用 `mark_steps_compacted()`
- [x] 6.2 剪枝逻辑封装为独立函数 `_prune_l3_steps(session_id, chat_persistence)`，在 chat.py 中 write-behind 调用

## 7. Level 2 摘要截断

- [x] 7.1 `app/execution/l3/react_engine.py`：新增 `_compact_messages(messages) -> list[dict]` 异步方法——识别保护区边界、提取可压缩区、调用 LLM 生成摘要、重建 messages
- [x] 7.2 摘要 prompt 封装为常量 `COMPACTION_PROMPT`（结构化，含制造业业务实体要求）
- [x] 7.3 `app/execution/l3/react_engine.py`：Think 完成后检查 `prompt_tokens > CONTEXT_SUMMARIZE_TRIGGER`，调用 `_compact_messages()`
- [x] 7.4 `app/api/chat.py`：Level 2 触发后，将摘要内容作为 `is_compaction=True` 的 assistant 消息写入 PG

## 8. 历史加载：跨轮 context 还原

- [x] 8.1 `app/memory/working_memory.py`：`get_history()` 从 PG 回源时调用新的 `load_history()`，拼接 l3_steps 并过滤 compaction 节点
- [x] 8.2 `app/api/chat.py` / `app/execution/l3/react_engine.py`：`_build_initial_messages()` 改用包含 l3_steps 的完整 history_messages，移除 `[-10:]` 硬截断，改为 token 动态边界
- [x] 8.3 history_messages 动态边界逻辑：从尾部累加 `_estimate_tokens()`，超出 `PRUNE_PROTECT_TOKENS` 停止

## 9. 验证与测试

- [ ] 9.1 更新 `tests/test_context_window.py`：加入 token 估算精度验证（估算值 vs 服务端实际值的比率）
- [ ] 9.2 新增集成测试：模拟 20 步 L3 执行，验证 l3_steps 写入条数和 compacted 标记正确性
- [ ] 9.3 新增集成测试：模拟超过 `CONTEXT_SUMMARIZE_TRIGGER` 场景，验证摘要生成和 messages 重建
- [ ] 9.4 新增集成测试：模拟有 compaction 节点的 session 加载，验证 genesis block 过滤行为
- [ ] 9.5 手动验证：多轮对话后追问，确认 LLM 能引用前轮中间步骤信息
