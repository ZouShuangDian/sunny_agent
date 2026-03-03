## Context

**当前状态：**
- L3 ReAct 引擎每次执行时，messages 列表在循环内单向增长（system + history + user + N×(assistant+tool_results)）
- 现有保护机制：`_compress_stale_tool_results()` 按 LLM 调用次数做步骤窗口截断（保留最近 1-3 步），`max_iterations=20` 硬上限，`timeout=300s` 熔断
- WorkingMemory 每轮只存 user 原文 + assistant 最终回复，L3 中间步骤全部丢弃
- 历史注入硬截断：`history_messages[-10:]` 且只保留 user/assistant 文本
- 模型上下文上限：98,304 token（实测确认，服务端精确值）

**核心问题：**
1. 单次 L3 执行内 tool results 可能撑爆 context window（一次 `read_file` 大文件即可消耗 20K+ token）
2. 跨轮对话中间步骤丢失，用户追问时 LLM 无法引用前一轮的分析过程
3. `[-10:]` 硬截断在长对话后丢失重要历史上下文

**参考设计：** OpenCode 的双层漏斗（Double-Tier Funnel）机制，已在生产中验证。

---

## Goals / Non-Goals

**Goals:**
- 实现 Level 1 持续剪枝：每轮结束后对旧 tool results 打 `compacted` 标记，发给 LLM 时替换占位符
- 实现 Level 2 摘要截断：context 接近上限时生成结构化摘要并持久化为新历史起点
- L3 中间步骤持久化到 PG，支持跨轮 context 连续
- 移除 `[-10:]` 硬截断，改为 token 估算的动态边界加载
- 配置参数化，便于根据模型切换调整阈值

**Non-Goals:**
- 不引入外部 tokenizer 库（使用字符数估算 `chars // 2`）
- 不实现 SubAgent 独立压缩进程（同一模型无成本优势）
- 不支持跨 session 的 compaction 共享
- 不修改 L1/L2 执行层（只改 L3 和 Memory 层）

---

## Decisions

### Decision 1：Token 计数策略 — 混合精确+估算

**选择：** 触发判断用服务端精确值，边界计算用字符估算

- **触发阈值**：使用 `think_result.usage["prompt_tokens"]`（服务端每次 LLM call 后返回，精确）
- **PRUNE_PROTECT 边界**：使用 `len(content) // 2` 字符估算（中文约 1.92 chars/token，实测）

**为什么不全用估算：** 触发阈值判断错误代价高（误触或漏触），必须用精确值。

**为什么不全用精确值：** API 只返回整体 prompt_tokens，不提供 per-message 拆分。delta 追踪（用相邻步骤之差推算）实现复杂，而保护区边界计算允许 ±20% 误差（有 25K buffer）。

**为什么不引入 tokenizer：** DeepSeek tokenizer 非标准包，引入依赖风险高，且字符估算精度已足够。

---

### Decision 2：Level 1 剪枝——标记而非删除

**选择：** `compacted=True` 标记，原始内容保留在 PG，运行时动态替换占位符

优于物理删除的原因：
- 原始 tool output 可用于 UI 回看、审计、调试
- 剪枝动作廉价（一次 UPDATE），不需要 LLM 参与
- 与 OpenCode 设计一致，已验证

**占位符格式：** `"[已处理] {tool_name} 输出已压缩（原始内容保留在历史记录中）"`

---

### Decision 3：Level 2 摘要——genesis block 存储与注入格式

**持久化选择：** 摘要存为 `role=assistant, is_compaction=True` 的 PG 消息

- 不注入 system prompt（避免污染模型指令层）
- role=assistant 作为 PG 存储格式（`is_compaction=True` 作为加载停止标志）
- `filterCompacted` 读到此消息即停，不再加载之前的消息

**注入 LLM 时的 role：** `role=user`，带明确框架说明

重建 messages 时，摘要以 `role=user` 注入（而非 assistant），原因：
- 保护区第一条消息通常是 `assistant(tool_calls)`，若摘要也用 assistant，则出现两条连续 assistant 消息，违反 API 格式
- role=user 符合 API 规范，且语义上是"用户（代表系统）向 LLM 提供背景信息"

**关键：内容必须明确框架，消除语义歧义**

若内容只有 `[历史摘要]` 前缀，LLM 会困惑"用户为何以第三人称总结 assistant 的操作"。

摘要注入内容模板：
```
【系统自动生成的历史摘要】
以下内容由系统生成，帮助你了解之前的对话背景，请基于此继续执行任务。

{summary_content}

---
请继续基于以上历史背景执行当前任务。
```

