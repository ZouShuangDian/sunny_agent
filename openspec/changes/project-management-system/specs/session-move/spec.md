# Session Move Capability

> 对话移动管理规范：支持将对话移动到项目、项目内对话列表

## ADDED Requirements

### Requirement: 用户可以将对话移动到项目

系统 SHALL 允许用户通过 POST /api/sessions/{id}/move 接口将对话移动到项目。

#### Scenario: 成功移动对话
- **WHEN** 对话所有者发送 POST /api/sessions/{id}/move 请求，包含有效的 project_id
- **THEN** 系统返回 200 OK，对话的 project_id 更新为指定项目 ID

#### Scenario: 项目不存在
- **WHEN** 移动到的 project_id 不存在
- **THEN** 系统返回 404 Not Found

#### Scenario: 无权访问项目
- **WHEN** 用户尝试将对话移动到无权访问的项目
- **THEN** 系统返回 403 Forbidden

#### Scenario: 对话已属于其他项目
- **WHEN** 移动的对话已属于另一个项目
- **THEN** 系统更新 project_id，对话从原项目移除，加入新项目

### Requirement: 用户可以查询项目内对话列表

系统 SHALL 提供 GET /api/projects/{id}/sessions 接口，返回项目内的所有对话列表。

#### Scenario: 查询成功
- **WHEN** 项目成员发送 GET /api/projects/{id}/sessions 请求
- **THEN** 系统返回项目内的对话列表，包含 id, session_id, title, created_at, last_active_at

#### Scenario: 分页查询
- **WHEN** 用户发送 GET /api/projects/{id}/sessions?page=1&page_size=20 请求
- **THEN** 系统返回分页的对话列表

### Requirement: 用户可以查询项目内对话关联的文件

系统 SHALL 提供 GET /api/projects/{id}/sessions/{session_id}/files 接口，返回项目内指定对话的所有文件。

#### Scenario: 查询成功
- **WHEN** 项目成员发送 GET /api/projects/{id}/sessions/{session_id}/files 请求
- **THEN** 系统返回该对话上传的文件列表

### Requirement: 用户可以查看项目聚合文件

系统 SHALL 提供 GET /api/projects/{id}/files/all 接口，返回项目内所有对话上传的文件聚合列表。

#### Scenario: 查询成功
- **WHEN** 项目成员发送 GET /api/projects/{id}/files/all 请求
- **THEN** 系统返回项目内所有对话的文件聚合列表，包含 session_id 和文件信息
