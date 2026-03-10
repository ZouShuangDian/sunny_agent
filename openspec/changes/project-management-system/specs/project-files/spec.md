# Project Files Capability

> 项目文件管理规范：支持文件上传、下载、删除，文件类型限制和大小限制

## ADDED Requirements

### Requirement: 项目成员可以上传文件

系统 SHALL 允许项目成员（owner/editor）通过 POST /api/projects/{id}/files 接口上传文件到项目。

#### Scenario: 成功上传文件
- **WHEN** 项目成员上传符合要求的文件（Office/.md/.txt，< 20MB）
- **THEN** 系统返回 201 Created，包含文件信息（id, file_name, file_size, uploaded_at）

#### Scenario: 文件格式不支持
- **WHEN** 上传的文件格式不在允许列表中（如 .exe, .pdf）
- **THEN** 系统返回 400 Bad Request，提示"不支持的文件类型，仅支持 .doc, .docx, .xls, .xlsx, .ppt, .pptx, .md, .txt"

#### Scenario: 文件超过大小限制
- **WHEN** 上传的文件大小超过 20MB
- **THEN** 系统返回 413 Payload Too Large，提示"文件大小超过限制（最大 20MB）"

#### Scenario: 文件名已存在
- **WHEN** 上传的文件名与项目内现有文件同名
- **THEN** 系统自动重命名文件为 {filename}_{uuid}.{ext}，避免冲突

#### Scenario: viewer 角色上传
- **WHEN** viewer 角色用户尝试上传文件
- **THEN** 系统返回 403 Forbidden，提示"权限不足：需要 editor 角色"

### Requirement: 项目成员可以下载文件

系统 SHALL 允许项目成员通过 GET /api/projects/{id}/files/{file_id} 接口下载项目文件。

#### Scenario: 成功下载文件
- **WHEN** 项目成员发送文件下载请求
- **THEN** 系统返回文件内容，Content-Type 正确，Content-Disposition 包含文件名

#### Scenario: 文件不存在
- **WHEN** 下载的 file_id 不存在
- **THEN** 系统返回 404 Not Found

### Requirement: 项目成员可以删除文件

系统 SHALL 允许项目成员（owner/editor）通过 DELETE /api/projects/{id}/files/{file_id} 接口删除项目文件。

#### Scenario: 成功删除文件
- **WHEN** 项目成员（owner/editor）发送删除文件请求
- **THEN** 系统返回 200 OK，文件记录删除，物理文件删除

#### Scenario: viewer 角色删除
- **WHEN** viewer 角色用户尝试删除文件
- **THEN** 系统返回 403 Forbidden

### Requirement: 用户可以查询项目文件列表

系统 SHALL 提供 GET /api/projects/{id}/files 接口，返回项目内所有文件的列表。

#### Scenario: 查询成功
- **WHEN** 项目成员发送 GET /api/projects/{id}/files 请求
- **THEN** 系统返回文件列表，包含 id, file_name, file_size, mime_type, uploaded_by, uploaded_at

### Requirement: 项目文件数量限制

系统 SHALL 限制每个项目最多上传 50 个文件。

#### Scenario: 达到文件数量上限
- **WHEN** 项目已有 50 个文件，用户尝试上传新文件
- **THEN** 系统拒绝上传，提示"项目文件数量已达上限（最多 50 个文件）"

#### Scenario: 未达到文件数量上限
- **WHEN** 项目文件数量少于 50 个，用户尝试上传文件
- **THEN** 系统接受上传（仍需满足其他限制条件）
