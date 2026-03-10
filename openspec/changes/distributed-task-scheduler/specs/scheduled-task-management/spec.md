# Capability: 定时任务管理

## 需求

为用户提供完整的定时任务生命周期管理能力，支持灵活的任务配置和直观的操作界面。

### 功能性需求

#### REQ-1: 任务创建
- 用户可以通过 API 创建定时任务
- 任务配置项包括：
  - 任务名称和描述
  - Cron 表达式（支持标准 cron 语法）
  - 时区设置（默认 Asia/Shanghai）
  - 任务参数（JSON 格式，传递给 Chat API）
  - 重试策略（重试次数、重试间隔）
  - Webhook URL（失败通知）
- 系统自动计算并设置 next_run_at

#### REQ-2: 任务查询
- 支持分页查询任务列表
- 支持按状态、类型、创建者筛选
- 支持按创建时间排序
- 返回任务执行统计（成功次数、失败次数）

#### REQ-3: 任务更新
- 支持修改任务配置（Cron、参数、重试策略等）
- 修改后重新计算 next_run_at
- 不中断正在执行的任务实例

#### REQ-4: 任务删除
- 支持逻辑删除（保留历史记录）
- 删除时取消所有待执行的实例
- 已执行完成的任务实例保留

#### REQ-5: 手动触发
- 支持立即执行一次任务
- 手动触发的任务也记录在 execution_history
- 不影响原有的定时调度

#### REQ-6: 任务启用/禁用
- 支持暂停任务（is_active=false）
- 暂停期间不生成新的执行实例
- 恢复后根据当前时间计算 next_run_at

### 非功能性需求

#### REQ-7: 数据验证
- Cron 表达式必须有效
- 时区必须在 IANA 时区数据库中
- 任务名称不能为空且唯一（同一用户内）
- Webhook URL 格式验证

#### REQ-8: 权限控制
- 用户只能管理自己创建的任务
- 管理员可以查看所有任务
- 删除操作需要二次确认

### API 端点

```
POST   /api/scheduler/tasks              # 创建任务
GET    /api/scheduler/tasks              # 查询任务列表
GET    /api/scheduler/tasks/{id}         # 获取任务详情
PATCH  /api/scheduler/tasks/{id}         # 更新任务
DELETE /api/scheduler/tasks/{id}         # 删除任务
POST   /api/scheduler/tasks/{id}/trigger # 手动触发
PATCH  /api/scheduler/tasks/{id}/toggle  # 启用/禁用
```

### 数据模型

```sql
scheduled_tasks:
  - id: UUID PK
  - name: VARCHAR(255) NOT NULL
  - description: TEXT
  - cron_expression: VARCHAR(100) NOT NULL
  - timezone: VARCHAR(50) DEFAULT 'Asia/Shanghai'
  - parameters: JSONB NOT NULL DEFAULT '{}'
  - retry_limit: INT DEFAULT 3
  - retry_delays: JSONB DEFAULT '[0, 60, 300]'
  - webhook_url: VARCHAR(500)
  - is_active: BOOLEAN DEFAULT true
  - next_run_at: TIMESTAMP
  - created_by: UUID FK
  - created_at: TIMESTAMP
  - updated_at: TIMESTAMP
  - scheduler_token: BIGINT  -- Fencing Token
  - scheduler_id: VARCHAR(100)
```
