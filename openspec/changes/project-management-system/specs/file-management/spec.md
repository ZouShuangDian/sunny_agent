# File Management Capability

> 统一文件管理规范：File 模型、文件上传限制、Hash 去重、标签描述

## ADDED Requirements

### Requirement: 系统统一管理所有文件

系统 SHALL 通过 File 表统一管理会话文件和项目文件的元数据。

#### Scenario: 会话文件上传
- **WHEN** 用户在对话中上传文件
- **THEN** 创建 File 记录，file_context='session'，记录 session_id、file_hash、tags、description

#### Scenario: 项目文件上传
- **WHEN** 用户在项目中上传文件
- **THEN** 创建 File 记录，file_context='project'，记录 project_id、file_hash、tags、description

#### Scenario: 文件归属
- **WHEN** 查询文件归属
- **THEN** 一个文件只能属于一种上下文（session 或 project）

### Requirement: 文件上传限制

系统 SHALL 对所有文件上传实施统一的类型和大小限制。

#### Scenario: 允许的文件类型
- **WHEN** 上传 Office (.doc/.docx/.xls/.xlsx/.ppt/.pptx)、.md、.txt 格式文件
- **THEN** 接受上传

#### Scenario: 不允许的文件类型
- **WHEN** 上传其他格式文件（如 .exe, .pdf, .jpg）
- **THEN** 拒绝上传，提示"不支持的文件类型，仅支持 .doc, .docx, .xls, .xlsx, .ppt, .pptx, .md, .txt"

#### Scenario: 文件大小超限
- **WHEN** 上传超过 20MB 的文件
- **THEN** 拒绝上传，提示"文件大小超过限制（最大 20MB）"

#### Scenario: 项目文件数量超限
- **WHEN** 项目已有 50 个文件，用户尝试上传新文件
- **THEN** 拒绝上传，提示"项目文件数量已达上限（最多 50 个文件）"

#### Scenario: MIME 类型验证
- **WHEN** 上传文件的 MIME 类型与扩展名不匹配
- **THEN** 拒绝上传，提示"文件类型不匹配"

### Requirement: 文件 Hash 计算与去重

系统 SHALL 在上传时计算文件的 SHA256 hash 值，并用于去重检查。

#### Scenario: 计算文件 Hash
- **WHEN** 文件上传成功
- **THEN** 计算 SHA256 hash 并存储到 file_hash 字段

#### Scenario: 同一会话内去重
- **WHEN** 上传的文件 hash 与会话内现有文件相同
- **THEN** 提示"文件已存在"，返回现有文件记录，不重复上传

#### Scenario: 同一项目内去重
- **WHEN** 上传的文件 hash 与项目内现有文件相同
- **THEN** 提示"文件已存在"，返回现有文件记录，不重复上传

#### Scenario: 完整性验证
- **WHEN** 下载文件时
- **THEN** 可选项：验证文件 hash 与记录是否匹配

### Requirement: 文件标签和描述

系统 SHALL 支持用户上传时为文件添加标签和描述。

#### Scenario: 添加文件描述
- **WHEN** 用户上传文件时提供 description（可选）
- **THEN** 存储到 File 表的 description 字段，限制 500 字符

#### Scenario: 添加文件标签
- **WHEN** 用户上传文件时提供 tags（可选）
- **THEN** 存储到 File 表的 tags 字段（JSONB 数组）

#### Scenario: 更新文件描述
- **WHEN** 文件上传者更新文件描述
- **THEN** 系统更新 description 字段

#### Scenario: 更新文件标签
- **WHEN** 文件上传者更新文件标签
- **THEN** 系统更新 tags 字段

### Requirement: 文件存储路径

系统 SHALL 使用统一的存储路径格式。

#### Scenario: 会话文件存储
- **WHEN** 上传会话文件
- **THEN** 存储到 `users/{user_id}/outputs/sessions/{session_id}/{uuid}_{filename}`

#### Scenario: 项目文件存储
- **WHEN** 上传项目文件
- **THEN** 存储到 `users/{owner_id}/outputs/projects/{project_id}/{uuid}_{filename}`

#### Scenario: 历史文件兼容
- **WHEN** 访问历史文件（无 File 记录）
- **THEN** 支持旧路径 `users/{usernumb}/outputs/{session_id}/{filename}`
