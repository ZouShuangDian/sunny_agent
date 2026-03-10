# 分布式定时任务调度系统 - 实现任务清单（基于 arq）

## 1. 项目初始化

- [ ] 1.1 安装 arq 及相关依赖（arq>=0.25, croniter>=1.0）
- [ ] 1.2 创建目录结构（scheduler/, services/scheduler/, services/worker/）
- [ ] 1.3 配置环境变量模板（.env.example）

## 2. 数据库模型

- [ ] 2.1 创建 scheduled_tasks 模型（app/scheduler/models.py）
- [ ] 2.2 创建 task_executions 模型（app/scheduler/models.py）
- [ ] 2.3 创建 Alembic 迁移脚本
- [ ] 2.4 运行数据库迁移并验证表结构
- [ ] 2.5 添加数据库索引（next_run_at, is_active）

## 3. 核心组件 - 幂等性控制（仍需自建）

- [ ] 3.1 实现 IdempotencyController 类（app/scheduler/idempotency.py）
- [ ] 3.2 实现 Redis 缓存机制（检查/创建/完成标记）
- [ ] 3.3 实现僵死检测逻辑（> 5 分钟）
- [ ] 3.4 编写幂等性控制器单元测试

## 4. arq Worker 服务

- [ ] 4.1 安装 arq 并配置 Redis 连接（app/scheduler/arq_config.py）
- [ ] 4.2 实现 execute_chat_task 任务函数（app/scheduler/tasks.py）
- [ ] 4.3 实现 Worker 启动/关闭回调（startup/shutdown）
- [ ] 4.4 配置 Worker 参数（max_jobs=10, job_timeout=300, max_tries=3）
- [ ] 4.5 实现指数退避重试逻辑
- [ ] 4.6 创建 Worker 入口文件（services/worker/main.py）
- [ ] 4.7 创建 Worker Dockerfile
- [ ] 4.8 编写 Worker 集成测试（测试任务执行、重试）

## 5. arq Scheduler 服务

- [ ] 5.1 实现 schedule_tasks 函数（每秒扫描数据库）
- [ ] 5.2 配置 arq cron 调度器（second='*/1', run_at_startup=True）
- [ ] 5.3 实现任务入队逻辑（enqueue_job）
- [ ] 5.4 实现下次执行时间计算（croniter）
- [ ] 5.5 实现 Scheduler 启动/关闭回调
- [ ] 5.6 创建 Scheduler 入口文件（services/scheduler/main.py）
- [ ] 5.7 创建 Scheduler Dockerfile
- [ ] 5.8 编写 Scheduler 集成测试（测试 Cron 调度）

## 6. 任务管理服务

- [ ] 6.1 实现 ScheduledTaskService 类（app/scheduler/service.py）
- [ ] 6.2 实现任务 CRUD 操作
- [ ] 6.3 实现 Cron 表达式验证（支持秒级）
- [ ] 6.4 实现任务手动触发（直接 enqueue_job）
- [ ] 6.5 实现任务启用/禁用逻辑
- [ ] 6.6 编写任务管理服务单元测试

## 7. API 路由

- [ ] 7.1 创建 scheduler API 路由文件（app/api/scheduler.py）
- [ ] 7.2 实现 POST /api/scheduler/tasks（创建任务）
- [ ] 7.3 实现 GET /api/scheduler/tasks（查询任务列表）
- [ ] 7.4 实现 GET /api/scheduler/tasks/{id}（获取任务详情）
- [ ] 7.5 实现 PATCH /api/scheduler/tasks/{id}（更新任务）
- [ ] 7.6 实现 DELETE /api/scheduler/tasks/{id}（删除任务）
- [ ] 7.7 实现 POST /api/scheduler/tasks/{id}/trigger（手动触发）
- [ ] 7.8 实现 PATCH /api/scheduler/tasks/{id}/toggle（启用/禁用）
- [ ] 7.9 实现 GET /api/scheduler/tasks/{id}/executions（执行历史）
- [ ] 7.10 在 main.py 中注册 scheduler 路由

## 8. 监控与指标

- [ ] 8.1 实现 arq 内置指标暴露（通过 arq 监控端点）
- [ ] 8.2 实现自定义 Prometheus 指标（task_execution_total, task_execution_duration）
- [ ] 8.3 实现幂等性缓存命中率指标
- [ ] 8.4 创建 Prometheus 告警规则文件
- [ ] 8.5 创建 Grafana Dashboard（包含 arq 内置指标）

## 9. Webhook 通知

- [ ] 9.1 实现 Webhook 发送逻辑（任务最终失败时触发）
- [ ] 9.2 实现 Webhook 重试机制（2 次重试）
- [ ] 9.3 实现 Webhook 签名验证（可选）
- [ ] 9.4 编写 Webhook 单元测试

## 10. Docker Compose 编排

- [ ] 10.1 更新主 docker-compose.yml（添加 scheduler, worker 服务）
- [ ] 10.2 配置服务依赖关系（scheduler/worker 依赖 postgres, redis）
- [ ] 10.3 配置 Worker 水平扩展（replicas: 3）
- [ ] 10.4 配置健康检查端点（使用 arq 内置 health check）
- [ ] 10.5 配置日志收集（json-file driver）

## 11. 配置与文档

- [ ] 11.1 更新 app/config.py（添加 arq 配置）
- [ ] 11.2 更新 .env.example（添加 arq 相关环境变量）
- [ ] 11.3 创建部署文档（docs/deployment.md）
- [ ] 11.4 更新 README.md（添加 arq 架构说明）
- [ ] 11.5 创建 API 文档（使用 OpenAPI/Swagger 注解）

## 12. 测试

- [ ] 12.1 编写数据库模型单元测试
- [ ] 12.2 编写幂等性控制器单元测试
- [ ] 12.3 编写 arq Worker 集成测试
- [ ] 12.4 编写 arq Scheduler 集成测试
- [ ] 12.5 编写 API 端点测试（pytest + TestClient）
- [ ] 12.6 编写端到端测试（创建任务 -> 调度 -> 执行 -> 验证）
- [ ] 12.7 测试高频任务（10秒间隔）精度
- [ ] 12.8 测试故障场景（Worker 崩溃、任务重试）

## 13. 性能测试

- [ ] 13.1 测试 1000 个任务的调度性能
- [ ] 13.2 测试 10 秒间隔高频任务精度
- [ ] 13.3 测试 Worker 水平扩展能力
- [ ] 13.4 分析并优化性能瓶颈

## 14. 部署与发布

- [ ] 14.1 在测试环境部署完整架构
- [ ] 14.2 验证所有组件正常运行
- [ ] 14.3 执行数据库迁移（生产环境）
- [ ] 14.4 灰度发布（先部署 1 个 Worker）
- [ ] 14.5 全量发布（部署 Scheduler + 3 Worker）
- [ ] 14.6 验证监控和告警正常工作
- [ ] 14.7 编写运维手册（故障排查、扩缩容）

## 15. 清理与归档

- [ ] 15.1 删除自建的时间轮、分布式锁等废弃代码
- [ ] 15.2 归档变更文档
- [ ] 15.3 更新版本号（CHANGELOG.md）
- [ ] 15.4 创建发布 Tag

---

**进度追踪:**
- 总任务数: 15 个阶段，约 70 个具体任务
- 相比自建方案减少了 ~15 个任务（无需 Redlock、时间轮等）
- 关键路径: 2 → 3 → 4 → 5 → 7 → 12（最小可用版本）
- **arq 优势**: 大幅简化架构，代码量减少 ~80%
