# Memory System Spec — 记忆系统

## 1. 记忆分层架构

```
                ┌─────────────────────────────┐
                │       两速记忆分层           │
                │                             │
                │  ┌───────────────────────┐  │
                │  │   热层（Redis）         │  │
                │  │   TTL = 30min          │  │
                │  │   key: wm:{session_id} │  │
                │  │   存储：Hash（3个field）│  │
                │  │   - history            │  │
                │  │   - last_intent        │  │
                │  │   - meta               │  │
                │  └──────────┬────────────┘  │
                │             │ Redis miss     │
                │             ▼               │
                │  ┌───────────────────────┐  │
                │  │   冷层（PostgreSQL）   │  │
                │  │   write-behind 异步    │  │
                │  │   - chat_sessions     │  │
                │  │   - chat_messages     │  │
                │  │     含 tool_calls     │  │
                │  │     含 reasoning_trace│  │
                │  └───────────────────────┘  │
                └─────────────────────────────┘
```

---

## 2. WorkingMemory（热层）

`app/memory/working_memory.py`

### Redis 数据结构
```
key: wm:{session_id}           # Hash
fields:
  history     → ConversationHistory (JSON)
  last_intent → LastIntent (JSON)
  meta        → SessionMeta (JSON)
TTL: 1800s（30min，每次写入时刷新）
```

### 核心操作
```python
class WorkingMemory:
    async def exists(session_id: str) -> bool
    async def init_session(session_id, user_id, usernumb) -> SessionMeta
    async def get_history(session_id) -> ConversationHistory
    async def append_message(session_id, msg: Message) -> None
    async def get_last_intent(session_id) -> LastIntent | None
    async def save_last_intent(session_id, intent: LastIntent) -> None
    async def get_meta(session_id) -> SessionMeta | None
    async def increment_turn(session_id) -> int
    async def touch(session_id) -> None     # 续期
    async def clear(session_id) -> None     # 清空
```

### 数据模型（memory/schemas.py）

**Message**
```python
class Message(BaseModel):
    role: str           # user | assistant | tool
    content: str
    timestamp: float
    message_id: str
    intent_primary: str | None
    route: str | None
    tool_calls: list[ToolCall] | None
    is_compaction: bool = False  # Level 2 摘要 genesis block 标记
```

**L3Step**
```python
class L3Step(BaseModel):
    step_index: int
    role: str           # 'assistant' | 'tool'
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    compacted: bool = False
```

**ToolCall**
```python
class ToolCall(BaseModel):
    tool_call_id: str
    tool_name: str
    arguments: dict
    result: str
    status: str         # success | error
    duration_ms: int
```

**ConversationHistory**
```python
class ConversationHistory(BaseModel):
    messages: list[Message]
    max_turns: int = 20  # WORKING_MEMORY_MAX_TURNS

    def append(self, msg: Message) -> None     # 超限时移除最老 2 条（1 轮）
    def to_llm_messages(self) -> list[dict]   # 转为 [{"role":..., "content":...}]
```

**SessionMeta**
```python
class SessionMeta(BaseModel):
    session_id: str
    user_id: str
    usernumb: str
    turn_count: int
    created_at: float
    last_active_at: float
```

**LastIntent**
```python
class LastIntent(BaseModel):
    primary: str
    sub_intent: str | None
    route: str
    complexity: str
    confidence: float
    needs_clarify: bool
    clarify_question: str | None
```

---

## 3. ChatPersistence（冷层）

`app/memory/chat_persistence.py`

### 写入策略（write-behind）
```python
# 异步 fire-and-forget，不阻塞请求
chat_persistence.save_message_background(session_id, msg, reasoning_trace=...)
chat_persistence.ensure_session_background(session_id, user_id, first_message)
```

CHAT_PERSIST_ENABLED=True 时启用，CHAT_PERSIST_WRITE_BEHIND=True 时异步写。

### Redis miss 回源
```python
# init_session() 中：Redis miss → 从 PG 恢复
pg_history = await chat_persistence.load_history(session_id)
if pg_history and pg_history.messages:
    for msg in pg_history.messages:
        await memory.append_message(session_id, msg)
```

`load_history()` 会还原 tool_calls JSON → ToolCall 对象列表。

