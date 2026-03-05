# SSO Integration Capability

> OA SSO 单点登录集成规范：支持舜宇 OA CAS 协议认证、首次登录自动创建用户

## ADDED Requirements

### Requirement: 用户可以重定向到 SSO 登录页

系统 SHALL 提供 GET /api/auth/sso/login 接口，重定向用户到舜宇 OA SSO 登录页面。

#### Scenario: 成功重定向
- **WHEN** 用户访问 GET /api/auth/sso/login
- **THEN** 系统返回 302 Redirect，Location 指向 https://sso.sunnyoptical.cn/login?service={callback_url}

#### Scenario: 携带 model 参数
- **WHEN** 用户访问 GET /api/auth/sso/login?model=xxx
- **THEN** 系统重定向时 service 参数包含 model 信息

### Requirement: 系统验证 SSO ticket

系统 SHALL 在 GET /api/auth/sso/callback 接口验证 OA SSO 返回的 ticket，解析用户属性。

#### Scenario: ticket 验证成功
- **WHEN** SSO 回调携带有效的 ticket 参数
- **THEN** 系统向 https://sso.sunnyoptical.cn/serviceValidate 发送验证请求，成功解析 XML 响应

#### Scenario: ticket 无效
- **WHEN** ticket 参数无效或已过期
- **THEN** 系统返回 401 Unauthorized，提示"身份验证失败"

#### Scenario: 解析用户属性
- **WHEN** SSO 验证成功，XML 包含 user, name, email, dept, company 属性
- **THEN** 系统正确解析所有属性，用于后续用户创建/更新

### Requirement: 首次登录自动创建用户

系统 SHALL 在 SSO 首次登录时自动创建用户账户，角色默认为"普通用户"。

#### Scenario: 首次登录创建用户
- **WHEN** SSO 登录的 usernumb 在系统中不存在
- **THEN** 系统创建新用户，source="sso"，role="普通用户"，填充 company、dept、email 等属性

#### Scenario: 非首次登录更新信息
- **WHEN** SSO 登录的 usernumb 已存在，但部门或公司信息变更
- **THEN** 系统更新用户的 department、company、email 等信息，更新 sso_last_login 时间

#### Scenario: "普通用户"角色不存在
- **WHEN** 首次登录时 roles 表中没有"普通用户"角色
- **THEN** 系统返回 500 Internal Server Error，提示"角色'普通用户'不存在"

### Requirement: 签发包含公司属性的 JWT

系统 SHALL 在 SSO 登录成功后签发 JWT Token，Token 载荷包含 company 属性用于数据隔离。

#### Scenario: 签发 access_token
- **WHEN** SSO 登录成功
- **THEN** 系统签发 access_token，载荷包含 sub, usernumb, role, company, department, permissions

#### Scenario: Token 包含公司属性
- **WHEN** 解析 access_token
- **THEN** Token 载荷中包含 company 字段，值为 SSO 返回的公司名

### Requirement: 用户可以 SSO 登出

系统 SHALL 提供 POST /api/auth/logout 接口，支持 SSO 用户登出。

#### Scenario: 本地登出
- **WHEN** 用户发送 POST /api/auth/logout 请求
- **THEN** 系统将 JWT Token 加入黑名单，返回 200 OK

#### Scenario: SSO 登出
- **WHEN** SSO 用户登出
- **THEN** 系统可选择重定向到 SSO 登出页（可选）
