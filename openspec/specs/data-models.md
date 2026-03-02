# Data Models Spec — 数据库模型与 Milvus Schema

## 1. PostgreSQL 表清单

Schema: `sunny_agent`，Alembic head: `f1e2d3c4b5a6`

---

### users
```sql
CREATE TABLE sunny_agent.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    usernumb VARCHAR(50) NOT NULL UNIQUE,    -- 工号（核心标识）
    username VARCHAR(100),                    -- 姓名（非唯一，可为空）
    password_hash VARCHAR(255) NOT NULL,     -- bcrypt hash
    role VARCHAR(50) DEFAULT 'viewer',       -- viewer | editor | admin
    department VARCHAR(100),
    data_scope JSONB DEFAULT '{}',           -- 数据权限范围
    permissions JSONB DEFAULT '[]',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
```

---

### audit_logs
```sql
CREATE TABLE sunny_agent.audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id VARCHAR(64),
    user_id UUID REFERENCES users(id),
    usernumb VARCHAR(50),
    action VARCHAR(100),         -- chat | chat_stream | plugin_command | login | logout
    route VARCHAR(50),           -- standard_l1 | deep_l3
    input_text TEXT,             -- 用户原始输入（不加密）
    duration_ms INTEGER,
    metadata JSONB DEFAULT '{}', -- 扩展字段（intent, confidence, iterations...）
    created_at TIMESTAMPTZ DEFAULT NOW()
)
-- 索引建议：(usernumb, created_at)，(trace_id)
```

---

### codebook
```sql
CREATE TABLE sunny_agent.codebook (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(200) NOT NULL,          -- 标准码
    alias VARCHAR(200) NOT NULL,         -- 归一化别名（小写+去分隔符）
    alias_display VARCHAR(200),          -- 原始展示名
    category VARCHAR(100),               -- 码表分类（product/process/quality...）
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
)
-- 索引：(alias, category)，(category)
```

---

### prompt_templates
```sql
CREATE TABLE sunny_agent.prompt_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    tier VARCHAR(10) NOT NULL,           -- L1 | L3
    match_text TEXT,                     -- 用于 embedding 匹配的文本
    prompt_content TEXT NOT NULL,        -- Prompt 正文
    intent_tags JSONB DEFAULT '[]',      -- 意图标签数组，如 ["writing", "summary"]
    is_default BOOLEAN DEFAULT FALSE,    -- 是否为默认 Prompt（tier 内只有 1 个 default）
    version VARCHAR(16) DEFAULT '1.0',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
```

---

### skills
```sql
CREATE TABLE sunny_agent.skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    path VARCHAR(500) NOT NULL,          -- 相对路径，如 skills/github
    scope VARCHAR(20) NOT NULL,          -- system | user
    is_active BOOLEAN DEFAULT TRUE,
    is_default_enabled BOOLEAN DEFAULT TRUE,  -- 系统 Skill 默认开关
    owner_usernumb VARCHAR(50),               -- scope=user 时的归属工号
    created_at TIMESTAMPTZ DEFAULT NOW()
)
```

---

### user_skill_settings
```sql
CREATE TABLE sunny_agent.user_skill_settings (
    skill_id UUID REFERENCES skills(id),
    usernumb VARCHAR(50),
    is_enabled BOOLEAN NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (skill_id, usernumb)
)
```

---

### plugins
```sql
CREATE TABLE sunny_agent.plugins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    name VARCHAR(100) NOT NULL,
    display_name VARCHAR(200),
    description TEXT,
    file_path VARCHAR(500),              -- .claude-plugin/ 目录宿主机路径
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, name)
)
```

---

### plugin_commands
```sql
CREATE TABLE sunny_agent.plugin_commands (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plugin_id UUID REFERENCES plugins(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    description VARCHAR(500),
    command_file_path VARCHAR(500),      -- COMMAND.md 宿主机绝对路径
    created_at TIMESTAMPTZ DEFAULT NOW()
)
```

---

### chat_sessions
```sql
CREATE TABLE sunny_agent.chat_sessions (
    id UUID PRIMARY KEY,                 -- 与 WorkingMemory session_id 对应
    user_id UUID REFERENCES users(id),
    title VARCHAR(500),                  -- 第一条消息前 50 字
    first_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
```

---

### chat_messages
```sql
CREATE TABLE sunny_agent.chat_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES chat_sessions(id),
    role VARCHAR(20) NOT NULL,           -- user | assistant | tool
    content TEXT,
    tool_calls JSONB,                    -- list[ToolCall] JSON
    reasoning_trace JSONB,              -- L3 推理轨迹（仅 assistant）
    intent_primary VARCHAR(100),
    route VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW()
)
-- 索引：(session_id, created_at)
```

---

## 2. Alembic 迁移记录

| 版本 | 内容 |
|------|------|
| `02808ce30c7b` | 初始化：users, roles, audit_logs, codebook |
| `edbe70b998c2` | 新增 usernumb 字段 |
| `684e135865f9` | username 非唯一约束，usernumb NOT NULL |
| `ac2143083ccf` | 修复 timestamp 字段类型 |
| `06621b9b3bd3` | 更新 prompt_templates v4（tier 字段匹配） |
| `5582a6f57d74` | 新增 chat_sessions + chat_messages |
| `a1b2c3d4e5f6` | 新增 skills + user_skill_settings |
| `f1e2d3c4b5a6` | 新增 plugins + plugin_commands（当前 head） |

### env.py 关键设计
```python
# 只管理 sunny_agent schema，避免影响其他项目
def include_name(name, type_, parent_names, reflected, compare_to):
    if type_ == "schema":
        return name == "sunny_agent"
    return True

# 异步执行
asyncio.run(run_migrations_online())
```

---

## 3. Milvus Schema

集合名：`l1_prompt_templates`

```python
fields = [
    FieldSchema("id",             INT64,  is_primary=True, auto_id=True),
    FieldSchema("template_id",    VARCHAR, max_length=64),   # PG UUID
    FieldSchema("name",           VARCHAR, max_length=100),
    FieldSchema("tier",           VARCHAR, max_length=10),   # L1 / L3
    FieldSchema("match_text",     VARCHAR, max_length=2000), # embedding 来源
    FieldSchema("prompt_content", VARCHAR, max_length=8000), # Prompt 正文
    FieldSchema("intent_tags",    VARCHAR, max_length=500),  # JSON
    FieldSchema("is_default",     BOOL),
    FieldSchema("version",        VARCHAR, max_length=16),
    FieldSchema("embedding",      FLOAT_VECTOR, dim=1024),   # bge-m3
]

index_params = {
    "metric_type": "COSINE",
    "index_type": "HNSW",
    "params": {"M": 16, "efConstruction": 256},
}
```

### 检索参数
```python
search_params = {"metric_type": "COSINE", "params": {"ef": 128}}
expr = f'tier == "L1"'
limit = PROMPT_SEARCH_TOP_K (=3)
threshold = PROMPT_SEARCH_THRESHOLD (=0.5)
```

---

## 4. Redis Key 命名规范

| Key 模式 | 类型 | TTL | 用途 |
|---------|------|-----|------|
| `wm:{session_id}` | Hash | 1800s | WorkingMemory |
| `todo:{session_id}` | List | 7天 | Todo 状态 |
| `blacklist:{jti}` | String | access_token 剩余有效期 | JWT 黑名单 |
| `codebook:{category}` | Hash | 3600s | 码表缓存 |
| `prompt:{intent_tag}` | String | 3600s | Prompt 模板缓存 |
