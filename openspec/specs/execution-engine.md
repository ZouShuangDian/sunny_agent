# Execution Engine Spec — L1 FastTrack & L3 ReAct

## 1. 概述

执行层负责将 `IntentResult` 转化为最终回复。包含两条执行路径：

| 路径 | 触发条件 | 最大步数 | 工具集 | 核心特征 |
|------|---------|---------|-------|---------|
| **L1 FastTrack** | route=standard_l1 | 3 steps | 全部工具（all tiers） | 快速、低成本、无推理轨迹 |
| **L3 ReActEngine** | route=deep_l3 | 20 iter / 50 LLM calls / 300s | tier=L3 工具 | 多步推理、轨迹记录、熔断保护 |

---

## 2. ExecutionRouter（entry point）

`app/execution/router.py`

### 职责
1. 启动时构建 `ToolRegistry`（注册所有内置工具 + SkillCallTool + SubAgentCallTool）
2. 加载 SubAgent 目录（内置 → 用户，同名用户覆盖内置）
3. **每次请求**：查 DB 获取当前用户 Skill 列表 → 设置 `skill_context` ContextVar → 分发执行 → 还原

### Skill 加载策略
```python
catalog = await skill_service.get_user_skills(usernumb)   # DB 查询
skill_token = set_skill_catalog(catalog)                   # 设 ContextVar
try:
    result = await self.l1.execute(intent_result, session_id)
finally:
    reset_skill_catalog(skill_token)                       # 必还原
```

### 路由逻辑
```
route == "standard_l1" → L1FastTrack
route == "deep_l3"      → L3ReActEngine
其他未知路由             → 降级到 standard_l1
```

---

## 3. L1 FastTrack

`app/execution/l1/fast_track.py`

### 执行流程
```
1. PromptRetriever.retrieve(intent_primary)
   → Milvus 语义检索匹配 Prompt（COSINE 相似度 > 0.5）
   → miss 时降级到默认 Prompt

2. 组装 messages = [system, *history_messages, user_input]

3. Bounded Loop (max 3 steps):
   step 0~1: chat_with_tools(all_tool_schemas)
     → 无 tool_calls → break
     → 有 tool_calls → 执行 → 追加 assistant + tool 消息 → 下一 step

   step 2 (最后): chat(no tools) → 强制 LLM 生成文本总结

4. 返回 ExecutionResult(reply, tool_calls, source="standard_l1")
```

### Prompt 检索机制
- 索引：Milvus `l1_prompt_templates`（tier="L1"）
- 相似度阈值：0.5（PROMPT_SEARCH_THRESHOLD）
- Top-K：3
- miss 时：`PromptCache` 返回 DB 默认 Prompt

### 固定参数
```python
_MAX_LOOP_STEPS = 3
_TEMPERATURE = 0.7
_MAX_TOKENS = 4096
_BASE_PROMPT = "你是 Agent Sunny，舜宇集团的 AI 智能助手..."
```

---

## 4. L3 ReActEngine

`app/execution/l3/react_engine.py`

### 架构：Thinker → Actor → Observer 三组件循环

```
                ┌─────────────────────────────────┐
                │          L3 ReActEngine          │
                │                                  │
   intent_result│                                  │
   ─────────────► _build_initial_messages()        │
                │    [system, *history, user]       │
                │    + Plugin 上下文（if 有）        │
                │          │                       │
                │    ┌─────▼──────────────────┐    │
                │    │  for step in range(20) │    │
                │    │                        │    │
                │    │  熔断检查（Observer）   │    │
                │    │          ↓             │    │
                │    │  Todo 注入（Layer 3）   │    │
                │    │          ↓             │    │
                │    │  Thinker.think()       │    │
                │    │  LLM → ThinkResult     │    │
                │    │  (thought+tool_calls)  │    │
                │    │          ↓             │    │
                │    │  is_done? → break      │    │
                │    │          ↓             │    │
                │    │  Actor.act()           │    │
                │    │  执行所有 tool_calls    │    │
                │    │          ↓             │    │
                │    │  Observer 记录         │    │
                │    │  context 压缩          │    │
                │    └────────────────────────┘    │
                │                                  │
                │    → ExecutionResult             │
                └─────────────────────────────────┘
```

### 4.1 Thinker（thinker.py）
- 调用 `llm.chat()` / `llm.chat_with_tools()`
- 解析 ThinkResult：thought（思考文本）+ tool_calls（工具调用列表）+ is_done
- 最后一步（step == max_iterations-1）：不传工具，强制文本输出

### 4.2 Actor（actor.py）
- 遍历 ThinkResult.tool_calls，调用 `ToolRegistry.execute(name, args)`
- 构建 `ActResult`（observations 列表：tool_name + arguments + result）
- 返回追加到上下文所需的 messages（assistant + tool 角色消息）

### 4.3 Observer（observer.py）
- **熔断检查**（`should_stop()`）：任一条件触发即中断
  - 超时：elapsed > L3_TIMEOUT_SECONDS（300s）
  - 迭代超限：step >= L3_MAX_ITERATIONS（20）
  - LLM 调用超限：llm_call_count >= L3_MAX_LLM_CALLS（50）
- **Token Budget**（token_budget.py）：跟踪 LLM 调用次数、token 用量
- **Trace**（推理轨迹）：记录每步 thought + tool_calls + observations，供输出校验和 PG 存储

### 4.4 Context 压缩

