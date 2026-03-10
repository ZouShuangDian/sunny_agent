## Why

当前 Sunny Agent 缺少项目级别的对话和文件管理功能，用户无法将相关对话组织到项目中，也无法在项目上下文中查看和管理文件。具体问题包括：

1. **对话分散**：用户的对话记录散落在 History 列表中，无法按项目或主题组织
2. **文件管理缺失**：对话中产生的文件无法统一管理，难以追溯和复用
3. **缺少项目上下文**：无法在项目维度积累知识和文件，每次对话都是孤立的
4. **协作困难**：虽然当前版本不支持共享，但需要为未来扩展预留空间

需要新增项目管理系统，支持项目创建、对话关联、统一文件管理，以满足企业级多项目协作需求。

## What Changes

### 新增功能

- **项目管理 REST API**：
  - `POST /api/projects` - 创建项目
  - `GET /api/projects` - 项目列表（分页、筛选）
  - `GET /api/projects/{id}` - 项目详情
  - `PUT /api/projects/{id}` - 更新项目
  - `DELETE /api/projects/{id}` - 删除项目（硬删除项目记录，保留物理文件）
  - 基于用户隔离：只能访问自己创建的项目（超级管理员豁免）

- **对话关联功能**：
  - `POST /api/sessions/{id}/move` - 移动对话到项目
  - `GET /api/projects/{id}/sessions` - 项目内对话列表
  - `GET /api/projects/{id}/files/all` - 项目聚合文件列表

- **统一文件管理**：
  - 创建 File 表统一管理所有文件元数据
  - 支持文件标签（JSONB 数组）
  - 支持文件描述（max 500 字符）
  - 支持文件 Hash 去重（SHA256）
   - 文件上传限制：50MB、白名单模式（PDF/Office/Markdown/文本/图片）

- **项目文件 API**：
  - `POST /api/projects/{id}/files/upload` - 上传文件
  - `GET /api/projects/{id}/files` - 文件列表
  - `GET /api/projects/{id}/files/{file_id}` - 下载文件
  - `DELETE /api/projects/{id}/files/{file_id}` - 删除文件
  - `PUT /api/files/{file_id}/metadata` - 更新文件标签和描述

### 数据模型变更

- **新增 Project 表**：存储项目基本信息（id, name, owner_id, company, created_at, updated_at）
- **新增 File 表**：统一管理所有文件（会话文件 + 项目文件）
- **扩展 ChatSession 表**：新增 project_id 外键（ON DELETE SET NULL）

### 权限控制

- 项目访问控制：用户只能访问自己创建的项目
- 公司数据隔离集成：用户只能访问自己公司的项目
- 超级管理员豁免：admin 角色可以访问所有项目

### 工具修改

- **present_files.py**：创建 File 记录，修改存储路径为 `sessions/{session_id}/{uuid}_{filename}`
- **files.py**：支持 File 表查询，验证文件访问权限

## Capabilities

### New Capabilities

1. **`project-crud`**: 项目 CRUD 操作
   - 创建项目（唯一名称约束）
   - 查询项目列表（按公司/所有者筛选）
   - 更新项目信息
   - 删除项目（硬删除项目记录，保留物理文件）

2. **`project-files`**: 项目文件管理
   - 文件上传（50MB 限制，白名单模式，用户级去重）
   - 文件下载（权限验证）
   - 文件删除（项目 Owner 可删除所有文件）
   - 文件列表聚合（包含对话文件，标记 AI 生成）
   - 文件元数据更新（标签、描述）
   - 文件预览（PDF/Office/图片/文本/Markdown）

3. **`session-move`**: 对话与项目关联
   - 移动对话到项目
   - 项目内对话列表
   - 自动关联文件
   - 对话与项目解绑

4. **`file-management`**: 统一文件管理
   - File 数据模型
   - 文件上传限制验证
   - SHA256 Hash 计算
   - 文件去重检查
   - 文件标签管理（JSONB）
   - 文件描述管理（max 500）
   - 历史文件兼容

