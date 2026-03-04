## Why

当前 Sunny Agent 缺少完整的用户管理体系和 OA 单点登录集成，无法支持企业级多公司数据隔离需求。需要补充用户管理 CRUD API、集成舜宇 OA SSO 单点登录、实现基于公司属性的数据隔离，以满足集团制造业场景下的多公司安全隔离要求。

## What Changes

- **新增用户管理 REST API**：支持管理员对用户进行增删改查、批量导入等操作
- **新增角色管理 REST API**：支持角色的创建、权限配置和管理
- **集成 OA SSO 单点登录**：用户首次登录自动创建账户，角色默认为"普通用户"
- **实现公司级数据隔离**：不同公司之间的聊天数据完全隔离（strict 模式）
- **扩展 User 数据模型**：新增 source、company、phone、avatar_url、sso_last_login 字段
- **新增数据迁移脚本**：从 SSO 同步公司属性到现有用户（上线前一次性执行）
- **预置"普通用户"角色**：SSO 用户首次登录时的默认角色

## Capabilities

### New Capabilities
- `user-management`: 用户 CRUD 操作、批量导入、用户列表筛选（按公司/部门/来源）
- `role-management`: 角色 CRUD 操作、角色权限配置
- `sso-integration`: OA SSO 单点登录、ticket 验证、用户属性解析、自动创建用户
- `company-isolation`: 基于公司属性的数据隔离、ABAC 过滤器、跨公司访问控制
- `user-sync`: SSO 用户信息同步、首次登录自动创建、登录时间更新

### Modified Capabilities
- `security`: 扩展 JWT 鉴权，新增 company 属性到 Token 载荷，支持 SSO 登出

## Impact

**受影响代码**:
- `app/db/models/user.py` - User 模型扩展
- `app/security/auth.py` - JWT Token 签发扩展
- `app/api/chat.py` - 应用公司数据隔离过滤器

**新增文件**:
- `app/api/auth.py` - SSO 认证接口
- `app/api/users.py` - 用户管理接口
- `app/db/models/data_scope.py` - 数据隔离策略模型
- `app/security/abac.py` - ABAC 权限检查器
- `app/db/filters.py` - 数据过滤器
- `app/services/user_sync.py` - 用户同步服务
- `scripts/migrate_user_company.py` - 数据迁移脚本

**数据库变更**:
- `users` 表新增 5 个字段（source, company, phone, avatar_url, sso_last_login）
- 新增 `data_scope_policies` 表（公司数据隔离策略）
- 预置"普通用户"角色
- 预置数据隔离策略（舜宇光学科技、舜宇精机等）

**配置变更**:
- `.env` 新增 SSO 相关配置（SSO_VALIDATE_URL, SSO_LOGIN_URL, 属性映射等）
- 新增数据隔离开关（DATA_ISOLATION_ENABLED, DEFAULT_ISOLATION_LEVEL）

**依赖**:
- 需要 OA 团队提供 SSO 接口文档和测试环境
- 需要 httpx 库用于异步 HTTP 请求（已在依赖中）
