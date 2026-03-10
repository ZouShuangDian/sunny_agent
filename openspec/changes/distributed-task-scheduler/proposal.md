# 分布式定时任务调度系统（基于 arq）

## Why

当前系统缺少定时任务调度能力，无法满足用户定时调用对话接口的需求。随着任务量增长到几千甚至上万个，需要高可靠、可水平扩展的分布式调度架构，确保任务不丢失、不重复执行，并支持多实例部署和故障自动恢复。

**技术选型理由**:
选择使用 **arq** 框架替代自建调度器和 Worker，因为 arq 提供了：
- 内置 Cron 任务调度（支持秒级精度）
- 自动延迟队列管理（Redis Sorted Set）
- 自动重试机制（指数退避）
- 优雅关闭和健康检查
- 大幅减少自定义代码（从 ~3000 行降至 ~500 行）
- 经过生产验证的可靠性

## What Changes

- **任务管理模块**：提供完整的任务 CRUD API，支持 cron 表达式、时区、重试策略配置
- **分布式调度服务（Scheduler）**：基于 arq 框架，使用内置 cron 调度，支持秒级精度
- **任务执行服务（Worker）**：基于 arq Worker，自动消费、重试、并发控制，无需自建队列
- **Outbox 模式**：使用事务外盒保证 PG 和 Redis 的一致性，防止 orphan execution
- **幂等性控制**：使用 Redis SET NX 原子操作，防止多 Worker 竞争执行
- **并发控制**：使用 FOR UPDATE SKIP LOCKED 行级锁，防止 Scheduler 重复调度
- **监控告警**：Prometheus 指标 + Grafana Dashboard

**BREAKING**: 新增数据库表 `scheduled_tasks`、`task_executions` 和 `scheduler_outbox`，需要执行迁移

## Capabilities

### New Capabilities
- `scheduled-task-management`: 定时任务的创建、查询、更新、删除和手动触发
- `distributed-scheduler`: 基于 arq 的分布式调度器，支持 Cron 和延迟任务
- `task-worker`: 基于 arq Worker 的任务执行服务
- `task-execution-tracking`: 任务执行历史记录和状态追踪
- `task-monitoring`: 任务调度系统的监控和告警

### Modified Capabilities
- 无

## Impact

### 数据库
- 新增 `scheduled_tasks` 表：存储定时任务定义
- 新增 `task_executions` 表：存储任务执行历史
- 使用 Alembic 执行迁移

### 基础设施
- Redis 单实例（arq 使用）：任务队列和延迟队列
- 新增 Scheduler 服务（可多实例部署）：基于 arq cron 的调度器，使用行级锁防重复
- 新增 Worker 服务（可扩展）：基于 arq Worker，使用原子幂等性控制
- Outbox 处理器（可与 Scheduler 同实例或独立部署）：可靠发送消息到队列

### API
- 新增 `/api/scheduler/*` 路由：任务管理接口
- 新增监控端点：暴露 Prometheus 指标

### 依赖
- `arq` (>=0.25): 异步任务队列和调度（核心框架）
- `croniter` (>=1.0): Cron 表达式解析
- `prometheus-client`: 监控指标

### 部署
- Docker Compose 新增服务：`scheduler`, `worker`
- 简化部署：无需 Redis Sentinel（arq 自动管理连接）
- 新增环境变量配置
