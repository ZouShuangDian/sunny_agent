# User Sync Capability

> SSO 用户同步规范：首次登录自动创建、登录信息更新、公司属性同步

## ADDED Requirements

### Requirement: SSO 登录时同步用户信息

系统 SHALL 在每次 SSO 登录时同步用户的部门、公司、邮箱等信息。

#### Scenario: 部门信息变更
- **WHEN** 用户部门从"技术部"变更为"技术创新中心"
- **THEN** 下次 SSO 登录时，系统自动更新用户的 department 字段

#### Scenario: 公司信息变更
- **WHEN** 用户公司从"舜宇光学科技"变更为"舜宇精机"
- **THEN** 下次 SSO 登录时，系统自动更新用户的 company 字段

### Requirement: 记录 SSO 登录时间

系统 SHALL 在每次 SSO 登录时更新 sso_last_login 字段。

#### Scenario: 首次 SSO 登录
- **WHEN** 用户首次通过 SSO 登录
- **THEN** 系统设置 sso_last_login 为当前时间

#### Scenario: 再次 SSO 登录
- **WHEN** 用户再次通过 SSO 登录
- **THEN** 系统更新 sso_last_login 为当前时间

### Requirement: 数据迁移脚本

系统 SHALL 提供数据迁移脚本，从 SSO 同步公司属性到现有用户。

#### Scenario: dry-run 模式
- **WHEN** 执行 python scripts/migrate_user_company.py --dry-run
- **THEN** 脚本输出将要更新的用户列表，但不实际修改数据

#### Scenario: execute 模式
- **WHEN** 执行 python scripts/migrate_user_company.py --execute
- **THEN** 脚本批量更新所有用户的 company 字段，输出迁移日志

#### Scenario: 迁移失败回滚
- **WHEN** 数据迁移过程中断
- **THEN** 支持回滚到迁移前状态（UPDATE users SET company = NULL WHERE source != 'sso'）
