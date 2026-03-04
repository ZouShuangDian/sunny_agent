## MODIFIED Requirements

### Requirement: L3 循环内 token 感知压缩
系统 SHALL 在 L3 ReAct 每步 Think 完成后，读取 `think_result.usage["prompt_tokens"]`，根据以下阈值执行对应操作：
- `> CONTEXT_PRUNE_TRIGGER`（73,728）：对当前 in-memory messages 中旧 tool results 替换为占位符（内存级 Level 1）
- `> CONTEXT_SUMMARIZE_TRIGGER`（88,473）：触发 Level 2 摘要，重建 messages 列表

#### Scenario: 超 75% 时内存级剪枝
- **WHEN** Think 后 prompt_tokens > 73,728
- **THEN** 对当前 messages 列表中超出 PRUNE_PROTECT_TOKENS 的旧 tool results 内容替换为占位符
- **THEN** 不中断 ReAct 循环，继续执行

#### Scenario: 超 90% 时触发摘要
- **WHEN** Think 后 prompt_tokens > 88,473
- **THEN** 调用 `_compact_messages()` 生成摘要并重建 messages
- **THEN** 继续 ReAct 循环（用重建后的 messages）

### Requirement: _compress_stale_tool_results 升级为 token 估算边界
现有按 LLM 调用次数（llm_call_count）决定保留步数的逻辑，SHALL 升级为基于 token 估算的 PRUNE_PROTECT_TOKENS 边界：从 messages 尾部往前累加 `len(content) // 2`，超出边界的 tool result 内容替换为占位符。

#### Scenario: token 估算边界替代步骤计数
- **WHEN** `_compress_stale_tool_results(messages)` 被调用
- **THEN** 从 messages 尾部往前累加 tool result 的 `len(content) // 2`
- **THEN** 累积超出 20,000 的 tool result 内容替换为 `"[已处理] {tool_name}..."` 占位符
- **THEN** 不再依赖 llm_call_count 参数

#### Scenario: skill_call 的 tool result 不被压缩
- **WHEN** tool result 对应的工具为 skill_call
- **THEN** 该 tool result 不被替换，完整保留