### DB 表结构

**chat_sessions**
```sql
id UUID PRIMARY KEY,
user_id UUID,
title VARCHAR(500),      -- 第一条用户消息的前 50 字
first_message TEXT,
created_at TIMESTAMPTZ,
updated_at TIMESTAMPTZ
```

**chat_messages**
```sql
id UUID PRIMARY KEY,
session_id UUID,
role VARCHAR(20),
content TEXT,
tool_calls JSONB,        -- list[ToolCall] JSON
reasoning_trace JSONB,   -- L3 推理轨迹（仅 assistant 消息）
intent_primary VARCHAR(100),
route VARCHAR(50),
is_compaction BOOLEAN DEFAULT FALSE,  -- Level 2 摘要 genesis block 标记
created_at TIMESTAMPTZ
```

**l3_steps**（L3 中间步骤持久化表）
```sql
id UUID PRIMARY KEY,
session_id VARCHAR(64),   -- 会话 ID，索引：(session_id, created_at), (session_id, compacted)
message_id VARCHAR(64),   -- 关联的 assistant 最终消息
step_index INTEGER,       -- 步骤序号（0-based）
role VARCHAR(20),         -- 'assistant' | 'tool'
content TEXT,             -- 消息内容（tool 消息的 result / assistant 的 tool_calls JSON）
tool_name VARCHAR(100),   -- tool 消息专用
tool_call_id VARCHAR(100),-- tool 消息专用
compacted BOOLEAN DEFAULT FALSE,  -- Level 1 剪枝标记（原始内容保留，发给 LLM 时替换为占位符）
created_at TIMESTAMPTZ
```

### load_history() 加载策略（compaction 感知）

```python
# 倒序扫描 chat_messages，遇到 is_compaction=True 停止
# 只返回 compaction 节点及其之后的消息（genesis block 为历史起点）
# 同时加载对应时间范围内的 l3_steps，还原为 LLM messages 格式
# 无 compaction 节点 → 兼容旧逻辑（完整加载）
# 无 l3_steps 记录（老会话） → 降级为 user/assistant 文本 + [-10:] 模式
```

---

## 4. TodoStore（任务追踪）

`app/todo/store.py`

### 设计
- Redis List 存储，key = `todo:{session_id}`
- TTL = 7 天（比 WorkingMemory 更长，支持跨 session 任务）
- SubAgent（execute_raw）不设 session_id，TodoStore 检测到空字符串直接 return

### TodoItem（schemas.py）
```python
class TodoItem(BaseModel):
    id: str
    content: str
    status: str    # pending | in_progress | completed
    created_at: float
    updated_at: float
```

### TodoStore API
```python
class TodoStore:
    @staticmethod
    async def get(session_id: str) -> list[dict]
    @staticmethod
    async def set(session_id: str, todos: list[dict]) -> None
    @staticmethod
    async def add(session_id: str, item: dict) -> None
    @staticmethod
    async def update(session_id: str, item_id: str, updates: dict) -> None
```

---

## 5. Redis Key 命名规范

`app/cache/redis_client.py` → `RedisKeys`

| Key 格式 | 用途 |
|---------|------|
| `wm:{session_id}` | WorkingMemory Hash |
| `todo:{session_id}` | Todo 列表 |
| `blacklist:{jti}` | JWT 黑名单（注销 Token） |
| `codebook:{category}` | 码表缓存 |
| `prompt:{intent_tag}` | Prompt 模板缓存 |

---

## 6. 会话生命周期

```
首次请求（无 session_id）
    │
    ▼ 生成 UUID session_id
    │
    ▼ Redis 创建 wm:{session_id}（TTL=30min）
    │ CHAT_PERSIST_ENABLED → PG 创建 chat_sessions 记录
    │
    ▼ 正常对话（每轮请求）
    │ append user_msg + assistant_msg → Redis
    │ increment_turn（TTL 刷新）
    │ write-behind → PG chat_messages
    │
    ▼ 会话过期（30min 无活动）
    │ Redis key 自动过期（热层消失）
    │ PG 数据永久保留（冷层）
    │
    ▼ 再次访问（Redis miss）
    │ load_history(session_id) → PG 回源
    │ 重建 WorkingMemory
```
