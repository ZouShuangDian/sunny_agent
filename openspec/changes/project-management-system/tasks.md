# Implementation Tasks

## 0. File 数据模型

**目标**: 创建统一的 File 数据模型，管理所有文件元数据

### 0.1 创建 File 模型

**文件**: `app/db/models/file.py`

**字段要求**:
- `id: UUID (PK)` - 文件记录 ID
- `file_name: str` - 原始文件名（含扩展名）
- `file_path: str` - 相对路径
- `file_size: int` - 字节
- `mime_type: str` - MIME 类型
- `file_extension: str` - 扩展名（小写）
- `storage_filename: str` - 存储文件名（{uuid}_{original_name}）
- `file_hash: str | None` - SHA256 hash（去重/完整性）
- `description: str | None` - 文件描述（max 500 字符）
- `tags: list[str]` - 标签数组（JSONB 存储）
- `uploaded_by: UUID (FK → User)` - 上传者
- `uploaded_at: datetime` - 上传时间
- `session_id: str | None` - 关联会话
- `project_id: UUID | None` - 关联项目
- `file_context: str` - "session" | "project"

**索引**:
- `ix_files_session`
- `ix_files_project`
- `ix_files_hash` (用于去重查询)
- `ix_files_uploaded_by`

**验收标准**:
- [x] 模型定义完整
- [x] 所有字段类型正确
- [x] 索引创建成功
- [x] 通过 SQLAlchemy 模型验证

### 0.2 创建 Alembic 迁移脚本

**文件**: `app/db/migrations/versions/{revision}_create_files_table.py`

**迁移内容**:
- 创建 `files` 表
- 包含所有字段
- 创建所有索引
- 添加外键约束（uploaded_by → users.id）

**验收标准**:
- [ ] 迁移脚本语法正确
- [ ] upgrade() 函数完整
- [ ] downgrade() 函数完整
- [ ] 通过 alembic check

### 0.3 执行数据库迁移

**命令**: `alembic upgrade head`

**验收标准**:
- [ ] 迁移执行成功
- [ ] 无错误日志
- [ ] 表结构正确

### 0.4 验证迁移结果

**验证内容**:
- 检查表结构
- 检查索引
- 检查约束

**验收标准**:
- [ ] 表字段数量正确
- [ ] 所有索引存在
- [ ] 外键约束生效

---

## 1. 数据模型与数据库迁移

**目标**: 创建 Project 模型并执行数据库迁移

### 1.1 创建 Project 模型

**文件**: `app/db/models/project.py`

**字段要求**:
- `id: UUID (PK)`
- `name: str (100)` - 项目名称
- `owner_id: UUID (FK → User)` - 项目创建者
- `company: str | None` - 公司（数据隔离）
- `created_at: datetime`
- `updated_at: datetime`

**索引**:
- `ix_projects_owner`
- `ix_projects_company`
- `ix_projects_updated` (DESC)
- `uq_projects_owner_name` (唯一约束)

**验收标准**:
- [x] 模型定义完整
- [x] 字段类型正确
- [ ] 索引定义正确
- [ ] 唯一约束正确

### 1.2 创建 Alembic 迁移脚本（projects 表）

**文件**: `app/db/migrations/versions/{revision}_create_projects_table.py`

**迁移内容**:
- 创建 `projects` 表
- 创建所有索引
- 添加外键约束
- 添加唯一约束

**验收标准**:
- [ ] 迁移脚本完整
- [ ] upgrade/downgrade 函数正确

### 1.3 创建 Alembic 迁移脚本（chat_sessions 扩展）

**文件**: `app/db/migrations/versions/{revision}_add_project_id_to_chat_sessions.py`

**迁移内容**:
- 给 `chat_sessions` 表添加 `project_id` 字段
- 外键约束：`ON DELETE SET NULL`
- 索引：`ix_chat_sessions_project`

**验收标准**:
- [ ] 字段添加正确
- [ ] 外键约束正确

### 1.4 执行数据库迁移

**命令**: `alembic upgrade head`

**验收标准**:
- [ ] 迁移执行成功
- [ ] projects 表创建成功
- [ ] chat_sessions 表扩展成功

### 1.5 验证迁移结果

**验证内容**:
- 检查 projects 表结构
- 检查 chat_sessions 表扩展
- 检查所有索引和约束

**验收标准**:
- [ ] 所有表结构正确
- [ ] 所有索引存在
- [ ] 约束生效

---

## 2. 修改现有文件工具

**目标**: 修改 present_files.py 和 files.py，支持 File 表统一管理

### 2.1 修改 present_files.py 创建 File 记录

**文件**: `app/tools/builtin_tools/present_files.py`

