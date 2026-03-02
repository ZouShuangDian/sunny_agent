# Tool System Spec — 工具系统、Skill、SubAgent

## 1. 工具抽象（BaseTool）

`app/tools/base.py`

所有工具必须继承 `BaseTool`，强制实现：

```python
class BaseTool(ABC):
    @property @abstractmethod
    def name(self) -> str: ...          # 工具唯一名称（snake_case）

    @property @abstractmethod
    def description(self) -> str: ...   # 工具描述（给 LLM 看）

    @property @abstractmethod
    def params_model(self) -> type[BaseModel]: ...  # 参数 Pydantic 模型

    @abstractmethod
    async def execute(self, args: dict) -> ToolResult: ...

    # 非抽象（有默认值）
    @property
    def tier(self) -> list[str]: return ["L1", "L3"]
    @property
    def timeout_ms(self) -> int: return settings.DEFAULT_TOOL_TIMEOUT_MS  # 60s
    @property
    def risk_level(self) -> str: return "read"
    def schema(self) -> dict: ...       # 自动生成 OpenAI function calling 格式
```

### ToolResult 标准化结果
```python
@dataclass
class ToolResult:
    status: str   # "success" | "error"
    data: dict    # 工具特定数据
    error: str | None

    def to_json(self) -> str: ...       # 序列化为 LLM tool result
    @classmethod
    def success(cls, **data): ...
    @classmethod
    def fail(cls, error: str): ...
```

### 风险等级（risk_level）
| 级别 | 含义 | 示例工具 |
|------|------|---------|
| `read` | 只读操作 | web_search, read_file |
| `suggest` | 建议操作，不执行 | （保留） |
| `write` | 写入/修改 | bash_tool, write_file |
| `critical` | 高风险（HITL 审批预留） | （保留） |

---

## 2. ToolRegistry

`app/tools/registry.py`

```python
registry.register(tool: BaseTool)
registry.execute(name: str, args: dict) -> str          # 返回 JSON 字符串
registry.get_all_schemas() -> list[dict]                # L1 用：全部工具
registry.get_schemas_by_tier(tier: str) -> list[dict]   # L3 用：过滤 tier
```

L1 与 L3 **共享同一个 ToolRegistry 实例**（Q1 裁决），通过 tier 过滤获取各自需要的工具集。

---

## 3. 内置工具清单（11 个）

`app/tools/builtin_tools/`

### 3.1 bash_tool（L3 only）
- 向 sandbox-service HTTP POST /exec，在隔离容器内执行 bash 命令
- 参数：command, timeout（1-300s，默认 30s）
- session_id + user_id 自动从 ContextVar 读取
- 容器内可用路径：
  - `/mnt/skills/{skill_name}/` — Skill 文件（只读）
  - `/mnt/users/{user_id}/uploads/` — 上传文件（只读）
  - `/mnt/users/{user_id}/outputs/{session_id}/` — 产出物（读写）
  - `/tmp/` — 临时工作区
- risk_level = "write"

### 3.2 read_file（L1+L3）
- 读取沙箱 volume 内的文件内容
- 路径必须在 SANDBOX_HOST_VOLUME 下（防路径穿越）

### 3.3 write_file（L3 only）
- 写入文件到用户输出目录 `/mnt/users/{user_id}/outputs/{session_id}/`
- risk_level = "write"

### 3.4 str_replace_file（L3 only）
- 对文件内容做字符串替换
- risk_level = "write"

### 3.5 present_files（L1+L3）
- 列出指定目录内的文件

### 3.6 web_search（L1+L3）
- 调用博查 Web Search API（BOCHA_API_KEY）
- 参数：query, num_results

### 3.7 web_fetch（L1+L3）
- 获取指定 URL 的页面内容（HTML → 文本）
- 参数：url, timeout

### 3.8 skill_call（L3 only）
- 元工具：从 `skill_context` ContextVar 读取当前用户可用 Skill 目录
- 参数：skill_name
- 返回：容器内 SKILL.md 的绝对路径（供后续 read_file + bash_tool 使用）
- **不执行 Skill，只返回路径**（pull 模式）

### 3.9 todo_write（L3 only）
- 读取/更新 Redis TodoStore（session_id 从 ContextVar 读取）
- 参数：action（add/update/complete/list）, items
- 每次返回完整 Todo 状态快照（Layer 2 感知层）

### 3.10 todo_read（L3 only）
- 只读查询当前 session 的 Todo 状态
- 返回完整快照

