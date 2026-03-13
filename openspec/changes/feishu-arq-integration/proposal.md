## Why

当前系统仅支持 Web 聊天界面，飞书用户无法通过企业微信/飞书机器人与 AI 助手交互。企业用户希望直接在飞书内完成问答、定时任务创建等操作，无需切换平台。同时需要支持异步处理架构，解耦消息接收与 AI 生成，提升系统吞吐量和稳定性。

## What Changes

### 新增功能

1. **飞书 Webhook 接收服务** (`feishu-webhook`)
   - 接收飞书开放平台事件推送（消息、@提及等）
   - 消息签名验证与解密
   - 推送到 Redis 队列供 Worker 消费

2. **ARQ Worker 独立消息处理**
   - **独立队列架构**：飞书消息使用独立的 ARQ 队列 `arq:feishu:queue`，与定时任务队列完全分离
   - **独立 Worker 进程**：部署独立的 Feishu Worker 专门处理飞书消息，实现资源隔离和独立扩缩容
   - **消息转移机制**：`feishu-webhook` → Redis List → `message_transfer_loop` (BRPOPLPUSH 长驻任务) → ARQ Queue (`arq:feishu:queue`) → `process_feishu_message` ARQ Task
   - **双阶段 Inbound Debounce**（借鉴 CoPaw）：在 ARQ Task 中实现
     - Time Debounce：时间防抖（默认 2 秒），定时器重置策略
     - No-Text Debounce：无文本消息缓冲（默认 3 秒），等待文字说明
   - **should_debounce 钩子**（借鉴 OpenClaw）：可配置回调，精细控制是否 debounce
   - 消息合并：连续多条消息合并为单一请求，双换行分隔
   - **独立监控**：利用 ARQ 内置的监控、重试、超时机制，独立追踪飞书消息处理

3. **访问控制与身份验证**
   - 基于 PostgreSQL 配置表的访问策略（dm_policy/group_policy）
   - 白名单机制（employee_no/chat_id）
   - 飞书用户 open_id → employee_no → 系统用户映射
   - Token 缓存（Redis, TTL 7000s）

4. **BlockStreaming 回复策略**（借鉴 OpenClaw + 增强）
   - 累积策略：`min_chars` (800) + `idle_ms` (1000ms) + `max_chars` (1200)
   - **段落感知刷新**：检测到段落边界（双换行、列表项、代码块等）时立即 flush
   - 流式卡片实时更新（50ms/次，打字机效果）
   - 超长文本分块（>2000字符，第一块流式卡片，后续块普通消息）

5. **人机延迟（Human-like Delay）**
   - AI 生成完成后增加随机延迟，模拟人类打字节奏
   - 默认延迟范围：500-1500ms（可配置）
   - 支持应用级、群组级、用户级细粒度配置
   - 可禁用，适合对延迟敏感的场景

6. **媒体文件处理**
   - 下载飞书媒体文件（图片、文件、音频）
   - 30MB 大小限制，流式读取
   - 长期保存到本地存储（`uploads/feishu_media/{user_id}/`）
   - 创建 `feishu_media_files` 表记录元数据

7. **审计与监控**
   - `feishu_message_logs` 表记录完整消息链路
   - 状态追踪：received → buffering → processing → completed
   - 错误处理与降级策略

### 数据库变更

- 新建 `feishu_access_config`：访问控制配置
- 新建 `feishu_group_config`：群组特定配置
- 新建 `feishu_user_bindings`：用户绑定关系
- 新建 `feishu_media_files`：媒体文件元数据
- 新建 `feishu_message_logs`：消息审计日志

### API 新增

- `POST /webhook`：飞书事件接收端点
- `GET /health`：服务健康检查

## Capabilities

### New Capabilities

- `feishu-webhook`: 飞书 Webhook 接收与验证
- `feishu-access-control`: 访问控制策略管理（dm_policy/group_policy/白名单）
- `feishu-user-resolution`: 用户身份解析（open_id → employee_no → usernumb）
- `feishu-block-streaming`: BlockStreaming 回复策略与流式卡片（含段落感知刷新）
- `feishu-human-like-delay`: 人机延迟，模拟人类回复节奏
- `feishu-media-download`: 媒体文件下载与存储
- `feishu-message-debounce`: Inbound Debounce 消息缓冲合并
- `feishu-audit-logging`: 消息审计与链路追踪

### Modified Capabilities

无（本项目为新增功能，不修改现有 spec）

## Impact

### 代码影响
- 新增 `app/integrations/feishu/` 模块（客户端、访问控制、用户解析）
- 新增 `app/tasks/feishu_consumer.py`（ARQ Task：process_feishu_message，统一处理飞书消息）
- 新增 `app/worker_feishu.py`（独立 Worker，包含 `message_transfer_loop` 长驻任务，使用 BRPOPLPUSH 将 Redis List 消息转入 ARQ 队列）
- 修改 `app/worker.py`（定时任务 Worker，保持不变）
- 新增 `feishu-webhook/` 独立服务（FastAPI）

### 依赖影响
- 新增依赖：`lark-oapi`（飞书 SDK）
- Redis：新增 `feishu:webhook:queue`（中转队列），新增独立 ARQ 队列 `arq:feishu:queue` 专门处理飞书消息
- PostgreSQL：新增 5 张表

### 部署影响
- 新增 `feishu-webhook` 服务进程（Docker 容器）
- 新增独立的 `feishu-worker` 服务进程（专门处理飞书消息，独立扩缩容）
- 现有 `worker` 服务（定时任务）保持不变，与飞书 Worker 分离部署
- Worker 进程需要访问飞书 API（外网）
- 需要配置飞书应用凭证（app_id, app_secret, encrypt_key）
- Redis 队列：新增 `arq:feishu:queue` 专门存储飞书消息任务

### 性能影响
- Debounce 等待引入 2-3 秒延迟（可配置）
- BlockStreaming 累积引入 1-2 秒延迟（可配置）
- 段落感知刷新可能增加少量 CPU 开销（边界检测），但提升用户体验
- **人机延迟引入 500-1500ms 额外延迟（可配置，可禁用）**
- 媒体下载占用带宽和存储空间

### 安全影响
- 飞书 Webhook 需要验证签名（X-Signature）
- 消息内容可能包含敏感信息，需加密传输
- Token 缓存需设置合理 TTL
- 媒体文件存储需限制访问权限
