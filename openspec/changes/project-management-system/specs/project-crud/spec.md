# Project CRUD Capability

> 项目 CRUD 操作规范：支持项目的创建、查询、更新、删除

## ADDED Requirements

### Requirement: 管理员可以创建项目

系统 SHALL 允许管理员通过 POST /api/projects 接口创建新项目，创建时需指定项目名称和描述。

#### Scenario: 成功创建项目
- **WHEN** 管理员发送 POST /api/projects 请求，包含有效的 name 和 description
- **THEN** 系统返回 201 Created，包含新创建的项目信息，owner_id 为当前用户 ID

#### Scenario: 缺少必填字段
- **WHEN** 创建项目请求缺少 name 字段
- **THEN** 系统返回 422 Validation Error，列出缺失的字段

### Requirement: 用户可以查询项目列表

系统 SHALL 提供 GET /api/projects 接口，支持分页查询和按公司筛选。普通用户只能看到自己的项目和被共享的项目，管理员可以看到所有项目。

#### Scenario: 管理员查询所有项目
- **WHEN** 管理员发送 GET /api/projects 请求
- **THEN** 系统返回所有项目的分页列表

#### Scenario: 普通用户查询自己的项目
- **WHEN** 普通用户发送 GET /api/projects 请求
- **THEN** 系统只返回该用户创建的项目和被共享给该用户的项目

#### Scenario: 按公司筛选
- **WHEN** 用户发送 GET /api/projects?company=舜宇光学科技 请求
- **THEN** 系统返回该公司下的项目列表（受公司隔离限制）

### Requirement: 用户可以查询项目详情

系统 SHALL 提供 GET /api/projects/{id} 接口，返回指定项目的详细信息。

#### Scenario: 查询成功
- **WHEN** 用户发送 GET /api/projects/{project_id} 请求，且有权访问该项目
- **THEN** 系统返回该项目的详细信息

#### Scenario: 无权访问
- **WHEN** 用户查询不属于自己且未共享给自己的项目
- **THEN** 系统返回 403 Forbidden

#### Scenario: 项目不存在
- **WHEN** 查询的 project_id 不存在
- **THEN** 系统返回 404 Not Found

### Requirement: 项目 owner 可以更新项目信息

系统 SHALL 允许项目 owner 通过 PUT /api/projects/{id} 接口更新项目信息（名称、描述）。

#### Scenario: 成功更新
- **WHEN** 项目 owner 发送 PUT /api/projects/{id} 请求，包含有效的更新字段
- **THEN** 系统返回 200 OK，包含更新后的项目信息

#### Scenario: 非 owner 更新
- **WHEN** 非 owner 用户尝试更新项目
- **THEN** 系统返回 403 Forbidden

### Requirement: 项目 owner 可以删除项目

系统 SHALL 允许项目 owner 通过 DELETE /api/projects/{id} 接口删除项目，级联删除项目成员和文件。

#### Scenario: 成功删除
- **WHEN** 项目 owner 发送 DELETE /api/projects/{id} 请求
- **THEN** 系统返回 200 OK，项目、成员记录、文件记录被删除，文件物理删除

#### Scenario: 删除后对话关联
- **WHEN** 项目被删除
- **THEN** 原项目内的对话的 project_id 设置为 NULL（对话保留）

#### Scenario: 非 owner 删除
- **WHEN** 非 owner 用户尝试删除项目
- **THEN** 系统返回 403 Forbidden
