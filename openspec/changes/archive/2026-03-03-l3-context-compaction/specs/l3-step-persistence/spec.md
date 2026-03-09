## ADDED Requirements

### Requirement: L3 中间步骤写入 PG
系统 SHALL 在每次 L3 ReAct 执行完成后，将该次执行产生的所有中间步骤（每步的 assistant tool_call 消息和 tool result 消息）写入 PG `l3_steps` 表，关联到本轮的 session_id 和 assistant message_id。

#### Scenario: 正常执行后写入步骤
- **WHEN** L3 ReAct 循环执行完毕（is_done=True 或达到 max_iterations）
- **THEN** 每步的 assistant(tool_calls) 和 tool(result) 消息按 step_index 顺序写入 l3_steps 表
- **THEN** 每条记录包含 session_id、message_id、step_index、role、content、tool_name（tool 消息）、tool_call_id（tool 消息）、compacted=False

#### Scenario: 写入失败不影响主流程
- **WHEN** l3_steps 写入 PG 发生异常
- **THEN** 异常被捕获并记录 warning 日志
- **THEN** 主流程（用户收到回复）不受影响

#### Scenario: 非 L3 路由不写入
- **WHEN** 请求走 L1 或 L2 路由（无 tool calls）
- **THEN** 不写入 l3_steps 记录

### Requirement: 跨轮加载中间步骤
系统 SHALL 在构建 L3 初始 messages 时，从 PG 加载当前会话的历史 l3_steps，还原为 LLM 可读的 messages 格式（assistant tool_calls + tool results），注入到 history_messages 中。

#### Scenario: 有历史步骤时加载并还原
- **WHEN** 构建 L3 initial messages，session 存在历史 l3_steps
- **THEN** 历史步骤以 `{"role": "assistant", "tool_calls": [...]}` 和 `{"role": "tool", "content": ..., "tool_call_id": ...}` 格式还原
- **THEN** compacted=True 的 tool result 内容替换为占位符 `"[已处理] {tool_name} 输出已压缩"`
- **THEN** 还原后的 messages 按时间顺序插入 history_messages 中对应位置

#### Scenario: 遇到 is_compaction 消息停止加载
- **WHEN** 加载历史 l3_steps，遇到 is_compaction=True 的 chat_messages 记录
- **THEN** 停止加载该记录之前的所有 l3_steps
- **THEN** 只返回 compaction 节点之后的步骤

#### Scenario: 无历史步骤降级为旧逻辑
- **WHEN** 构建 L3 initial messages，session 无 l3_steps 记录（老会话或 l3_steps 功能未启用时）
- **THEN** 降级为原有逻辑：使用 chat_messages 的 user/assistant 文本，保持兼容