**修改内容**:
- 导入 File 模型和数据库会话
- 在写入文件后创建 File 记录
- 计算 file_hash（SHA256）
- 支持 description 和 tags 参数
- 修改存储路径为 `sessions/{session_id}/{uuid}_{filename}`

**验收标准**:
- [x] File 记录创建成功
- [ ] file_hash 计算正确
- [ ] 存储路径格式正确
- [ ] description 和 tags 支持正确

### 2.2 修改文件存储路径统一格式

**修改内容**:
- 统一使用 `users/{user_id}/outputs/` 前缀
- 会话文件：`sessions/{session_id}/{uuid}_{filename}`
- 项目文件：`projects/{project_id}/{uuid}_{filename}`

**验收标准**:
- [ ] 路径格式统一
- [ ] 文件保存成功

### 2.3 修改 files.py 支持 File 表查询

**文件**: `app/api/files.py`

**修改内容**:
- 导入 File 模型
- 从 File 表查询文件元数据
- 验证文件访问权限
- 支持历史文件路径兼容

**验收标准**:
- [ ] File 表查询正确
- [ ] 权限验证生效
- [ ] 历史文件兼容

### 2.4 添加文件上传限制验证

**验证内容**:
- 文件类型验证（Office/.md/.txt）
- 文件大小验证（< 20MB）
- MIME 类型验证
- 文件数量验证（< 50 个/项目）

**验收标准**:
- [x] 所有验证正确
- [ ] 错误提示清晰

### 2.5 添加文件去重检查逻辑

**去重逻辑**:
- 同一会话内去重
- 同一项目内去重

**验收标准**:
- [ ] 会话内去重正确
- [ ] 项目内去重正确
- [ ] 返回现有记录正确

---

## 3. 项目 CRUD API 实现

**目标**: 实现完整的项目增删改查 API

### 3.1 创建 app/api/projects.py

**文件**: `app/api/projects.py`

**内容**:
- 导入依赖
- 创建 APIRouter
- 定义 Pydantic 模型

**验收标准**:
- [ ] 文件结构正确
- [ ] Pydantic 模型定义完整

### 3.2 实现 POST /api/projects

**功能**: 创建新项目

**验收标准**:
- [x] 创建成功
- [ ] 名称唯一性验证
- [ ] 返回正确的项目信息

### 3.3 实现 GET /api/projects

**功能**: 项目列表（分页 + 筛选）

**验收标准**:
- [ ] 分页正确
- [ ] 筛选正确
- [ ] 排序正确（updated_at DESC）

### 3.4 实现 GET /api/projects/{id}

**功能**: 项目详情

**验收标准**:
- [ ] 返回项目详情
- [ ] 包含文件数、对话数统计

### 3.5 实现 PUT /api/projects/{id}

**功能**: 更新项目信息

**验收标准**:
- [ ] 更新成功
- [ ] 权限验证正确
- [ ] 名称唯一性验证

### 3.6 实现 DELETE /api/projects/{id}

**功能**: 删除项目（硬删除项目记录，保留物理文件）

**删除内容**:
- 硬删除项目记录
- 文件记录 project_id 置为 NULL（保留记录）
- 物理文件保留（不删除）
- 对话 project_id SET NULL（不删除）

**验收标准**:
- [x] 项目删除成功
- [ ] 文件记录保留（project_id=NULL）
- [x] 物理文件保留
- [ ] 对话保留

### 3.7 实现项目权限检查辅助函数

**函数**: `check_project_access(project_id, user)`

**检查逻辑**:
1. 超级管理员豁免
2. 公司隔离检查
3. 项目创建者检查

**验收标准**:
- [x] 权限检查正确
- [ ] 管理员豁免正确
- [ ] 公司隔离正确

---

## 4. 项目文件 API 实现

**目标**: 实现项目文件上传、下载、删除 API

### 4.1 创建 app/api/project_files.py

**文件**: `app/api/project_files.py`

**验收标准**:
- [ ] 文件结构正确

### 4.2 实现文件上传验证逻辑

**验证内容**:
- 文件类型、大小、MIME、数量
- 项目权限
- 文件名合法性

**验收标准**:
- [x] 所有验证正确

### 4.3 实现 POST /api/projects/{id}/files/upload

**功能**: 上传文件到项目

**验收标准**:
- [x] 上传成功
- [x] 去重检查正确
- [x] File 记录创建成功

### 4.4 实现 GET /api/projects/{id}/files

**功能**: 文件列表（包含对话文件）

**验收标准**:
- [ ] 包含项目直接上传的文件
- [ ] 包含项目内对话的文件
- [ ] 排序正确

### 4.5 实现 GET /api/projects/{id}/files/{file_id}/download

**功能**: 下载文件

**验收标准**:
- [ ] 下载成功
- [ ] 权限验证正确
- [ ] Content-Type 正确

