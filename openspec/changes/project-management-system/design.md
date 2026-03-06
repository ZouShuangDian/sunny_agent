## Context

Sunny Agent 当前已有基础的聊天会话（ChatSession）和文件下载功能，但缺少：
1. 项目级别的概念，无法将相关对话组织在一起
2. 项目维度的文件管理，文件散落在各个对话中
3. 统一文件管理，缺少文件元数据记录

现状：
- ChatSession 表已有基础字段（id, session_id, user_id, title, status）
- 文件存储在 SANDBOX_HOST_VOLUME/users/{usernumb}/outputs/{session_id}/ 目录
- 已有公司数据隔离机制（ABAC 过滤器）
- 已有 RBAC 角色系统（admin/manager/operator/viewer）

约束：
- 一个对话只能属于一个项目（一对一关系）
- 项目文件保留原始文件名，存储时添加 UUID 避免冲突
- 文件上传限制：Office 格式、.md、.txt，最大 50MB，白名单模式
- 生产环境使用 NAS 存储，开发环境使用本地路径
- 硬删除策略，项目删除时保留物理文件（不级联删除）
- 用户只能访问自己创建的项目（超级管理员除外）

## Goals / Non-Goals

**Goals:**
- 实现完整的项目 CRUD API（创建、查询、更新、删除）
- 实现项目文件上传、下载、删除功能（20MB 限制，Office/.md/.txt）
- 实现对话移动到项目功能
- 扩展 ChatSession 模型支持 project_id 关联
- 项目权限控制与公司数据隔离集成
- 创建 File 表统一管理所有文件
- 支持文件 Hash 去重、标签、描述

**Non-Goals:**
- 不支持项目共享/成员管理
- 不支持子项目/项目树结构（扁平项目列表）
- 不支持项目模板功能
- 不支持项目归档（硬删除）
- 不支持跨公司项目共享（公司隔离优先）
- 不实现前端界面（纯 API）
- 不实现病毒扫描（依赖上传者可信度）

## Decisions

### Decision 1: 对话与项目一对一关系

**选择**: ChatSession.project_id 外键（可选，唯一）

**理由**:
- 简单直接，查询高效
- 符合需求描述"一个对话只能属于一个项目"
- 项目删除时 SET NULL，对话保留

**备选方案**:
- 中间表 project_sessions（支持多对多）：增加复杂度，当前不需要

### Decision 2: 项目文件独立存储

**选择**: SANDBOX_HOST_VOLUME/users/{user_id}/outputs/projects/{project_id}/{uuid}_{original_name}

**理由**:
- 清晰的项目边界
- 统一的存储路径格式（与会话文件一致）
- 支持文件物理隔离

**备选方案**:
- 文件软关联（文件仍在原位置）：路径复杂，删除逻辑复杂

### Decision 3: 文件命名策略

**选择**: {original_filename}_{uuid}.{ext}

**理由**:
- 保留原始文件名便于识别
- UUID 避免同名冲突
- 前端显示原始文件名

**示例**:
- 原始：report.docx
- 存储：report_abc123.docx
- 前端显示：report.docx

### Decision 4: 硬删除 + 保留文件

**选择**: 删除项目时硬删除项目记录，但保留物理文件，文件记录标记为孤儿

**理由**:
- 保留用户数据，避免误删重要文件
- 简化删除逻辑，无需级联清理
- 对话保留避免误删重要数据
- 未来可扩展孤儿文件清理机制

**实现**:
- 项目记录硬删除
- 文件记录 project_id 置为 NULL
- 物理文件保留在存储中
- 暂不处理孤儿文件（未来清理）

**备选方案**:
- 级联删除：删除项目同时删除所有文件（已放弃）
- 软删除：增加 is_active 字段，需要定期清理机制

### Decision 5: 简单权限模型

**选择**: 用户只能访问自己创建的项目（超级管理员豁免）

**理由**:
- 简单直观，无需复杂的成员管理
- 符合个人项目管理场景
- 公司隔离作为额外保护层

**权限矩阵**:
| 用户类型 | 查看项目 | 编辑项目 | 删除项目 |
|----------|----------|----------|----------|
| 项目创建者 | ✓ | ✓ | ✓ |
| 其他用户 | ✗ | ✗ | ✗ |
| 超级管理员 | ✓ | ✓ | ✓ |

