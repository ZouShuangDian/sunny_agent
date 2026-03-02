# Intent Pipeline Spec — 意图理解管线

## 1. 概述

意图管线是请求进入执行层之前的核心处理链路，负责将用户自然语言转化为结构化的 `IntentResult`。

```
用户输入
    │
    ▼
ContextBuilder.build()          → 基础上下文（Redis 读一次）
    │
    ▼
IntentEngine.analyze()          → LLM 意图识别（temperature=0.0）
    │
    ▼
ContextBuilder.enrich()         → 增量扩展上下文（按意图类型）
    │
    ▼
ClarifyHandler.check()          → 是否需要追问？
    │
    ▼
OutputAssembler.assemble()      → 组装最终 IntentResult
    │
    ▼
GuardrailsValidator.validate()  → JSON修复 + Schema校验 + 降级
    │
    ▼
WorkingMemory.save_last_intent() → 保存本轮意图快照（连续追问判断）
```

---

## 2. ContextBuilder（M03-3）

`app/intent/context_builder.py`

### 两阶段设计（W2 修正）

**阶段 1：`build()`** — 基础上下文，仅一次 Redis 读取
- 读取 `WorkingMemory`：对话历史 + 上一轮意图
- 构造用户信息（usernumb, username, role, department, data_scope）
- 生成初始 system prompt（含历史，无扩展知识）
- 返回 `AssembledContext`

**阶段 2：`enrich()`** — 增量扩展，零次 Redis 读取（复用 build() 结果）
- 按 `intent_hint` 选择 `ContextStrategy`
- 加载扩展上下文（码表映射、知识库片段、相似历史）
- 重建 system prompt（追加 knowledge_section）

### ContextStrategy 类型

| 策略 | 触发意图 | 加载内容 |
|------|---------|---------|
| `MinimalStrategy` | greeting, general_qa, writing | 无扩展（直接返回基础上下文） |
| `QueryStrategy` | query | 码表映射（Phase 3 注入 codebook_service） |
| `AnalysisStrategy` | analysis | 码表 + 知识库 + 历史问答（Phase 3 注入） |

> 当前 Phase 1-2：QueryStrategy / AnalysisStrategy 均为桩实现，Phase 3 替换。

### System Prompt 模板
```
你是 Agent Sunny，一个制造业智能助手...
当前用户: {usernumb}，姓名: {username}，角色: {role}，部门: {department}
数据权限范围: {data_scope}
{history_section}
{knowledge_section}
请分析用户的意图，严格按照指定的 JSON 格式输出结果。
```

---

## 3. IntentEngine（M03-4）

`app/intent/intent_engine.py`

### LLM 调用参数
```python
temperature = 0.0   # 意图识别要求确定性
max_tokens = 1024
```

### 输出结构（JSON）
```json
{
  "intent": {
    "primary": "writing",
    "sub_intent": null,
    "user_goal": "帮用户撰写工作周报"
  },
  "route": "standard_l1",
  "complexity": "simple",
  "confidence": 0.95,
  "entity_hints": {},
  "needs_clarify": false,
  "clarify_question": null
}
```

### 路由决策规则

**standard_l1**（默认）：
- 1-3 步内可完成的任务
- 直接回答、内容生成、简单检索

**deep_l3**（仅当明确需要多步推理）：
- 需要制定执行计划
- 多步推理、归因分析、跨数据源对比
- "先...然后...最后..." 的分步任务

### 容错机制
1. JSON 解析失败 → `JsonRepairer.repair()` 修复
2. 修复后仍失败 → 重试 1 次（在 messages 追加格式提示）
3. 重试失败 → 返回 `DEFAULT_RESULT`（general_qa, standard_l1, confidence=0.0）
4. route 非法值 → 降级为 standard_l1

### 意图类别（intent.primary）
动态从 PG `prompt_templates` 表加载（`intent_tags` 字段），加载失败降级到内置 `general_qa`。

---

## 4. ClarifyHandler

`app/intent/clarify_handler.py`

- 检查 `IntentEngineResult.needs_clarify == True`
- 若需追问：直接返回 `clarify_question` 作为 reply，跳过执行层
- 追问结果写入 WorkingMemory，便于下一轮消除追问状态

---

## 5. OutputAssembler

`app/intent/output_assembler.py`

将 `IntentEngineResult` + `AssembledContext` + `ClarifyResult` + 用户/会话信息组装成最终 `IntentResult`：

```python
IntentResult(
    route=...,
    complexity=...,
    confidence=...,
    intent=IntentDetail(primary=..., sub_intent=..., user_goal=...),
    raw_input=...,
    session_id=...,
    trace_id=...,
    history_messages=[...],  # 最近 10 条，用于执行层上下文
    needs_clarify=...,
    clarify_question=...,
)
```

---

## 6. GuardrailsValidator（M04）

`app/guardrails/validator.py`

### 四步流程
```
raw_json → JsonRepairer.repair() → DefaultFiller.fill() → IntentResult.model_validate() → OK
                                                                     │
                                                              ValidationError
                                                                     │
                                                         FallbackHandler.fallback()
                                                         → standard_l1, general_qa
```

### JsonRepairer
处理 LLM 常见畸形输出：
- Markdown 代码块包裹（```json ... ```）
- 前后多余文字
- 单引号替换
- 尾随逗号

### DefaultFiller
填充缺失字段：
- route 默认 "standard_l1"
- complexity 默认 "simple"
- confidence 默认 0.5

### FallbackHandler
完全失败时返回：
```python
IntentResult(route="standard_l1", intent=IntentDetail(primary="general_qa"), ...)
```

---

## 7. CodebookService（M03-2）

`app/intent/codebook_service.py`

制造业术语归一化：将用户输入中的别名映射到标准码。

### 缓存策略
- **读**：先查 Redis（TTL=1h），miss 时 PG 回源 + 写回
- **预热**：应用启动时 `warm_cache()` 加载全量码表到 Redis
- **预热失败**：不阻止启动，降级为逐条回源

### DB 表：`sunny_agent.codebook`
- `code`：标准码
- `alias`：归一化别名（小写+去分隔符）
- `alias_display`：原始展示名
- `category`：码表分类

---

## 8. 意图类别扩展

通过 DB `prompt_templates.intent_tags` 字段动态注入意图分析 Prompt，无需改代码新增意图类别：

```sql
-- 新增意图类别：修改 intent_tags，IntentEngine 下次请求自动感知
UPDATE sunny_agent.prompt_templates
SET intent_tags = '["production_query", "quality_analysis"]'
WHERE name = 'manufacturing_template';
```
