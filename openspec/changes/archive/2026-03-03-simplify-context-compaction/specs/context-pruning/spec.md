## MODIFIED Requirements

### Requirement: 内存级剪枝触发时机

- **每步 Act 后无条件执行**（每步保洁，低成本）

触发后调用 `_compress_stale_tool_results(messages)`，算法不变（token 估算边界 + skill_call 保护）。

#### Scenario: Act 后无条件执行
- **WHEN** Actor.act() 完成，messages.extend(act_result.messages) 后
- **THEN** 无条件调用 `_compress_stale_tool_results(messages)` 进行内存级剪枝

#### Scenario: Think 后不触发额外剪枝
- **WHEN** Thinker.think() 返回任意 prompt_tokens 值
- **THEN** 不触发 Level 1 额外剪枝（该路径已移除）

### Requirement: 历史加载预算独立化

`_select_history_by_token()` 使用独立的 `HISTORY_TOKEN_BUDGET`（60,000）作为历史消息加载预算，不再复用 `PRUNE_PROTECT_TOKENS`。

```python
def _select_history_by_token(self, history_messages: list[dict]) -> list[dict]:
    budget = settings.HISTORY_TOKEN_BUDGET  # 60,000（独立预算）
    selected = []
    accumulated = 0
    for msg in reversed(history_messages):
        token_est = self._estimate_tokens(msg.get("content") or "")
        if accumulated + token_est <= budget:
            accumulated += token_est
            selected.insert(0, msg)
        else:
            break
    return selected
```

#### Scenario: 历史加载使用独立预算
- **WHEN** `_build_initial_messages()` 调用 `_select_history_by_token()` 加载历史
- **THEN** 使用 `HISTORY_TOKEN_BUDGET`（60,000）作为预算，而非 `PRUNE_PROTECT_TOKENS`（20,000）

#### Scenario: 长对话历史充分加载
- **WHEN** 历史消息总量超过 60,000 token
- **THEN** 从尾部往前加载至 60,000 token 预算耗尽，丢弃更早的历史

### Requirement: Level 1 配置参数

| 参数 | 值 | 说明 |
|------|---|------|
| `PRUNE_PROTECT_TOKENS` | 20,000 | 保护区 token 数（最近步骤不被剪枝） |
| `HISTORY_TOKEN_BUDGET` | 60,000 | 历史消息加载预算（独立于保护区） |

#### Scenario: 配置参数完整性
- **WHEN** 系统启动
- **THEN** 以上参数均有默认值，无需 `.env` 配置

## REMOVED Requirements

### Requirement: Think 后 75% 阈值额外触发 Level 1
**Reason**: Level 1 已在每步 Act 后无条件执行，75% 阈值触发多余且永远不会生效（因为 L1 无条件运行已将消息量压至远低于 75%）。
**Migration**: 移除 `react_engine.py` 中 Think 后的 `if prompt_tokens > CONTEXT_PRUNE_TRIGGER` 分支。

### Requirement: CONTEXT_PRUNE_TRIGGER 配置常量
**Reason**: 75% 阈值触发路径被移除后，该常量不再有使用方。
**Migration**: 从 `config.py` 中移除 `CONTEXT_PRUNE_TRIGGER = 73_728`。