防止长对话 context 膨胀，参照 OpenCode 的简洁模型：

**Level 1 内存级剪枝（`_compress_stale_tool_results()`，每步 Act 后无条件运行）**

基于 token 估算（`len(content) // 2`）的保护区边界：
- 从 messages 尾部往前累加 tool result 的 token 估算
- 超出 `PRUNE_PROTECT_TOKENS`（20,000）的 tool result 内容替换为占位符
- `skill_call` 的 tool result 始终保留（不被替换）
- 每步 Act 后无条件执行（每步保洁）

**Level 2 摘要截断（`_compact_messages()`，上下文剩余空间不足时触发）**

- Think 后计算 `remaining = MODEL_CONTEXT_LIMIT - prompt_tokens`
- `remaining < COMPACTION_BUFFER`（20,000）时触发
- 识别保护区（从尾部累积 PRUNE_PROTECT_TOKENS），提取可压缩区
- 调用 LLM 生成结构化摘要（max_tokens=2,000）
- 重建 messages：`[system] → [user: 历史摘要] → [保护区消息]`
- 摘要暂存于 `self.last_compaction_summary`，供 chat.py 持久化为 genesis block

**Token 计数策略（混合精确+估算）**

| 用途 | 方法 |
|------|------|
| Level 2 触发判断 | `think_result.usage["prompt_tokens"]`（服务端精确值） |
| 保护区边界计算 | `len(content) // 2`（字符估算，允许 ±20% 误差） |

### 4.5 优雅降级（`_build_result(degrade_reason=...)`）
熔断触发时：
- 用 Observer 已收集的 observations 拼接摘要
- **不做额外 LLM 调用**（Q2 裁决）
- 返回 `ExecutionResult(is_degraded=True, degrade_reason=...)`

---

## 5. Todo 三层机制

L3 引擎内嵌的任务追踪系统（对标 opencode 设计）：

```
Layer 1（宪法层）
└── build_l3_system_prompt() 末尾静态注入 Todo 规范
    "首先用 todo_write 规划任务，再逐步执行..."

Layer 2（感知层）
└── todo_write / todo_read 工具
    每次调用返回完整 Todo 状态快照
    存储：Redis key=todo:{session_id}，TTL=7天

Layer 3（干预层）
└── _inject_todo_reminder(messages, user_goal)
    每次 Think 前调用
    有活跃 Todo → 注入最新状态到 system prompt 末尾
    无活跃 Todo → 剥离注入块（幂等）
    按 TODO_REMINDER_MARKER 截断，防重复注入
```

---

## 6. Plugin 上下文注入

若当前请求由 Plugin 命令触发（`plugin_context` ContextVar 已设置），`_build_initial_messages()` 在 system prompt 末尾追加：
- `/{plugin}:{command}` 命令标识
- COMMAND.md 完整工作流指引
- Plugin 内可用 Skill 列表（含容器路径）
- 禁止通过全局 skill_call 调用的规范说明

---

## 7. execute_raw()（SubAgent 专用）

```python
async def execute_raw(self, messages: list[dict]) -> ExecutionResult
```
- 跳过 `_build_initial_messages()`
- 不设置 session_id（SubAgent 隔离，Todo 不追踪）
- 由 `SubAgentCallTool` 传入隔离好的子 Agent 上下文调用

---

## 8. 流式执行（execute_stream）

SSE 事件格式（含 `context_usage`）：
```json
{"event": "thought",        "data": {"step": 0, "content": "我需要先搜索..."}}
{"event": "context_usage",  "data": {"prompt_tokens": 52000, "remaining": 46304, "percent": 52.9, "limit": 98304}}
{"event": "tool_call",      "data": {"step": 0, "name": "web_search", "args": {...}}}
{"event": "tool_result",    "data": {"step": 0, "name": "web_search", "result": "..."}}
{"event": "delta",          "data": "最终回答的文本片段"}
{"event": "finish",         "data": {"iterations": 3, "llm_calls": 5}}
```

`context_usage` 事件在每步 Think 后、tool_call 之前推送。
中间步骤（thought + tool_call + tool_result）非流式推送，最终回答（delta）流式推送。

### 8.1 ExecutionResult 上下文用量

`ExecutionResult` 包含 `context_usage: dict | None` 字段，存储最后一步 Think 的上下文用量快照。
`ChatResponse` 包含 `context_usage: dict | None` 字段，透传给前端。

---

## 9. L3 配置参数

| 参数 | 默认值 | 说明 |
|------|-------|------|
| `L3_MAX_ITERATIONS` | 20 | ReAct 循环最大步数 |
| `L3_TIMEOUT_SECONDS` | 300.0 | 整体超时（秒） |
| `L3_MAX_LLM_CALLS` | 50 | LLM 调用次数上限 |
| `MODEL_CONTEXT_LIMIT` | 98,304 | 模型 context 上限（token，实测精确值） |
| `COMPACTION_BUFFER` | 20,000 | Level 2 触发阈值（剩余空间低于此值触发摘要） |
| `PRUNE_PROTECT_TOKENS` | 20,000 | 保护区 token 数（最近步骤不被剪枝） |
| `HISTORY_TOKEN_BUDGET` | 60,000 | 历史消息加载预算 |
| `COMPRESS_MIN_SAVING` | 10,000 | Level 2 摘要后至少需节省的 token 数 |
| `COMPACTION_MAX_TOKENS` | 2,000 | 摘要生成 max_tokens |
