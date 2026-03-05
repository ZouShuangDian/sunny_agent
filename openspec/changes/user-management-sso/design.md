## Context

Sunny Agent 当前已有基础的 JWT 鉴权和用户模型，但缺少：
1. 完整的用户管理 CRUD API，无法进行用户生命周期管理
2. OA SSO 单点登录集成，用户需要单独维护账号密码
3. 公司维度的数据隔离，无法满足集团多公司安全合规要求

现状：
- User 表已有基础字段（id, username, usernumb, email, role_id, department）
- 已有 JWT 鉴权机制（access_token + refresh_token + 黑名单）
- 已有 RBAC 角色系统（admin / manager / operator / viewer）
- 已有 Plugin 用户隔离经验（owner_usernumb 字段）

约束：
- SSO 接口由 OA 团队提供，需遵循 CAS 协议
- 现有用户数据需要迁移填充 company 字段
- 不同公司数据必须严格隔离（strict 模式）
- 首次登录的 SSO 用户角色固定为"普通用户"

## Goals / Non-Goals

**Goals:**
- 实现完整的用户管理 CRUD API（管理员操作）
- 集成舜宇 OA SSO 单点登录（ticket 验证 + 用户自动创建）
- 实现公司级数据隔离（基于 ABAC 属性的查询过滤）
- 扩展 User 模型支持 SSO 属性（source, company, phone, avatar_url, sso_last_login）
- 数据迁移脚本填充现有用户的 company 字段

**Non-Goals:**
- 不支持"部门经理"等动态角色（仅静态 RBAC）
- 不支持项目维度的数据权限（仅公司维度）
- 不支持审批流（越权访问直接拒绝）
- 不支持集团管理员跨公司访问（管理员除外）
- 不实现管理界面（纯 API）

## Decisions

### Decision 1: RBAC + ABAC 混合模式

**选择**: RBAC 控制功能权限 + ABAC 控制数据范围

**理由**:
- RBAC 简单直观，适合功能权限管理（如：谁能访问用户管理 API）
- ABAC 灵活，适合数据隔离（如：公司 A 用户只能访问公司 A 数据）
- 舜宇场景不需要复杂的动态角色，RBAC 足够
- 公司隔离是典型的 ABAC 场景（基于用户属性过滤）

**备选方案**:
- 纯 RBAC: 需要为每个公司创建角色（角色爆炸问题）
- 纯 ABAC: 复杂度高，维护成本大

### Decision 2: SSO 首次登录自动创建用户

**选择**: SSO 回调时自动创建用户，角色固定为"普通用户"

**理由**:
- 用户体验好，无需管理员手动创建
- 角色固定为"普通用户"，安全风险可控
- 参考 open-webui 的成功实践

**备选方案**:
- 管理员审核后激活：增加管理成本
- 批量导入 SSO 用户：无法实时同步

### Decision 3: 公司数据完全隔离（strict）

**选择**: 不同公司之间数据完全隔离，管理员除外

**理由**:
- 符合集团安全合规要求
- 实现简单，SQL 注入 WHERE company = ?
- 避免数据泄露风险

**备选方案**:
- shared 模式（只读共享）：增加复杂度，当前不需要

### Decision 4: 数据迁移一次性执行

**选择**: 上线前通过脚本一次性迁移，非定时同步

**理由**:
- 现有用户数量有限（预计 < 1000）
- 一次性执行简单可控
- 迁移前可 dry-run 预览

**备选方案**:
- 定时任务每天同步：增加运维复杂度
- 实时同步：依赖 SSO 推送接口，不可控

## Risks / Trade-offs

**[Risk] SSO 接口不可用**
→ Mitigation: 
  - 申请 SSO 测试环境提前联调
  - 准备降级方案（允许本地密码登录）
  - SSO 验证超时时间设置为 5 秒

**[Risk] 数据迁移失败**
→ Mitigation:
  - 迁移脚本支持 dry-run 模式
  - 分批次执行（每次 1000 用户）
  - 准备回滚脚本（UPDATE users SET company = NULL）

**[Risk] ABAC 过滤器影响性能**
→ Mitigation:
  - 在 company、department 字段建立索引
  - 压测验证性能影响 < 10%
  - 管理员查询跳过过滤器

**[Risk] 越权访问漏洞**
→ Mitigation:
  - 所有查询接口统一应用 CompanyFilter
  - 编写越权访问测试用例
  - 代码审查重点检查 WHERE 条件

**[Trade-off] 增加数据库查询复杂度**
- ABAC 过滤器会增加 SQL 的 WHERE 条件
- 影响：查询性能略有下降（< 10%）
- 收益：数据安全性大幅提升

**[Trade-off] User 表字段增加**
- 新增 5 个字段，表结构变复杂
- 影响：存储开销略有增加
- 收益：支持 SSO 和公司隔离

## Migration Plan

### 阶段 1: 数据模型与迁移（2 天）
1. 创建 Alembic 迁移脚本（xxxx_add_sso_support.py）
2. 执行数据库迁移（测试环境）
3. 预置"普通用户"角色
4. 预置数据隔离策略

### 阶段 2: SSO 集成（3 天）
1. 实现 SSO 登录重定向接口
2. 实现 SSO 回调接口（验证 ticket + 解析 XML）
3. 实现 get_or_create_sso_user 函数
4. SSO 流程测试（测试环境）

### 阶段 3: 用户管理 API（3 天）
1. 实现用户 CRUD 接口
2. 实现角色管理接口
3. 集成 ABAC 过滤器
4. API 测试（Postman 集合）

### 阶段 4: ABAC 数据隔离（2 天）
1. 实现 ABAC 检查器
2. 实现 CompanyFilter 过滤器
3. 应用到 chat_messages、chat_sessions 查询
4. 隔离测试（越权访问测试）

### 阶段 5: 数据迁移与上线（1 天）
1. 执行数据迁移脚本（dry-run → execute）
2. 生产环境部署
3. 上线验证
4. 监控告警配置

### 回滚策略
1. 回滚代码到上一个版本
2. 执行回滚迁移（downgrade）
3. 恢复旧版 User 模型
4. 验证功能正常

## Open Questions

1. **SSO 接口细节**
   - 需要 OA 团队提供 SSO 接口文档
   - 确认 SSO 返回的 XML 字段名（确认 company 和 dept）
   - 确认 SSO 验证接口的 QPS 限制

2. **现有用户数量**
   - 需要统计当前 users 表的用户数量
   - 评估数据迁移耗时

3. **角色权限列表**
   - "普通用户"角色的具体权限列表
   - 是否需要预置其他角色

4. **上线时间**
   - 预计上线时间窗口
   - 是否需要灰度发布