这样 LLM 明确知道：这不是用户的自然语言输入，而是系统给出的结构化背景信息。

**摘要生成 prompt：**
```
请为以下对话历史生成结构化摘要，包含：
1. 任务目标
2. 已完成的操作步骤
3. 重要发现和结论
4. 操作过的文件、路径、数据
5. 涉及的业务实体（产品型号、工单号、设备编号、指标名称等）
6. 当前状态与下一步计划
要求：摘要需足够详细，让继续任务的 AI 能无缝衔接。
```

---

### Decision 4：中间步骤存储——独立 l3_steps 表

**选择：** 新建 `l3_steps` 表，不污染 `chat_messages` 表

| 方案 | 优 | 劣 |
|---|---|---|
| 扩展 chat_messages | 简单 | role=tool 消息与 user/assistant 语义混杂，查询复杂 |
| **独立 l3_steps 表** | 关注点分离，查询清晰 | 需要 JOIN |
| 存 JSON 在 reasoning_trace 列 | 已有基础 | 无法高效查询、无法单条打 compacted 标记 |

**l3_steps 表结构：**
```sql
session_id   VARCHAR    -- 会话 ID
message_id   VARCHAR    -- 关联的 assistant 最终消息
step_index   INTEGER    -- 步骤序号（0-based）
role         VARCHAR    -- 'assistant' | 'tool'
content      TEXT       -- 消息内容
tool_name    VARCHAR    -- tool 消息专用
tool_call_id VARCHAR    -- tool 消息专用
compacted    BOOLEAN    -- Level 1 剪枝标记
created_at   TIMESTAMP
```

---

### Decision 5：剪枝触发时机——每轮结束无条件运行

**选择：** 每次 `/chat` 请求完成后，无条件对该轮产生的 l3_steps 运行剪枝

**为什么不只在接近阈值时运行：**
- 剪枝代价极低（DB UPDATE + 内存标记），没有理由等待
- 持续保洁使下一轮起始时 context 始终处于可控状态
- 与 OpenCode 实践一致

---

## Risks / Trade-offs

**[风险 1] l3_steps 写入量大** → 每步 2 条记录（assistant+tool），20 步 = 40 条/请求。高并发下 PG 写压力上升。
→ 缓解：write-behind 异步写入（复用现有 `CHAT_PERSIST_WRITE_BEHIND` 机制）；初期可限制只存 L3 路由请求的 steps。

**[风险 2] 历史加载变重** → 加载时需 JOIN l3_steps，且需过滤 compacted。
→ 缓解：`session_id + created_at` 索引；compaction 节点后的消息数量有限。

**[风险 3] 摘要质量不稳定** → 制造业专业术语可能被 LLM 摘要漏掉或错误概括。
→ 缓解：结构化 prompt 明确要求保留业务实体；摘要原始内容仍在 PG 可回查；Level 2 是最后防线，Level 1 日常保洁已能处理大多数场景。

**[风险 4] 首次上线时现有历史无 l3_steps** → 老会话没有中间步骤数据，加载时行为与新会话不同。
→ 缓解：`load_history` 中对无 l3_steps 的老会话降级为现有逻辑（user/assistant 文本 + `[-10:]`），渐进迁移。

**[Trade-off] 字符估算误差** → `chars // 2` 对纯英文可能高估（英文约 4 chars/token），导致 PRUNE_PROTECT 边界偏移。
→ 可接受：有 25K buffer，偏移 ±4K 不影响正确性。

---

## Migration Plan

1. **Alembic 迁移**：添加 `l3_steps` 表 + `chat_messages.is_compaction` 字段
2. **配置项上线**：`config.py` 新增 6 个常量，`.env` 不需变更（用默认值）
3. **代码发布**：L3、Memory、Persistence 三层同批次发布
4. **老会话兼容**：`load_history` 降级逻辑保证老会话无感知
5. **回滚**：关闭 `CHAT_PERSIST_ENABLED=False` 可禁用 l3_steps 写入；`config.py` 的 `CONTEXT_PRUNING_ENABLED=False` 开关（建议新增）可单独关闭压缩逻辑

---

## Open Questions

1. **l3_steps 的保留策略**：是否需要独立的 TTL/清理任务？（建议与 chat_messages 同生命周期）
2. **Level 2 摘要的触发位置**：在 `chat.py` 的每轮结束后检测，还是在 `react_engine.py` 的循环内检测？（建议在 `react_engine.py` 内，更早感知）
3. **PRUNE_PROTECT_TOKENS = 20,000 是否合适**：制造业任务的 tool outputs 大小有待观察，可能需要根据实际使用调整
