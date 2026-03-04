# Company Isolation Capability

> 公司数据隔离规范：基于 ABAC 属性的公司级数据隔离，不同公司数据完全隔离

## ADDED Requirements

### Requirement: 查询聊天消息时自动过滤公司

系统 SHALL 在查询 chat_messages 表时自动注入 WHERE company = ? 条件，普通用户只能看到自己公司的消息。

#### Scenario: 普通用户查询聊天消息
- **WHEN** 普通用户（company="舜宇光学科技"）查询聊天消息
- **THEN** 系统自动注入 WHERE company = '舜宇光学科技' 条件，只返回该公司消息

#### Scenario: 管理员查询聊天消息
- **WHEN** 管理员（permissions 包含"admin"）查询聊天消息
- **THEN** 系统不注入公司过滤条件，返回所有公司消息

#### Scenario: 跨公司访问尝试
- **WHEN** 用户 A（舜宇光学科技）尝试通过修改参数访问公司 B（舜宇精机）的消息
- **THEN** 系统返回 403 Forbidden 或空列表（SQL 层面过滤）

### Requirement: 查询聊天会话时自动过滤公司

系统 SHALL 在查询 chat_sessions 表时自动注入公司过滤条件。

#### Scenario: 用户查询自己的会话列表
- **WHEN** 用户查询聊天会话列表
- **THEN** 系统返回该用户创建或参与的所有会话（已通过用户 ID 过滤）

#### Scenario: 管理员查询所有会话
- **WHEN** 管理员查询聊天会话列表
- **THEN** 系统返回所有用户的会话，无公司限制

### Requirement: 数据隔离策略可配置

系统 SHALL 通过 data_scope_policies 表配置不同公司的隔离级别。

#### Scenario: 查询隔离策略
- **WHEN** 系统执行数据访问检查
- **THEN** 系统查询 data_scope_policies 表，获取该公司的隔离级别

#### Scenario: strict 隔离级别
- **WHEN** 公司 A 的 isolation_level="strict"
- **THEN** 只允许 company = 公司 A 的用户访问该公司数据

### Requirement: 文件上传继承公司属性

系统 SHALL 在用户上传文件时自动继承用户的 company 属性。

#### Scenario: 用户上传文件
- **WHEN** 用户上传文件到系统
- **THEN** 文件记录自动填充 company 字段，值为用户的 company

#### Scenario: 跨公司下载文件
- **WHEN** 用户 A 尝试下载公司 B 的文件
- **THEN** 系统返回 403 Forbidden（文件查询时应用公司过滤）