### Decision 6: 统一文件管理

**选择**: 创建 File 表统一管理所有文件（会话文件 + 项目文件）

**理由**:
- 统一的文件元数据管理
- 支持文件标签、描述、Hash 去重
- 清晰的文件归属（session 或 project）
- 便于文件权限控制和审计

**数据模型**:
```python
class File(Base):
    id: UUID (PK)
    file_name: str                    # 原始文件名
    file_path: str                    # 相对路径
    file_size: int                    # 字节
    mime_type: str                    # MIME 类型
    file_hash: str | None             # SHA256 hash（去重）
    description: str | None           # 文件描述（max 500）
    tags: list[str]                   # 标签数组（JSONB）
    uploaded_by: UUID (FK)
    session_id: str | None            # 关联会话
    project_id: UUID | None           # 关联项目
    file_context: str                 # "session" | "project"
```

**存储路径**:
```
SANDBOX_HOST_VOLUME/
└── users/
    └── {user_id}/
        └── outputs/
            ├── sessions/
            │   └── {session_id}/
            │       └── {uuid}_{original_name}
            └── projects/
                └── {project_id}/
                    └── {uuid}_{original_name}
```

**文件去重逻辑**:
- 上传时计算 SHA256 hash
- 检查同一项目/会话内是否有相同 hash 的文件
- 如已存在，返回现有记录（不重复上传）
- 如不存在，创建新记录

**文件标签和描述**:
- 标签：JSONB 数组 `["标签 1", "标签 2"]`
- 描述：可选，max 500 字符
- 上传时由用户提供

### Decision 7: 文件上传限制统一

**选择**: 会话文件和项目文件使用统一的上传限制

**限制规则**:
- 允许的文件类型：Office (.doc/.docx/.xls/.xlsx/.ppt/.pptx)、.md、.txt
- 最大文件大小：20MB
- 最多文件数：50 个/项目
- 错误提示：中文详细提示

**理由**:
- 统一的用户体验
- 简化合规检查
- 避免安全漏洞

### Decision 8: 历史文件兼容

**选择**: 历史文件不迁移，新文件使用新结构

**兼容策略**:
- 现有文件：`users/{usernumb}/outputs/{session_id}/{filename}`（无 File 记录）
- 新文件：`users/{user_id}/outputs/sessions/{session_id}/{uuid}_{filename}`（有 File 记录）
- 下载接口兼容两种路径

**理由**:
- 避免大规模数据迁移风险
- 渐进式升级
- 新老文件并存

## Risks / Trade-offs

**[Risk] 文件上传安全风险**
→ Mitigation:
  - 严格的文件类型验证（扩展名 + MIME 类型）
  - 文件大小限制（20MB）
  - 文件名 sanitization 防止路径遍历
  - 存储路径隔离（项目间不可访问）

**[Risk] 项目删除导致数据丢失**
→ Mitigation:
  - 删除前确认机制（前端提示）
  - 级联删除逻辑清晰记录
  - 考虑未来添加回收站功能

**[Risk] 权限检查复杂度增加**
→ Mitigation:
  - 统一的权限检查辅助函数
  - 管理员豁免逻辑
  - 充分的权限测试用例

**[Risk] NAS 存储依赖**
→ Mitigation:
  - 开发环境使用本地路径
  - 生产环境预先配置 NAS 挂载
  - 文件操作失败降级处理

**[Trade-off] 增加数据库查询复杂度**
- 项目关联增加 JOIN 查询
- 影响：查询性能略有下降
- 收益：项目维度的数据组织

**[Trade-off] 文件存储路径变更**
- 从 users/ 目录改为统一 users/{user_id}/outputs/结构
- 影响：需要新的存储配置
- 收益：文件路径统一，易于管理

## Migration Plan

### 阶段 1: 数据模型与迁移（1 天）
1. 创建 Project 模型
2. 创建 File 模型
3. 创建 Alembic 迁移脚本
4. 执行数据库迁移（测试环境）

### 阶段 2: 项目 CRUD API（1 天）
1. 实现项目 CRUD 接口
2. 实现权限检查辅助函数
3. 集成公司数据隔离

### 阶段 3: 文件管理 API（1.5 天）
1. 修改 present_files.py（创建 File 记录）
2. 实现项目文件上传（验证 + 存储 + 去重）
3. 实现文件下载
4. 实现文件列表
5. 实现文件删除

