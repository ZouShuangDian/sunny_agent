# API 设计文档：插件管理系统

**关联 Spec**: [spec.md](./spec.md)
**关联原型**: [prototype.html](./prototype.html)
**Created**: 2026-03-10

## 概述

### 通用约定

| 约定 | 说明 |
|------|------|
| 基础路径 | `/api/plugins`（插件）、`/api/skills`（独立技能） |
| 认证方式 | JWT Bearer Token（`Authorization: Bearer <token>`） |
| 用户上下文 | 所有接口通过 `get_current_user` 依赖注入获取 `AuthenticatedUser` |
| 响应格式 | 统一信封 `{ success, code, message, data }` |
| 版本策略 | 当前无版本前缀，路径直接为 `/api/{resource}` |

### 统一响应信封

```json
{
  "success": true,
  "code": 0,
  "message": "ok",
  "data": { ... }
}
```

### 业务错误码

| Code | HTTP Status | 含义 |
|------|-------------|------|
| 0 | 200/201 | 成功 |
| 40000 | 400 | 请求参数错误（格式、缺字段、无效 ZIP） |
| 40100 | 401 | 未认证（Token 缺失/过期/黑名单） |
| 40300 | 403 | 无权限（非 owner、系统资源不可操作） |
| 40400 | 404 | 资源不存在 |
| 50000 | 500 | 服务器内部错误 |

---

## 插件 API（/api/plugins）

### 1. 获取插件列表

**`GET /api/plugins/list`** — 已实现

列出当前用户所有可见插件（系统预置 + 用户上传），含命令数。

**对应 Spec**: FR-001, FR-002 | **对应原型**: 左侧边栏插件列表

**Query 参数**: 无

**响应 data**:

```json
{
  "plugins": [
    {
      "id": "uuid",
      "name": "data",
      "version": "1.0.0",
      "description": "Write SQL, explore datasets...",
      "is_active": true,
      "command_count": 6,
      "created_at": "2026-03-10T08:00:00+08:00",
      "updated_at": "2026-03-10T08:00:00+08:00"
    }
  ],
  "total": 4
}
```

**待补充字段**:
- `scope`: `"system"` | `"user"` — 区分系统预置与用户上传（原型用 Uploaded 徽章标识）
- `is_enabled`: `boolean` — 当前用户的启用状态（需关联 user_plugin_settings）
- `owner_usernumb`: `string` — 插件所属用户

---

### 2. 获取插件详情

**`GET /api/plugins/{plugin_name}`** — 待实现

获取单个插件的完整信息，含 commands 列表和 skills 列表。

**对应 Spec**: FR-003 | **对应原型**: 右侧插件详情面板

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| plugin_name | string | 插件名称（URL path segment） |

**响应 data**:

```json
{
  "id": "uuid",
  "name": "data",
  "version": "1.0.0",
  "description": "Write SQL, explore datasets...",
  "is_active": true,
  "is_enabled": true,
  "scope": "system",
  "owner_usernumb": "1131618",
  "commands": [
    {
      "name": "analyze",
      "description": "Answer data questions -- from quick lookups to full analyses",
      "argument_hint": "your question about the data"
    },
    {
      "name": "build-dashboard",
      "description": "Build an interactive HTML dashboard with charts, filters, and tables",
      "argument_hint": null
    }
  ],
  "skills": [
    {
      "name": "data-context-extractor",
      "skill_md_path": "/mnt/users/1131618/plugins/data/skills/data-context-extractor/SKILL.md"
    },
    {
      "name": "chart-builder",
      "skill_md_path": "/mnt/users/1131618/plugins/data/skills/chart-builder/SKILL.md"
    }
  ],
  "try_asking": [
    "Explore and profile a dataset",
    "Build an interactive dashboard from my data"
  ],
  "created_at": "2026-03-10T08:00:00+08:00",
  "updated_at": "2026-03-10T08:00:00+08:00"
}
```

**权限**: 仅返回当前用户可见的插件（owner 或系统预置）

**错误场景**:

| HTTP | Code | 场景 |
|------|------|------|
| 404 | 40400 | 插件不存在或无权限访问 |

---

### 3. 切换插件启用/禁用

**`PATCH /api/plugins/{plugin_name}`** — 待实现

切换当前用户对某个插件的启用/禁用状态。禁用后该插件的所有 Commands 和 Skills 均不可用。

**对应 Spec**: FR-004, FR-005, FR-006, FR-007, FR-008 | **对应原型**: 详情面板 Toggle 开关

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| plugin_name | string | 插件名称 |

