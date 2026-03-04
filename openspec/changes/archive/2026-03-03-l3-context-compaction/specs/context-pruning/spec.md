## ADDED Requirements

### Requirement: 每轮结束后无条件运行 Level 1 剪枝
系统 SHALL 在每次 `/chat` 请求完成（L3 执行结束、消息写入 WorkingMemory）后，无条件对本轮产生的 l3_steps 运行剪枝：从最新步骤往前累加 tool result 的 token 估算（`len(content) // 2`），超出 `PRUNE_PROTECT_TOKENS`（20,000）的步骤标记 `compacted=True`。

#### Scenario: 旧 tool results 被标记
- **WHEN** 本轮 l3_steps 写入完成后运行剪枝
- **THEN** 从最新 tool result 往前累加估算 token 数
- **THEN** 超出 20,000 token 保护区的 tool result 记录，`compacted` 字段更新为 True
- **THEN** `compacted` 更新为 DB 批量 UPDATE，单次操作

#### Scenario: 总量未超保护区时不剪枝
- **WHEN** 所有 tool results 的总估算 token ≤ 20,000
- **THEN** 不更新任何记录的 compacted 字段
- **THEN** 跳过剪枝，记录 debug 日志

#### Scenario: 剪枝不影响 skill tool 输出
- **WHEN** tool result 对应的 tool_name 为 `skill_call`
- **THEN** 该 tool result 不被标记 compacted，始终完整保留

### Requirement: 发给 LLM 时动态替换 compacted 内容
系统 SHALL 在构建发给 LLM 的 messages 列表时，对 `compacted=True` 的 tool result 消息，将 content 替换为占位符，原始内容不传给 LLM。

#### Scenario: compacted tool result 替换为占位符
- **WHEN** 从 l3_steps 还原 messages 列表，遇到 compacted=True 的 tool result
- **THEN** 该消息的 content 替换为 `"[已处理] {tool_name} 输出已压缩（原始内容保留在历史记录中）"`
- **THEN** 消息的其他字段（role、tool_call_id）保持不变

#### Scenario: 原始内容保留在 DB
- **WHEN** tool result 被标记 compacted=True
- **THEN** l3_steps 表中该记录的 content 字段保持原始内容不变
- **THEN** UI 回看历史时可展示原始内容