### 阶段 4: 对话关联（0.5 天）
1. 实现移动对话到项目
2. 实现项目内对话列表
3. 扩展会话查询支持 project 筛选

### 阶段 5: 文件标签和描述（0.5 天）
1. 实现标签管理 API
2. 实现描述更新 API

### 阶段 6: 测试与部署（1 天）
1. 单元测试
2. 集成测试
3. 生产环境部署

### 回滚策略
1. 回滚代码到上一个版本
2. 执行数据库回滚迁移（downgrade）
3. 删除 projects/ 目录
4. 验证功能正常

## Open Questions

1. **NAS 路径配置**
   - 需要运维团队提供生产环境 NAS 挂载路径
   - 确认 NAS 访问权限和配额

2. **文件上传并发限制**
   - 暂不限制，根据实际使用情况调整

3. **现有对话迁移**
   - 不需要将现有对话迁移到项目
   - 用户手动关联

4. **项目成员上限**
   - 不适用（不支持项目共享）

---

## Detailed Architecture

### System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend                                 │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────┐    │
│  │  Sidebar   │  │ Project Home │  │ Project Workspace    │    │
│  │  Projects  │  │  + New Proj  │  │  ┌────────┬────────┐ │    │
│  │  History   │  │  Project     │  │  │Sources │  Chat  │ │    │
│  └────────────┘  └──────────────┘  │  │ Panel  │ Panel  │ │    │
│                                     │  └────────┴────────┘ │    │
│                                     └──────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         API Layer                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ /api/projects│  │ /api/files   │  │ /api/sessions        │  │
│  │ GET/POST     │  │ GET          │  │ POST /{id}/move      │  │
│  │ /{id}        │  │ /download    │  │ GET /{id}/sessions   │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ /api/projects/{id}/files                                 │   │
│  │ GET (list) | POST (upload) | DELETE/{file_id}            │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Business Logic                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ProjectService│  │ FileService  │  │ PermissionService    │  │
│  │ - create     │  │ - upload     │  │ - check_project_access│ │
│  │ - get_by_id  │  │ - download   │  │ - check_file_access  │  │
│  │ - delete     │  │ - dedup_check│  │ - admin_exemption    │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Data Layer                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │   projects   │  │    files     │  │   chat_sessions      │  │
│  │   Table      │  │    Table     │  │   Table (+project_id)│  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              File System Storage                         │   │
│  │  users/{user_id}/outputs/                                │   │
│  │    ├── sessions/{session_id}/{uuid}_{filename}           │   │
│  │    └── projects/{project_id}/{uuid}_{filename}           │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow Diagrams

#### 1. 创建项目流程

```
User → Frontend → POST /api/projects → ProjectService
                                              │
                                              ▼
                                       验证权限（用户创建者）
                                              │
                                              ▼
                                       验证名称唯一性
                                              │
                                              ▼
                                       创建 Project 记录
                                              │
                                              ▼
                                       返回项目信息
                                              │
User ← Frontend ← JSON Response ←─────────────┘
```

#### 2. 上传文件流程（含去重）

```
User → Frontend → POST /api/projects/{id}/files
                              │
                              ▼
                       FileService.upload()
                              │
                              ├──→ 验证项目权限
                              ├──→ 验证文件类型
                              ├──→ 验证文件大小 (< 20MB)
                              ├──→ 验证文件数量 (< 50)
                              ├──→ 计算 SHA256 Hash
                              │
                              ▼
                       检查是否重复（同一项目内）
                              │
                    ┌─────────┴─────────┐
                    │                   │
                  已存在              不存在
                    │                   │
                    ▼                   ▼
              返回现有记录      保存到文件系统
                                    │
                                    ▼
                              创建 File 记录
                              (file_name, file_path,
                               file_hash, tags,
                               description, ...)
                                    │
                                    ▼
                              返回文件信息
                                    │
User ← Frontend ← JSON Response ←───┘
```

#### 3. 移动对话到项目流程

```
User → Frontend → POST /api/sessions/{id}/move
                              │
                              ▼
                       SessionMoveService
                              │
                              ├──→ 验证对话所有权
                              ├──→ 验证项目权限
                              ├──→ 检查对话是否已在项目中
                              │
                              ▼
                       更新 ChatSession.project_id
                              │
                              ▼
                       自动关联文件
                       (UPDATE files SET project_id
                        WHERE session_id = ?)
                              │
                              ▼
                       返回成功响应
                              │
User ← Frontend ← JSON Response
```