### 4.6 实现 DELETE /api/projects/{id}/files/{file_id}

**功能**: 删除文件

**验收标准**:
- [ ] 删除成功
- [ ] 物理文件删除
- [ ] File 记录删除

### 4.7 实现 PUT /api/files/{file_id}/metadata

**功能**: 更新文件标签和描述

**验收标准**:
- [ ] 更新成功
- [ ] description 限制 500 字符
- [ ] tags 为 JSONB 数组

### 4.8 配置文件存储路径

**文件**: `app/.env.example`

**配置项**: `PROJECT_FILE_STORAGE`

**验收标准**:
- [ ] 配置项添加
- [ ] 文档说明清晰

---

## 5. 对话关联功能实现

### 5.1 扩展 app/api/chat.py 支持 project 筛选

**验收标准**:
- [ ] 筛选正确

### 5.2 创建 app/api/session_move.py

**验收标准**:
- [ ] 文件创建成功

### 5.3 实现 POST /api/sessions/{id}/move

**功能**: 移动对话到项目

**验收标准**:
- [x] 移动成功
- [ ] ChatSession.project_id 更新
- [ ] 文件自动关联
- [ ] 权限验证正确

### 5.4 实现 GET /api/projects/{id}/sessions

**功能**: 项目内对话列表

**验收标准**:
- [ ] 返回项目内的对话
- [ ] 排序正确

### 5.5 实现 GET /api/projects/{id}/files/all

**功能**: 项目聚合文件列表

**验收标准**:
- [ ] 包含项目直接上传的文件
- [ ] 包含项目内对话的文件
- [ ] 去重正确

---

## 6. 权限与数据隔离集成

### 6.1 扩展 CompanyFilter 支持项目过滤

**验收标准**:
- [ ] 过滤正确

### 6.2 扩展 CompanyFilter 支持文件过滤

**验收标准**:
- [ ] 过滤正确

### 6.3 项目权限检查集成

**验收标准**:
- [ ] 所有端点都有权限检查

### 6.4 文件下载权限检查

**验收标准**:
- [x] 权限检查正确

### 6.5 超级管理员豁免逻辑

**验收标准**:
- [ ] 管理员豁免正确

### 6.6 编写权限测试用例

**验收标准**:
- [ ] 测试覆盖所有场景
- [ ] 测试通过

---

## 7. 配置与文档

### 7.1 更新 app/.env.example

**验收标准**:
- [ ] 配置项添加

### 7.2 更新 app/config.py

**验收标准**:
- [ ] 配置类定义正确

### 7.3 更新 README（项目管理章节）

**验收标准**:
- [ ] README 更新完整

### 7.4 编写项目文件上传配置指南

**验收标准**:
- [ ] 指南完整清晰

### 7.5 编写文件标签和描述使用指南

**验收标准**:
- [ ] 指南完整清晰

---

## 8. 测试与验证

### 8.1 单元测试：项目 CRUD

**验收标准**:
- [ ] 测试通过
- [ ] 覆盖率 > 80%

### 8.2 单元测试：项目文件上传下载

**验收标准**:
- [ ] 测试通过

### 8.3 单元测试：文件 Hash 计算

**验收标准**:
- [ ] 测试通过

### 8.4 单元测试：文件去重逻辑

**验收标准**:
- [ ] 测试通过

### 8.5 单元测试：文件标签和描述

**验收标准**:
- [ ] 测试通过

### 8.6 单元测试：对话移动

**验收标准**:
- [ ] 测试通过

### 8.7 集成测试：项目权限检查

**验收标准**:
- [ ] 测试通过

### 8.8 集成测试：公司数据隔离

**验收标准**:
- [ ] 测试通过

### 8.9 文件上传测试

**验收标准**:
- [ ] 测试通过

### 8.10 性能测试：项目文件列表查询

**验收标准**:
- [ ] 查询响应时间 < 200ms

---

## 9. 上线部署

### 9.1 生产环境数据库迁移

**验收标准**:
- [ ] 迁移成功

### 9.2 配置 NAS 存储路径

**验收标准**:
- [ ] NAS 挂载成功
- [ ] 权限正确

### 9.3 部署代码到生产环境

**验收标准**:
- [ ] 部署成功

### 9.4 验证项目 CRUD 功能

**验收标准**:
- [ ] 功能正常

### 9.5 验证项目文件上传下载

**验收标准**:
- [ ] 功能正常

### 9.6 验证文件去重功能

**验收标准**:
- [ ] 去重正常

### 9.7 验证权限控制

**验收标准**:
- [ ] 权限正常

### 9.8 配置监控告警

**监控指标**:
- 文件上传失败率
- 权限错误次数
- 平均上传耗时

**验收标准**:
- [ ] 监控配置完成
- [ ] 告警规则配置