### Modified Capabilities

1. **`chat-sessions`**: 扩展查询接口
   - 支持按项目筛选对话
   - 新增 project_id 关联字段
   - 项目内对话列表查询

## Impact

### 新增文件

| 文件路径 | 说明 | 行数预估 |
|----------|------|----------|
| `app/api/projects.py` | 项目管理 API | ~300 行 |
| `app/api/project_files.py` | 项目文件 API | ~400 行 |
| `app/db/models/project.py` | Project 模型 | ~50 行 |
| `app/db/models/file.py` | File 模型 | ~80 行 |

### 修改文件

| 文件路径 | 修改内容 | 影响范围 |
|----------|----------|----------|
| `app/db/models/chat.py` | ChatSession 新增 project_id 字段 | 低 |
| `app/db/filters.py` | 扩展 CompanyFilter 支持项目过滤 | 中 |
| `app/tools/builtin_tools/present_files.py` | 创建 File 记录，修改存储路径 | 高 |
| `app/api/files.py` | 支持 File 表查询 | 中 |
| `app/main.py` | 注册新项目路由 | 低 |

### 数据库变更

**新增表**:
- `projects` 表：项目信息
  - 字段：id, name, owner_id, company, created_at, updated_at
  - 索引：ix_projects_owner, ix_projects_company, uq_projects_owner_name
  
- `files` 表：统一文件管理
  - 字段：id, file_name, file_path, file_size, mime_type, file_hash, description, tags, uploaded_by, session_id, project_id, file_context, uploaded_at
  - 索引：ix_files_session, ix_files_project, ix_files_hash, ix_files_uploaded_by

**修改表**:
- `chat_sessions` 表：新增 project_id 外键
  - 约束：FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
  - 索引：ix_chat_sessions_project

### 配置变更

**.env 新增配置项**:
```ini
# 项目文件存储
PROJECT_FILE_STORAGE=/path/to/project_files  # 开发环境本地路径
PROJECT_FILE_STORAGE_NAS=/nas/sunny_agent/projects  # 生产环境 NAS 路径
```

### 依赖要求

**文件上传限制**:
- 文件类型：白名单模式（PDF、Word、Excel、PPT、Markdown、文本、图片）
- 文件大小：最大 50MB
- 文件去重：基于 SHA256 hash（用户级去重）
- 文件描述：max 500 字符
- 文件标签：JSONB 数组

**Python 依赖**:
- 无新增依赖（使用标准库 hashlib 计算 hash）

### 兼容性

**向后兼容**:
- 历史文件路径保持不变：`users/{usernumb}/outputs/{session_id}/{filename}`
- 新文件使用新路径：`users/{user_id}/outputs/sessions/{session_id}/{uuid}_{filename}`
- 下载接口兼容两种路径

**破坏性变更**:
- 无

## Success Criteria

### 功能完整性

- ✅ 用户可以创建、查询、更新、删除项目
- ✅ 用户可以将对话移动到项目
- ✅ 用户可以在项目中上传、下载、删除文件
- ✅ 用户可以查看项目内的所有文件（包括对话文件）
- ✅ 文件上传自动去重
- ✅ 支持文件标签和描述

### 性能指标

- 项目列表查询响应时间 < 200ms
- 文件上传响应时间 < 5s（10MB 文件）
- 文件下载响应时间 < 1s
- 支持至少 1000 个项目/用户
- 支持至少 50 个文件/项目

### 安全要求

- 100% 权限检查覆盖（无越权访问）
- 文件类型严格验证
- 文件大小严格限制
- 路径遍历攻击防护

### 测试覆盖

- 单元测试覆盖率 > 80%
- 集成测试覆盖所有 API 端点
- 权限测试覆盖所有场景
- 文件上传测试覆盖所有限制
