## 1. 数据模型与数据库迁移

- [ ] 1.1 创建 Alembic 迁移脚本（xxxx_add_sso_support.py）
- [ ] 1.2 扩展 User 模型：新增 source, company, phone, avatar_url, sso_last_login 字段
- [ ] 1.3 创建 DataScopePolicy 模型（data_scope_policies 表）
- [ ] 1.4 在 roles 表中预置"普通用户"角色
- [ ] 1.5 在 data_scope_policies 表预置数据隔离策略
- [ ] 1.6 执行数据库迁移（测试环境）
- [ ] 1.7 验证迁移结果（检查字段、索引、预置数据）

## 2. SSO 集成实现

- [ ] 2.1 创建 app/api/auth.py（SSO 认证接口）
- [ ] 2.2 实现 GET /api/auth/sso/login（重定向到 SSO 登录页）
- [ ] 2.3 实现 GET /api/auth/sso/callback（验证 ticket + 解析 XML）
- [ ] 2.4 创建 app/services/user_sync.py（用户同步服务）
- [ ] 2.5 实现 get_or_create_sso_user 函数（首次登录自动创建）
- [ ] 2.6 扩展 JWT 签发：在 Token 中添加 company 属性
- [ ] 2.7 实现 SSO 登出接口（POST /api/auth/logout）
- [ ] 2.8 测试 SSO 完整流程（登录 → 回调 → 创建用户 → 签发 Token）

## 3. 用户管理 API 实现

- [ ] 3.1 创建 app/api/users.py（用户管理接口）
- [ ] 3.2 实现 POST /api/users（创建用户）
- [ ] 3.3 实现 GET /api/users（用户列表，支持分页 + 筛选）
- [ ] 3.4 实现 GET /api/users/{id}（用户详情）
- [ ] 3.5 实现 PUT /api/users/{id}（更新用户）
- [ ] 3.6 实现 DELETE /api/users/{id}（软删除用户）
- [ ] 3.7 实现 POST /api/users/bulk-import（批量导入）
- [ ] 3.8 实现 GET /api/users/me（当前用户信息）
- [ ] 3.9 实现 PUT /api/users/me（更新个人信息）
- [ ] 3.10 创建 app/api/roles.py（角色管理接口）
- [ ] 3.11 实现角色 CRUD 接口
- [ ] 3.12 实现 PUT /api/roles/{id}/permissions（更新角色权限）
- [ ] 3.13 编写 API 测试用例（Postman 集合或 pytest）

## 4. ABAC 数据隔离实现

- [ ] 4.1 创建 app/security/abac.py（ABAC 权限检查器）
- [ ] 4.2 实现 check_company_isolation 函数
- [ ] 4.3 实现 check_data_access 函数
- [ ] 4.4 创建 app/db/filters.py（数据过滤器）
- [ ] 4.5 实现 CompanyFilter.apply 方法（自动注入公司过滤）
- [ ] 4.6 在 chat_messages 查询中应用 CompanyFilter
- [ ] 4.7 在 chat_sessions 查询中应用 CompanyFilter
- [ ] 4.8 在 files 查询中应用 CompanyFilter
- [ ] 4.9 编写越权访问测试用例
- [ ] 4.10 性能测试（验证 ABAC 过滤器性能影响 < 10%）

## 5. 数据迁移脚本

- [ ] 5.1 创建 scripts/migrate_user_company.py 脚本
- [ ] 5.2 实现 dry-run 模式（预览迁移结果）
- [ ] 5.3 实现 execute 模式（实际执行迁移）
- [ ] 5.4 实现回滚逻辑（迁移失败时回滚）
- [ ] 5.5 测试迁移脚本（测试环境）
- [ ] 5.6 编写迁移操作手册

## 6. 配置与文档

- [ ] 6.1 更新 app/.env.example（新增 SSO 配置项）
- [ ] 6.2 更新 app/config.py（新增 SSO 配置类）
- [ ] 6.3 编写 SSO 配置指南（README 或独立文档）
- [ ] 6.4 更新 README（用户管理章节）
- [ ] 6.5 编写数据迁移操作手册
- [ ] 6.6 编写 API 使用文档（OpenAPI/Swagger）

## 7. 测试与验证

- [ ] 7.1 单元测试覆盖率达到 80%
- [ ] 7.2 集成测试（SSO + 用户管理 + 数据隔离）
- [ ] 7.3 安全测试（越权访问、SQL 注入、JWT 伪造）
- [ ] 7.4 性能测试（SSO 登录响应 < 500ms）
- [ ] 7.5 压测（ABAC 过滤器性能影响 < 10%）
- [ ] 7.6 用户验收测试（UAT 环境验证）

## 8. 上线部署

- [ ] 8.1 生产环境数据库迁移
- [ ] 8.2 执行数据迁移脚本（dry-run → execute）
- [ ] 8.3 部署代码到生产环境
- [ ] 8.4 验证 SSO 登录流程
- [ ] 8.5 验证用户管理功能
- [ ] 8.6 验证数据隔离功能
- [ ] 8.7 配置监控告警（SSO 失败率、越权访问尝试）
- [ ] 8.8 编写上线报告