### Security Design

#### 权限检查矩阵

| 操作 | 项目创建者 | 其他用户 | 超级管理员 |
|------|-----------|---------|-----------|
| 创建项目 | ✓ | ✓ | ✓ |
| 查看自己的项目 | ✓ | - | ✓ |
| 查看他人的项目 | - | ✗ | ✓ |
| 编辑自己的项目 | ✓ | - | ✓ |
| 编辑他人的项目 | - | ✗ | ✓ |
| 删除自己的项目 | ✓ | - | ✓ |
| 删除他人的项目 | - | ✗ | ✓ |
| 上传文件到自己的项目 | ✓ | - | ✓ |
| 上传文件到他人的项目 | - | ✗ | ✓ |
| 下载项目文件 | ✓ | ✗ | ✓ |

#### 公司隔离规则

```python
async def check_project_access(
    project: Project,
    user: AuthenticatedUser,
) -> bool:
    # 1. 超级管理员豁免
    if "admin" in user.permissions:
        return True
    
    # 2. 公司隔离检查
    if project.company != user.company:
        raise HTTPException(403, "无权访问其他公司的项目")
    
    # 3. 项目创建者检查
    if project.owner_id != user.id:
        raise HTTPException(403, "无权访问该项目")
    
    return True
```

### Performance Considerations

#### 数据库索引策略

```sql
-- projects 表
CREATE INDEX idx_projects_owner ON projects(owner_id);
CREATE INDEX idx_projects_company ON projects(company);
CREATE INDEX idx_projects_updated ON projects(updated_at DESC);
CREATE UNIQUE INDEX uq_projects_owner_name 
  ON projects(owner_id, name) 
  WHERE is_deleted = FALSE;

-- files 表
CREATE INDEX idx_files_session ON files(session_id);
CREATE INDEX idx_files_project ON files(project_id);
CREATE INDEX idx_files_hash ON files(file_hash);  -- 去重查询
CREATE INDEX idx_files_uploaded_by ON files(uploaded_by);

-- chat_sessions 表
CREATE INDEX idx_chat_sessions_project ON chat_sessions(project_id);
```

#### 查询优化

**项目列表查询**（按更新时间倒序）:
```sql
SELECT p.*, 
       COUNT(DISTINCT f.id) as file_count,
       COUNT(DISTINCT cs.id) as conversation_count
FROM projects p
LEFT JOIN files f ON p.id = f.project_id
LEFT JOIN chat_sessions cs ON p.id = cs.project_id
WHERE p.owner_id = :user_id
  AND p.company = :user_company
  AND p.is_deleted = FALSE
GROUP BY p.id
ORDER BY p.updated_at DESC
LIMIT :limit OFFSET :offset;
```

**项目文件列表查询**（包含对话文件）:
```sql
SELECT f.*
FROM files f
WHERE f.project_id = :project_id
   OR f.session_id IN (
     SELECT session_id 
     FR

---

## Detailed Architecture

### System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend                                 │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────┐    │
│  │  Sidebar   │  │ Project Home │  │ Project Workspace    │    │
│  │  Projects  │  │  + New Proj  │  │  ┌────────┬────────┐ │    │
│  │  History   │  │  Project     │  │  │Sources │  Chat  │ │    │
│  └────────────┘  └──────────────┘  │  │ Panel  │ Panel  │ │    │
│                                     │  └────────┴────────┘ │    │
│                                     └──────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         API Layer                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ /api/projects│  │ /api/files   │  │ /api/sessions        │  │
│  │ GET/POST     │  │ GET          │  │ POST /{id}/move      │  │
│  │ /{id}        │  │ /download    │  │ GET /{id}/sessions   │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ /api/projects/{id}/files                                 │   │
│  │ GET (list) | POST (upload) | DELETE/{file_id}            │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Business Logic                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ProjectService│  │ FileService  │  │ PermissionService    │  │
│  │ - create     │  │ - upload     │  │ - check_project_access│ │
│  │ - get_by_id  │  │ - download   │  │ - check_file_access  │  │
│  │ - delete     │  │ - dedup_check│  │ - admin_exemption    │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Data Layer                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │   projects   │  │    files     │  │   chat_sessions      │  │
│  │   Table      │  │    Table     │  │   Table (+project_id)│  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              File System Storage                         │   │
│  │  users/{user_id}/outputs/                                │   │
│  │    ├── sessions/{session_id}/{uuid}_{filename}           │   │
│  │    └── projects/{project_id}/{uuid}_{filename}           │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow: File Upload with Dedup

```
User → Frontend → POST /api/projects/{id}/files
                              │
                              ▼
                       FileService.upload()
                              │
                              ├──→ 验证项目权限
                              ├──→ 验证文件类型
                              ├──→ 验证文件大小 (< 20MB)
                              ├──→ 验证文件数量 (< 50)
                              ├──→ 计算 SHA256 Hash
                              │
                              ▼
                       检查是否重复（同一项目内）
                              │
                    ┌─────────┴─────────┐
                    │                   │
                  已存在              不存在
                    │                   │
                    ▼                   ▼
              返回现有记录      保存到文件系统
                                    │
                                    ▼
                              创建 File 记录
                              (file_name, file_path,
                               file_hash, tags,
                               description, ...)
                                    │
                                    ▼
                              返回文件信息