**请求 Body**:

```json
{
  "is_enabled": false
}
```

**响应 data**: `null`

**响应 message**: `"Plugin 'data' 已关闭"` / `"Plugin 'data' 已开启"`

**实现说明**:
- 需要新建 `user_plugin_settings` 表（参照 `user_skill_settings` 的 UPSERT 模式）
- 或在现有 `plugins` 表扩展用户级设置

**错误场景**:

| HTTP | Code | 场景 |
|------|------|------|
| 404 | 40400 | 插件不存在或未激活（is_active=FALSE） |

---

### 4. 上传插件包

**`POST /api/plugins/upload`** — 已实现

上传 ZIP 格式的插件包，校验后注册到 DB 和 volume。

**对应 Spec**: FR-017, FR-018, FR-019, FR-020, FR-021 | **对应原型**: Upload Plugin 弹窗

**请求**: `multipart/form-data`

| 字段 | 类型 | 说明 |
|------|------|------|
| file | File | ZIP 文件（≤ 10MB，`.zip` 后缀） |

**ZIP 目录结构**:

```
{plugin-name}/              ← 根目录（可选）
├── .claude-plugin/
│   └── plugin.json          ← 必须：name, version, description, author.usernumb
├── commands/
│   └── *.md                 ← 至少一个命令文件，frontmatter 含 description
└── skills/                  ← 可选
    └── {skill-name}/
        └── SKILL.md
```

**响应 data**（HTTP 201）:

```json
{
  "plugin": "manufacturing-qc",
  "version": "0.1.0",
  "description": "Quality control analysis...",
  "commands": [
    { "name": "analyze", "description": "Analyze quality data" }
  ]
}
```

**错误场景**:

| HTTP | Code | 场景 |
|------|------|------|
| 400 | 40000 | 非 .zip 文件、ZIP 结构错误、缺少 plugin.json、缺少 commands/、路径穿越 |
| 403 | 40300 | author.usernumb 与登录用户不匹配 |

---

### 5. 删除插件

**`DELETE /api/plugins/{plugin_name}`** — 已实现

删除当前用户上传的插件，级联清理 DB 记录和文件系统。

**对应 Spec**: FR-022, FR-023, FR-024 | **对应原型**: "..." 更多菜单 → 删除插件

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| plugin_name | string | 插件名称 |

**响应 data**: `null`

**响应 message**: `"Plugin 'manufacturing-qc' 已删除"`

**级联操作**:
1. DB: 删除 `plugins` 记录 → `plugin_commands` 通过 FK CASCADE 自动删除
2. Volume: 删除 `users/{usernumb}/plugins/{plugin_name}/` 目录

**错误场景**:

| HTTP | Code | 场景 |
|------|------|------|
| 404 | 40400 | 插件不存在或非当前用户所有 |

---

### 6. 浏览插件市场

**`GET /api/plugins/browse`** — 待实现

浏览所有可安装的插件（含已安装状态标记）。

**对应 Spec**: FR-029, FR-030, FR-031 | **对应原型**: Browse Plugins 弹窗

**Query 参数**:

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| tab | string | `"preset"` | 分类筛选：`preset`（系统预置）/ `uploaded`（用户上传） |
| q | string | - | 搜索关键词（匹配 name 和 description） |

**响应 data**:

```json
{
  "plugins": [
    {
      "name": "data",
      "version": "1.0.0",
      "description": "Write SQL, explore datasets...",
      "scope": "system",
      "is_installed": true,
      "command_count": 6
    }
  ],
  "total": 4
}
```

---

## 独立技能 API（/api/skills）

### 7. 获取技能列表

**`GET /api/skills/list`** — 已实现

列出当前用户所有可见的独立技能（系统预置 + 用户上传），含启用状态。

**对应 Spec**: FR-032, FR-033, FR-036 | **对应原型**: Skills 管理界面中间列

**Query 参数**: 无

**响应 data**:

```json
{
  "skills": [
    {
      "name": "brand-guidelines",
      "description": "Applies brand colors and typography to artifacts...",
      "scope": "system",
      "is_enabled": false,
      "is_default_enabled": false,
      "has_scripts": false
    },
    {
      "name": "qms-analysis",
      "description": "QMS data analysis skill...",
      "scope": "user",
      "is_enabled": true,
      "is_default_enabled": false,
      "has_scripts": true
    }
  ],
  "total": 10
}
```

**分组逻辑**（前端）:
- `scope = "user"` → "My skills" 分组
- `scope = "system"` → "Examples" 分组