### 3.11 subagent_call（L3 only）
- 元工具：代理所有 SubAgent 调用
- 参数：agent_name, task（任务描述字符串）
- 委托给 SubAgentRegistry → SubAgentCallTool 执行

---

## 4. Skill 系统（DB 驱动）

`app/skills/service.py`

### 设计原则
- **无静态注册表**：启动时不扫描，无内存 SkillRegistry
- **每次请求动态查 DB**：`SkillService.get_user_skills(usernumb)`
- **零 Python 改动新增 Skill**：volume 放文件 + DB 插 1 条记录

### SkillInfo 数据类
```python
@dataclass
class SkillInfo:
    id: str
    name: str
    description: str
    path: str      # 相对路径，如 "skills/github"
    scope: str     # "system" | "user"

    def get_container_skill_path(self) -> str   # /mnt/{path}/SKILL.md
    def get_container_scripts_path(self) -> str # /mnt/{path}/scripts
    def get_host_skill_dir(self) -> Path        # 宿主机绝对路径（防路径穿越）
```

### 可见性规则
```sql
-- 系统 Skill（所有用户共享）
scope='system' AND is_active=TRUE AND COALESCE(uss.is_enabled, s.is_default_enabled)=TRUE

-- 用户 Skill（创建者私有）
scope='user' AND owner_usernumb=:usernumb AND is_active=TRUE AND COALESCE(uss.is_enabled, TRUE)=TRUE
```

优先级：`is_active=false`（admin 下线）> 用户显式设置 > 系统默认值

### DB 表
```sql
sunny_agent.skills (
    id UUID PRIMARY KEY,
    name VARCHAR(100) UNIQUE,
    description TEXT,
    path VARCHAR(500),          -- 相对路径，不含开头/、/SKILL.md
    scope VARCHAR(20),          -- system | user
    is_active BOOLEAN,
    is_default_enabled BOOLEAN,
    owner_usernumb VARCHAR(50)  -- scope=user 时的归属者
)

sunny_agent.user_skill_settings (
    skill_id UUID,
    usernumb VARCHAR(50),
    is_enabled BOOLEAN,
    PRIMARY KEY (skill_id, usernumb)
)
```

### Skill 执行流（pull 模式）
```
LLM 决策使用某 Skill
    │
    ▼ tool: skill_call(skill_name="github")
    │ → 从 skill_context ContextVar 找到 SkillInfo
    │ → 返回容器路径 /mnt/skills/github/SKILL.md
    │
    ▼ tool: read_file(path="/mnt/skills/github/SKILL.md")
    │ → 返回 SKILL.md 完整内容（含操作指引）
    │
    ▼ tool: bash_tool(command="python3 /mnt/skills/github/scripts/xxx.py")
    │ → 执行 Skill 脚本
```

---

## 5. SubAgent 系统

`app/subagents/`

### 架构
```
SubAgentRegistry
├── builtin_agents/          # 内置 SubAgent 目录
│   └── {agent_name}/
│       └── agent.md         # YAML frontmatter + 描述
└── ~/.sunny-agent/agents/   # 用户自定义（优先级更高，同名覆盖内置）
```

### agent.md 格式（frontmatter）
```yaml
---
name: supply_chain_agent
description: 供应链数据分析专家...
type: react_agent | local_code
max_depth: 2           # 防止递归调用层数过深
entry: app.subagents.builtin_agents.supply_chain.executor::SupplyChainExecutor  # local_code 时
---

# Agent 专业背景

你是供应链数据分析专家...（注入到 SubAgent system prompt）
```

### Agent 类型

**react_agent**：
- 使用 `L3ReActEngine.execute_raw()` 执行
- 独立 message 上下文（不继承主 Agent session）
- 可使用所有 L3 tier 工具
- 不设置 session_id（Todo 不追踪）

**local_code**：
- 实现 `LocalAgentExecutor` 抽象基类
- `async def execute(self, task: str) -> str`
- 内部可任意逻辑（多阶段 LLM、DB、规则引擎...）
- 返回字符串，主 Agent 作为 tool result 处理

### SubAgentCallTool（单一元工具）
```python
# 动态构建 description（枚举所有已注册 SubAgent）
subagent_call(
    agent_name: str,  # 必须在 registry 中存在
    task: str,        # 任务描述
) -> str             # SubAgent 执行结果报告
```

### 多目录加载优先级
```
from_directories([builtin_dir, user_dir])
# 按顺序加载：builtin 先，user_dir 后
# 同名 SubAgent → 后加载的覆盖（用户 > 内置）
```
