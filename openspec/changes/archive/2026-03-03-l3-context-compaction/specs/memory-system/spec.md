## MODIFIED Requirements

### Requirement: 历史消息加载支持 compaction 过滤
系统 SHALL 在从 PG 加载 session 历史时，倒序扫描 chat_messages，遇到 `is_compaction=True` 的记录立即停止，只返回该记录及其之后的消息（含摘要本身）。同时加载对应时间范围内的 l3_steps，compacted=True 的 tool result 替换为占位符后还原为 LLM messages 格式。

#### Scenario: 存在 compaction 节点时过滤旧消息
- **WHEN** `load_history(session_id)` 从 PG 加载
- **THEN** 倒序读取 chat_messages，遇到 is_compaction=True 停止
- **THEN** 只返回 compaction 节点之后（含节点本身）的消息
- **THEN** 节点之前的消息不加载到 WorkingMemory

#### Scenario: 无 compaction 节点时完整加载（降级兼容）
- **WHEN** `load_history(session_id)` 从 PG 加载，无 is_compaction=True 记录
- **THEN** 按原有逻辑加载全部历史消息
- **THEN** 如无 l3_steps 数据，退回 `[-10:]` user/assistant 文本模式

### Requirement: 动态 token 边界替代硬截断
系统 SHALL 将 `history_messages[-10:]` 硬截断替换为基于 token 估算的动态边界：从 history 尾部往前累加估算 token，超出 `PRUNE_PROTECT_TOKENS`（20,000）的消息不注入 L3 initial messages。

#### Scenario: 动态边界计算
- **WHEN** 构建 L3 initial messages，history_messages token 估算总量 > 20,000
- **THEN** 从尾部往前取，累积到 ≤ 20,000 token 为止
- **THEN** 超出边界的旧消息不注入（已由 compaction 机制覆盖）

#### Scenario: 历史消息总量未超边界
- **WHEN** history_messages token 估算总量 ≤ 20,000
- **THEN** 全部 history_messages 注入 L3 initial messages