```

### Security: Permission Matrix

| 操作 | 项目创建者 | 其他用户 | 超级管理员 |
|------|-----------|---------|-----------|
| 创建项目 | ✓ | ✓ | ✓ |
| 查看自己的项目 | ✓ | - | ✓ |
| 查看他人的项目 | - | ✗ | ✓ |
| 编辑自己的项目 | ✓ | - | ✓ |
| 编辑他人的项目 | - | ✗ | ✓ |
| 删除自己的项目 | ✓ | - | ✓ |
| 删除他人的项目 | - | ✗ | ✓ |
| 上传文件到自己的项目 | ✓ | - | ✓ |
| 上传文件到他人的项目 | - | ✗ | ✓ |
| 下载项目文件 | ✓ | ✗ | ✓ |

### Performance: Database Indexes

```sql
-- projects 表
CREATE INDEX idx_projects_owner ON projects(owner_id);
CREATE INDEX idx_projects_company ON projects(company);
CREATE INDEX idx_projects_updated ON projects(updated_at DESC);
CREATE UNIQUE INDEX uq_projects_owner_name 
  ON projects(owner_id, name) 
  WHERE is_deleted = FALSE;

-- files 表
CREATE INDEX idx_files_session ON files(session_id);
CREATE INDEX idx_files_project ON files(project_id);
CREATE INDEX idx_files_hash ON files(file_hash);  -- 去重查询
CREATE INDEX idx_files_uploaded_by ON files(uploaded_by);

-- chat_sessions 表
CREATE INDEX idx_chat_sessions_project ON chat_sessions(project_id);
```

### Error Codes

| 错误代码 | HTTP 状态码 | 说明 |
|---------|-----------|------|
| `PROJECT_NOT_FOUND` | 404 | 项目不存在 |
| `PROJECT_ACCESS_DENIED` | 403 | 无权访问项目 |
| `PROJECT_NAME_EXISTS` | 400 | 项目名称已存在 |
| `FILE_TYPE_NOT_ALLOWED` | 400 | 不支持的文件类型 |
| `FILE_SIZE_EXCEEDED` | 413 | 文件大小超限 |
| `FILE_COUNT_EXCEEDED` | 400 | 文件数量超限 |
| `FILE_DUPLICATE` | 400 | 文件已存在（去重） |
| `FILE_NOT_FOUND` | 404 | 文件不存在 |

### Deployment Checklist

**开发环境**:
- [ ] 配置本地文件存储路径
- [ ] 执行数据库迁移
- [ ] 测试文件上传下载
- [ ] 测试权限控制

**生产环境**:
- [ ] 配置 NAS 挂载路径
- [ ] 配置环境变量
- [ ] 执行数据库迁移
- [ ] 部署代码
- [ ] 重启服务
- [ ] 验证功能
- [ ] 配置监控告警

### Future Extensions

虽然当前版本不支持项目共享，但设计预留了扩展空间：

1. **ProjectMember 表**（未来添加）
2. **文件 CDN 加速**
3. **大文件分片上传**
4. **文件版本管理**
