## ADDED Requirements

### Requirement: Level 2 溢出检测
系统 SHALL 在 L3 ReAct 每步 Think 完成后，检查 `think_result.usage["prompt_tokens"]` 是否超过 `CONTEXT_SUMMARIZE_TRIGGER`（88,473，即 98,304 的 90%）。若超过，立即触发摘要压缩。

#### Scenario: 超过摘要阈值触发 Level 2
- **WHEN** Think 完成后 prompt_tokens > 88,473
- **THEN** 立即调用摘要生成逻辑（不继续执行下一步）
- **THEN** 生成摘要后，用压缩后的 messages 继续 ReAct 循环

#### Scenario: 未超过阈值时不触发
- **WHEN** Think 完成后 prompt_tokens ≤ 88,473
- **THEN** 继续正常 ReAct 循环，不触发摘要

### Requirement: 生成结构化摘要
系统 SHALL 调用同一 LLM（`self.llm.chat`）生成对话历史摘要，max_tokens=2,000，使用结构化 prompt 要求包含：任务目标、已完成操作、重要发现、操作过的文件/路径、业务实体（产品型号/工单号/设备编号）、当前状态与下一步计划。

#### Scenario: 摘要包含业务实体
- **WHEN** 对话历史中涉及产品型号、工单号、设备编号等制造业实体
- **THEN** 生成的摘要 MUST 包含这些实体的具体值

#### Scenario: 摘要失败时降级
- **WHEN** 摘要 LLM 调用失败（超时/网络错误）
- **THEN** 记录 warning 日志
- **THEN** 跳过摘要，继续使用当前 messages 执行（接受超限风险）

### Requirement: 摘要持久化为 genesis block
系统 SHALL 将生成的摘要存为一条 `role=assistant, is_compaction=True` 的 chat_messages 记录，并写入 PG。该记录成为新的历史起点——加载历史时遇到此记录即停止往前读取。

#### Scenario: 摘要写入 PG
- **WHEN** 摘要生成成功
- **THEN** 摘要内容写入 chat_messages 表，字段 is_compaction=True、role=assistant
- **THEN** 写入采用 write-behind 异步模式（与其他消息一致）

#### Scenario: 下次加载时以摘要为起点
- **WHEN** 加载 session 历史，遇到 is_compaction=True 的 chat_messages 记录
- **THEN** 停止加载该记录之前的所有消息和 l3_steps
- **THEN** 摘要消息本身作为 history 的第一条消息

### Requirement: 摘要后重建 messages 列表
系统 SHALL 在摘要生成后，重建当前 L3 执行的 messages 列表：保留 system prompt、插入摘要消息（role=user，前缀 `[历史摘要]`）、保留 PRUNE_PROTECT 范围内的最近步骤消息，丢弃旧消息。

#### Scenario: 摘要后 messages 结构正确
- **WHEN** 摘要生成并完成 messages 重建
- **THEN** messages 列表结构为：`[system] → [user: 历史摘要] → [保护区最近步骤]`
- **THEN** 重建后的 messages 估算 token 数 < CONTEXT_PRUNE_TRIGGER（73,728）