---

### 8. 获取技能详情

**`GET /api/skills/{skill_name}`** — 待实现

获取单个技能的完整信息，含 SKILL.md 内容和文件列表。

**对应 Spec**: FR-034, FR-035 | **对应原型**: Skills 管理右侧详情面板

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| skill_name | string | 技能名称 |

**响应 data**:

```json
{
  "name": "brand-guidelines",
  "description": "Applies brand colors and typography to artifacts...",
  "scope": "system",
  "is_enabled": false,
  "is_default_enabled": false,
  "has_scripts": false,
  "added_by": "SunnyAgent",
  "skill_md_content": "# Brand Guidelines\n\n## Colors\n...",
  "files": [
    { "name": "SKILL.md", "type": "file" },
    { "name": "LICENSE.txt", "type": "file" }
  ],
  "created_at": "2026-03-10T08:00:00+08:00",
  "updated_at": "2026-03-10T08:00:00+08:00"
}
```

**`added_by` 逻辑**:
- `scope = "system"` → `"SunnyAgent"`
- `scope = "user"` → 当前用户名或工号

**错误场景**:

| HTTP | Code | 场景 |
|------|------|------|
| 404 | 40400 | 技能不存在或无权限访问 |

---

### 9. 切换技能启用/禁用

**`PATCH /api/skills/{skill_name}`** — 已实现

切换当前用户对某个独立技能的启用/禁用状态。

**对应 Spec**: FR-037, FR-038, FR-039, FR-040 | **对应原型**: Skill 详情面板 Toggle 开关

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| skill_name | string | 技能名称 |

**请求 Body**:

```json
{
  "is_enabled": true
}
```

**响应 data**: `null`

**响应 message**: `"Skill 'brand-guidelines' 已开启"`

**实现说明**:
- UPSERT `user_skill_settings` 表
- 系统和个人 Skill 均可操作
- 未操作过的 Skill 回退到 `is_default_enabled` 默认值

**错误场景**:

| HTTP | Code | 场景 |
|------|------|------|
| 404 | 40400 | 技能不存在或未激活（is_active=FALSE） |

---

### 10. 上传技能包

**`POST /api/skills/upload`** — 已实现

上传 ZIP 格式的技能包，校验后注册到 DB 和 volume。

**对应 Spec**: FR-041, FR-042, FR-043, FR-044, FR-045, FR-046 | **对应原型**: Upload Skill 弹窗

**请求**: `multipart/form-data`

| 字段 | 类型 | 说明 |
|------|------|------|
| file | File | ZIP 文件（≤ 10MB，`.zip` 后缀） |

**ZIP 目录结构**:

```
{skill-name}/              ← 根目录（可选）
├── SKILL.md               ← 必须：frontmatter 含 name + description
├── scripts/               ← 可选脚本目录
└── ...                    ← 其余文件原样保留
```

**响应 data**（HTTP 201）:

```json
{
  "skill": "qms-analysis",
  "description": "QMS data analysis skill...",
  "has_scripts": true,
  "is_new": true
}
```

**特殊逻辑**:
- 同名覆盖更新（UPSERT），`is_new` 字段标识
- 不允许覆盖系统 Skill
- 不允许覆盖其他用户的 Skill
- 首次上传自动启用（UPSERT `user_skill_settings`）
- 文件原子写入（临时目录 → 备份 → rename）

**错误场景**:

| HTTP | Code | 场景 |
|------|------|------|
| 400 | 40000 | 非 .zip 文件、缺少 SKILL.md、name/description 为空、路径穿越 |
| 403 | 40300 | 试图覆盖系统 Skill 或他人的 Skill |

---

### 11. 删除技能

**`DELETE /api/skills/{skill_name}`** — 已实现

删除当前用户上传的独立技能，级联清理。

**对应 Spec**: FR-047, FR-048, FR-049 | **对应原型**: "..." 更多菜单 → 删除技能

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| skill_name | string | 技能名称 |

**响应 data**: `null`

**响应 message**: `"Skill 'qms-analysis' 已删除"`

**级联操作**:
1. DB: 删除 `skills` 记录 → `user_skill_settings` 通过 FK CASCADE 自动删除
2. Volume: 删除 `users/{usernumb}/skills/{skill_name}/` 目录

**错误场景**:

| HTTP | Code | 场景 |
|------|------|------|
| 404 | 40400 | 技能不存在 |
| 403 | 40300 | 系统 Skill 不允许删除 / 非当前用户所有 |

