# User Management Capability

> 用户管理功能规范：支持管理员对用户进行 CRUD 操作、批量导入、列表筛选

## ADDED Requirements

### Requirement: 管理员可以创建用户

系统 SHALL 允许管理员通过 POST /api/users 接口创建新用户，创建时需验证必填字段（usernumb, username, role_id），并确保 usernumb 唯一性。

#### Scenario: 成功创建用户
- **WHEN** 管理员发送 POST /api/users 请求，包含有效的 usernumb、username、role_id
- **THEN** 系统返回 201 Created，包含新创建的用户信息

#### Scenario: 工号重复
- **WHEN** 管理员尝试创建 usernumb 已存在的用户
- **THEN** 系统返回 400 Bad Request，错误信息提示"工号已存在"

#### Scenario: 缺少必填字段
- **WHEN** 创建用户请求缺少 usernumb 或 username
- **THEN** 系统返回 422 Validation Error，列出缺失的字段

### Requirement: 管理员可以查询用户列表

系统 SHALL 提供 GET /api/users 接口，支持分页查询和按公司、部门、来源筛选。普通用户只能看到自己公司的用户，管理员可以看到所有用户。

#### Scenario: 管理员查询所有用户
- **WHEN** 管理员发送 GET /api/users 请求
- **THEN** 系统返回所有用户的分页列表

#### Scenario: 普通用户查询用户列表
- **WHEN** 普通用户（company="舜宇光学科技"）发送 GET /api/users 请求
- **THEN** 系统只返回 company="舜宇光学科技" 的用户列表

#### Scenario: 按部门筛选
- **WHEN** 管理员发送 GET /api/users?department=技术创新中心 请求
- **THEN** 系统返回该部门下的用户列表

### Requirement: 管理员可以查询用户详情

系统 SHALL 提供 GET /api/users/{id} 接口，返回指定用户的完整信息。

#### Scenario: 查询成功
- **WHEN** 管理员发送 GET /api/users/{user_id} 请求
- **THEN** 系统返回该用户的详细信息

#### Scenario: 用户不存在
- **WHEN** 查询的 user_id 不存在
- **THEN** 系统返回 404 Not Found

### Requirement: 管理员可以更新用户信息

系统 SHALL 允许管理员通过 PUT /api/users/{id} 接口更新用户信息（部门、角色、公司等）。

#### Scenario: 成功更新
- **WHEN** 管理员发送 PUT /api/users/{id} 请求，包含有效的更新字段
- **THEN** 系统返回 200 OK，包含更新后的用户信息

#### Scenario: 跨公司修改（普通用户）
- **WHEN** 普通用户尝试修改其他公司的用户
- **THEN** 系统返回 403 Forbidden

### Requirement: 管理员可以删除用户

系统 SHALL 允许管理员通过 DELETE /api/users/{id} 接口软删除用户（设置 is_active=false）。

#### Scenario: 成功删除
- **WHEN** 管理员发送 DELETE /api/users/{id} 请求
- **THEN** 系统返回 200 OK，用户 is_active 设置为 false

#### Scenario: 删除自己
- **WHEN** 管理员尝试删除自己的账户
- **THEN** 系统返回 400 Bad Request，提示"不能删除自己的账户"

### Requirement: 管理员可以批量导入用户

系统 SHALL 提供 POST /api/users/bulk-import 接口，支持 CSV/Excel 格式批量导入用户。

#### Scenario: 成功导入
- **WHEN** 管理员上传格式正确的 CSV 文件，包含 usernumb, username, department, company
- **THEN** 系统批量创建用户，返回导入结果（成功数、失败数、失败原因）

#### Scenario: 部分导入失败
- **WHEN** CSV 中包含重复的 usernumb
- **THEN** 系统跳过重复用户，导入其他用户，返回失败记录及原因
