## MODIFIED Requirements

### Requirement: Level 2 触发条件

每步 Think 完成后，读取 `think_result.usage["prompt_tokens"]`，计算剩余空间：

```python
remaining = MODEL_CONTEXT_LIMIT - prompt_tokens
if remaining < COMPACTION_BUFFER:
    messages = await self._compact_messages(messages)
```

触发值使用服务端精确值（非估算），保证触发准确。

`COMPACTION_BUFFER`（20,000）定义"剩余空间不足"的阈值，语义为"预留给后续操作的最小空间"。实际触发点为 `prompt_tokens > 78,304`（≈80% of 98,304）。

#### Scenario: 正常执行不触发 Level 2
- **WHEN** Think 返回 `prompt_tokens = 50,000`，`remaining = 48,304 > 20,000`
- **THEN** 不触发 Level 2，继续正常执行

#### Scenario: 上下文接近溢出触发 Level 2
- **WHEN** Think 返回 `prompt_tokens = 82,000`，`remaining = 16,304 < 20,000`
- **THEN** 触发 `_compact_messages()`，生成摘要并重建 messages

### Requirement: Level 2 配置参数

| 参数 | 值 | 说明 |
|------|---|------|
| `MODEL_CONTEXT_LIMIT` | 98,304 | 模型 context 上限（token） |
| `COMPACTION_BUFFER` | 20,000 | 剩余空间阈值（低于此值触发 Level 2） |
| `PRUNE_PROTECT_TOKENS` | 20,000 | 保护区 token 数 |
| `COMPACTION_MAX_TOKENS` | 2,000 | 摘要生成 max_tokens |
| `COMPRESS_MIN_SAVING` | 10,000 | 摘要后至少节省 token 数 |

#### Scenario: 配置参数完整性
- **WHEN** 系统启动
- **THEN** 以上参数均有默认值，无需 `.env` 配置

## REMOVED Requirements

### Requirement: CONTEXT_SUMMARIZE_TRIGGER 百分比阈值
**Reason**: 被 `COMPACTION_BUFFER` 替代。`remaining < buffer` 比 `prompt_tokens > 90%` 语义更清晰，且与 OpenCode 的 COMPACTION_BUFFER 设计对齐。
**Migration**: 将 `CONTEXT_SUMMARIZE_TRIGGER = 88_473` 替换为 `COMPACTION_BUFFER = 20_000`，触发逻辑从 `prompt_tokens > threshold` 改为 `remaining < buffer`。