---

## 命令自动完成 API

### 12. 获取可用命令列表

**`GET /api/commands/autocomplete`** — 待实现

返回当前用户所有已启用插件的可用命令列表，供对话输入框 `/` 触发自动完成使用。

**对应 Spec**: FR-009, FR-010, FR-011 | **对应原型**: 对话交互（不在管理界面原型中）

**Query 参数**:

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| q | string | - | 可选前缀过滤（模糊匹配 plugin_name:command_name） |

**响应 data**:

```json
{
  "commands": [
    {
      "plugin_name": "data",
      "command_name": "analyze",
      "full_command": "/data:analyze",
      "description": "Answer data questions -- from quick lookups to full analyses",
      "argument_hint": "your question about the data"
    },
    {
      "plugin_name": "data",
      "command_name": "build-dashboard",
      "full_command": "/data:build-dashboard",
      "description": "Build an interactive HTML dashboard...",
      "argument_hint": null
    }
  ],
  "total": 15
}
```

**过滤逻辑**:
- 仅返回 `is_active = TRUE` 的插件
- 仅返回用户已启用的插件（需关联 `user_plugin_settings`）
- 按 plugin_name → command_name 排序

---

## API 覆盖矩阵

| # | 端点 | 方法 | 状态 | Spec FR | 原型位置 |
|---|------|------|------|---------|---------|
| 1 | `/api/plugins/list` | GET | 已实现 | FR-001, FR-002 | 左侧插件列表 |
| 2 | `/api/plugins/{name}` | GET | 待实现 | FR-003 | 右侧详情面板 |
| 3 | `/api/plugins/{name}` | PATCH | 待实现 | FR-004~008 | 详情 Toggle |
| 4 | `/api/plugins/upload` | POST | 已实现 | FR-017~021 | Upload Plugin 弹窗 |
| 5 | `/api/plugins/{name}` | DELETE | 已实现 | FR-022~024 | ... → 删除插件 |
| 6 | `/api/plugins/browse` | GET | 待实现 | FR-029~031 | Browse Plugins 弹窗 |
| 7 | `/api/skills/list` | GET | 已实现 | FR-032, FR-033, FR-036 | Skills 列表面板 |
| 8 | `/api/skills/{name}` | GET | 待实现 | FR-034, FR-035 | Skill 详情面板 |
| 9 | `/api/skills/{name}` | PATCH | 已实现 | FR-037~040 | Skill Toggle |
| 10 | `/api/skills/upload` | POST | 已实现 | FR-041~046 | Upload Skill 弹窗 |
| 11 | `/api/skills/{name}` | DELETE | 已实现 | FR-047~049 | ... → 删除技能 |
| 12 | `/api/commands/autocomplete` | GET | 待实现 | FR-009~011 | 对话输入框 |

**已实现**: 7 个 | **待实现**: 5 个

---

## 待实现 API 的依赖分析

### 优先级 P1（核心功能缺失）

| 端点 | 阻塞的功能 | 前置依赖 |
|------|-----------|---------|
| `GET /api/plugins/{name}` | 插件详情面板无法加载 | 无 |
| `PATCH /api/plugins/{name}` | 插件无法启用/禁用 | 需新建 `user_plugin_settings` 表 |
| `GET /api/commands/autocomplete` | 对话框 `/` 自动完成不可用 | 依赖 `user_plugin_settings` 判断启用状态 |

### 优先级 P2（增强功能）

| 端点 | 阻塞的功能 | 前置依赖 |
|------|-----------|---------|
| `GET /api/skills/{name}` | Skill 详情面板无法加载 | 无 |
| `GET /api/plugins/browse` | 插件市场弹窗不可用 | 无 |

### 新增 DB 表

```sql
-- user_plugin_settings（参照 user_skill_settings 模式）
CREATE TABLE sunny_agent.user_plugin_settings (
    usernumb    VARCHAR(20)   NOT NULL,
    plugin_id   UUID          NOT NULL REFERENCES sunny_agent.plugins(id) ON DELETE CASCADE,
    is_enabled  BOOLEAN       NOT NULL,
    updated_at  TIMESTAMPTZ   DEFAULT now(),
    PRIMARY KEY (usernumb, plugin_id)
);
```

### 现有 API 需补充的字段

| 端点 | 需补充字段 | 说明 |
|------|-----------|------|
| `GET /api/plugins/list` | `scope`, `is_enabled`, `owner_usernumb` | 区分来源、显示启用状态和 Uploaded 徽章 |
