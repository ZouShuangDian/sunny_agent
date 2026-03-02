# Plugin System Spec — 插件系统

## 1. 概念

Plugin 是用户可上传的**工作流扩展包**，通过 `/{plugin}:{command}` 触发，绕过意图分析直接进入 L3 执行。

与 Skill 的区别：

| | Plugin | Skill |
|---|---|---|
| 触发方式 | `/{plugin}:{command}` 消息前缀 | `skill_call` 工具（LLM 主动调用） |
| 上传方式 | ZIP 包上传（`/api/plugins/upload`） | volume 文件 + DB 插入 |
| 内容 | COMMAND.md 工作流 + 可选 SKILL.md | 仅 SKILL.md + scripts/ |
| 适用场景 | 明确命令式工作流 | LLM 自主选用的能力 |

---

## 2. Plugin 包结构

```
my-plugin.zip
└── .claude-plugin/
    ├── plugin.json          # 必填，Plugin 元数据
    └── commands/
        ├── analyze.md       # 命令 analyze 的 COMMAND.md
        ├── report.md        # 命令 report 的 COMMAND.md
        └── skills/          # 可选，Plugin 内置 Skill
            └── my-skill/
                ├── SKILL.md
                └── scripts/
                    └── run.py
```

### plugin.json 格式
```json
{
  "name": "supply-chain",             // 正则: ^[a-z][a-z0-9-]{0,62}$
  "display_name": "供应链分析插件",
  "description": "提供供应链数据分析功能",
  "version": "1.0.0",
  "author": {
    "usernumb": "admin"             // 必须与当前登录用户匹配
  }
}
```

---

## 3. 上传校验（安全）

`app/plugins/service.py`

### ZIP 安全校验
```python
# 拒绝含以下内容的 ZIP 成员名：
# - ".." 路径穿越
# - 以 "/" 开头的绝对路径
# - 包含 ":" 的 Windows 路径

# 解压后用 resolve() + relative_to() 二次验证
for member in zip_ref.namelist():
    extracted = (target_dir / member).resolve()
    extracted.relative_to(target_dir.resolve())  # 不在目标目录内 → 拒绝
```

### plugin.json 校验
1. 必填字段：name, display_name, description, author.usernumb
2. `author.usernumb` 必须与当前登录用户的 usernumb 匹配
3. `name` 正则：`^[a-z][a-z0-9-]{0,62}$`
4. commands/*.md 每个文件须含 frontmatter `description` 字段

---

## 4. UPSERT 策略

同名 Plugin 重新上传 = 完全覆盖：
```sql
-- 1. UPDATE plugins（元数据更新）
-- 2. DELETE plugin_commands WHERE plugin_id=...
-- 3. INSERT plugin_commands（全量重建）
-- 文件：直接覆写到同路径
```

---

## 5. DB 表结构

```sql
sunny_agent.plugins (
    id UUID PRIMARY KEY,
    user_id UUID,                   -- 归属用户
    name VARCHAR(100),              -- 如 supply-chain
    display_name VARCHAR(200),
    description TEXT,
    file_path VARCHAR(500),         -- 解压后 .claude-plugin/ 目录路径
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    UNIQUE(user_id, name)           -- 同用户同名 Plugin 唯一
)

sunny_agent.plugin_commands (
    id UUID PRIMARY KEY,
    plugin_id UUID REFERENCES plugins(id),
    name VARCHAR(100),              -- 命令名（如 analyze）
    description VARCHAR(500),
    command_file_path VARCHAR(500), -- COMMAND.md 绝对路径
    created_at TIMESTAMPTZ
)
```

---

## 6. 触发与执行流程

### 判断 Plugin 命令
```python
def _is_plugin_command(message: str) -> bool:
    if not message.startswith("/"):
        return False
    first_token = message.split()[0]
    return ":" in first_token
# 示例："/supply-chain:analyze 请分析本月库存" → True
```

### 快速路径（绕过意图管线）
```
用户消息：/supply-chain:analyze 请分析本月库存
    │
    ▼ _is_plugin_command() → True
    │
    ▼ 解析: plugin_name="supply-chain", command_name="analyze"
           user_context="请分析本月库存"
    │
    ▼ plugin_service.get_user_command(plugin_name, command_name, usernumb)
    │ → DB 查询，未找到 → 返回错误消息
    │
    ▼ plugin_service.read_command_content(info)
    │ → 读取 COMMAND.md 文件内容
    │
    ▼ plugin_service.scan_plugin_skills(info)
    │ → 扫描 commands/skills/ 目录，返回 [{name, skill_md_path}]
    │
    ▼ 设置 PluginCommandContext ContextVar
    │ {plugin_name, command_name, command_md_content, plugin_skills}
    │
    ▼ 构建 synthetic IntentResult（route="deep_l3", confidence=1.0）
    │
    ▼ L3ReActEngine.execute()
    │ → _build_initial_messages() 检测到 plugin_context ContextVar
    │ → system prompt 末尾注入 COMMAND.md + Plugin Skill 列表
    │
    ▼ L3 推理执行（LLM 按 COMMAND.md 指引操作）
```

### PluginCommandContext
```python
@dataclass
class PluginCommandContext:
    plugin_name: str
    command_name: str
    command_md_content: str           # COMMAND.md 完整内容
    plugin_skills: list[dict]         # [{name, skill_md_path}, ...]
```

---

## 7. Plugin 内置 Skill（隔离）

Plugin 内置 Skill 与全局 Skill 系统**完全隔离**：

- **访问方式**：通过 `read_file` 读取 SKILL.md 路径，不走全局 `skill_call`
- **System prompt 规范**：注入时明确写"禁止通过 skill_call 调用，插件 Skill 不在全局 catalog 中"
- **原因**：Plugin Skill 是 Plugin 作者私有的，不应暴露给其他用户的 Skill catalog

---

## 8. API 端点

```
POST /api/plugins/upload
  → Content-Type: multipart/form-data
  → Body: file=<zip>
  → 返回: {plugin_name, commands: [...]}

GET /api/plugins/list
  → 返回: [{name, display_name, commands: [...]}]

DELETE /api/plugins/{plugin_name}
  → 删除 DB 记录 + 文件目录
```
