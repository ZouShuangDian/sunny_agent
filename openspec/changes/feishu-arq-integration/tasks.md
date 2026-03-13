## 1. Database Migration

- [x] 1.1 Create feishu_access_config table with access control policies and block_streaming_config
- [x] 1.2 Create feishu_group_config table for group-specific settings
- [x] 1.3 Create feishu_user_bindings table for user identity mapping
- [x] 1.4 Create feishu_media_files table for media metadata
- [x] 1.5 Create feishu_message_logs table for audit logging
- [x] 1.6 Create feishu_chat_session_mapping table for session tracking (chat_id → session_id 映射)
- [x] 1.7 Generate and test Alembic migration script
- [x] 1.8 Add database indexes for frequently queried fields

## 2. Feishu Webhook Service (外部项目)

**⚠️ 注意：** Webhook 服务由独立项目 `feishu-sunnyagent-api` 提供
- 项目路径：`D:\ai_project_2026\feishu-sunnyagent-api`
- Webhook URL：https://larkchannel.51dnbsc.top/webhook
- **消息队列：Redis List** `feishu:webhook:queue` (使用 LPUSH 推送)

- [x] 2.1 Initialize FastAPI project structure for feishu-webhook service (外部项目)
- [x] 2.2 Implement webhook endpoint POST /webhook to receive Feishu events (外部项目)

### Webhook Security Implementation (修复 P1-3) - 外部项目完成
- [x] 2.3 Implement signature verification service (外部项目: feishu-webhook/services/signature.py)
  - ✅ Implemented verify_signature(encrypt_key, signature, timestamp, nonce, body)
  - ✅ Use SHA256(timestamp + nonce + encrypt_key + body) algorithm
  - ✅ Return 401 for invalid signatures
- [x] 2.4 Implement message decryption service (外部项目: feishu-webhook/services/decrypt.py)
  - ✅ Implemented decrypt_message(encrypt_key, encrypt) for AES-256-CBC
  - ✅ Handle PKCS7 padding removal
  - ✅ Support both encrypted and plaintext messages
- [x] 2.5 Implement replay protection service (外部项目: feishu-webhook/services/replay_protection.py)
  - ✅ Implemented ReplayProtection class with Redis backend
  - ✅ Check event_id uniqueness with 24-hour TTL
  - ✅ Validate timestamp within 5-minute window
  - ✅ Return success silently for duplicate events
- [x] 2.6 Integrate security layers in webhook endpoint (外部项目)
  - ✅ Verify X-Signature header (required)
  - ✅ Decrypt payload if encrypted (optional)
  - ✅ Check replay using event_id + timestamp
  - ✅ Return 400 for missing headers, 401 for invalid signature
- [x] 2.7 Implement message validation and filtering (外部项目)
- [x] 2.8 Implement Redis Stream producer (外部项目: XADD to feishu:messages)
- [x] 2.9 Implement queue overflow protection (max 10000 messages) (外部项目)
- [x] 2.10 Implement dead letter queue (DLQ) handling (外部项目: feishu:dlq)
  - ✅ Automatic retry 3 times before moving to DLQ
  - ✅ Archive and cleanup DLQ messages
- [x] 2.11 Add health check endpoint GET /health (外部项目)
- [x] 2.12 Add Docker configuration for feishu-webhook service (外部项目: Dockerfile + docker-compose.yml)
- [x] 2.13 Add logging and error handling (外部项目: structlog)

## 3. Feishu Integration Module (app/feishu/)

- [x] 3.1 Create module structure: app/feishu/
- [x] 3.2 Implement FeishuClient class with token management
- [x] 3.3 Implement token caching in Redis (TTL 7000s)
- [x] 3.4 Implement get_user_by_open_id method using /contact/v3/users/batch API
- [x] 3.5 Implement send_message method for sending text/post messages
- [x] 3.6 Implement create_streaming_card method via /cardkit/v1/cards API
- [x] 3.7 Implement update_streaming_card method via PUT /cardkit/v1/cards/{card_id}/elements/{element_id}/content
- [x] 3.8 Implement close_streaming_card method via PATCH /cardkit/v1/cards/{card_id}/settings
- [x] 3.9 Implement error handling and retry logic for API calls
- [x] 3.10 Add rate limit handling with exponential backoff

## 4. Access Control Module (app/feishu/access_control.py)

- [x] 4.1 Implement AccessController class
- [x] 4.2 Implement DM policy check (open/allowlist/disabled)
- [x] 4.3 Implement Group policy check (open/allowlist/disabled)
- [x] 4.4 Implement group allowlist verification
- [x] 4.5 Implement dm allowlist verification
- [x] 4.6 Implement require_mention check for group messages
- [x] 4.7 Implement configuration loading from PostgreSQL
- [x] 4.8 Implement configuration caching in memory
- [x] 4.9 Add support for group-specific configuration overrides
- [x] 4.10 Add rejection message templates

## 5. User Resolution Module (app/feishu/user_resolver.py)

- [x] 5.1 Implement UserResolver class
- [x] 5.2 Implement open_id to employee_no resolution via Feishu API
- [x] 5.3 Implement employee_no to usernumb mapping via users table
- [x] 5.4 Implement user binding creation/updates in feishu_user_bindings table
- [x] 5.5 Handle user not found errors with appropriate error messages
- [x] 5.6 Add caching for user resolution results

## 6. Media Download Module (app/feishu/media_downloader.py)

- [x] 6.1 Implement MediaDownloader class
- [x] 6.2 Implement download_media method using messageResource.get API
- [x] 6.3 Implement streaming download with 8KB chunks
- [x] 6.4 Implement 30MB size limit check
- [x] 6.5 Implement SHA256 hash calculation during download
- [x] 6.6 Implement file storage to uploads/feishu_media/{user_id}/
- [x] 6.7 Implement feishu_media_files table record creation
- [x] 6.8 Implement duplicate detection using file_key and message_id
- [x] 6.9 Handle download failures with retry logic (3 retries)
- [x] 6.10 Add support for image, file, audio, media, sticker types

## 7. Message Debounce Module (app/feishu/debounce.py)

### Time Debounce (基础功能)
- [x] 7.1 Implement DebounceManager class with Redis-based storage
- [x] 7.2 Implement message buffering using Redis List (feishu:buffer:{open_id}:{chat_id})
- [x] 7.3 Implement timer reset strategy (cancel old timer, create new timer on new message)
- [x] 7.4 Implement configurable debounce_wait_seconds (default 2.0s, range 0.5-10s)
- [x] 7.5 Implement message merging strategy (text concatenation with "\n\n", media placeholders)
- [x] 7.6 Implement session state management (idle → buffering → processing → idle)
- [x] 7.7 Implement Redis distributed lock for concurrent session protection
- [x] 7.8 Handle buffer overflow protection (max 100 messages per session)
- [x] 7.9 Add metrics for debounce timing and buffer size

### No-Text Debounce (借鉴 CoPaw)
- [x] 7.10 Implement no-text detection (content_has_text check)
- [x] 7.11 Implement no-text buffering with Redis key (feishu:no_text:{open_id}:{chat_id})
- [x] 7.12 Implement configurable no_text_debounce.enabled (default: true)
- [x] 7.13 Implement configurable no_text_max_wait_seconds (default 3.0s, range 1-10s)
- [x] 7.14 Implement media + text merge logic (prepend media placeholders to text)
- [x] 7.15 Handle no-text timeout (process media-only messages after max wait)

### should_debounce Hook (借鉴 OpenClaw)
- [x] 7.16 Implement default should_debounce logic
  - System commands (starts with "/") → return False
  - [URGENT] tag → return False
  - Other messages → return True
- [x] 7.17 Implement custom hook loader (should_debounce_hook config)
- [x] 7.18 Implement hook error handling (fallback to default logic)
- [x] 7.19 Add hook execution metrics

### Batch Draining 优化 (借鉴 CoPaw Manager)
- [x] 7.20 Implement batch_consume function (drain multiple messages at once)
- [x] 7.21 Implement configurable max_batch_size (default 10, range 1-50)
- [x] 7.22 Implement session-based filtering (only drain same session messages)
- [x] 7.23 Implement put-back logic for different session messages
- [x] 7.24 Add batch size metrics

### DebounceScanner 主动扫描（修复 P1-3）
- [x] 7.25 Implement DebounceScanner class
  - Scan interval: every 5 seconds
  - Scan pattern: feishu:state:* = "buffering"
- [x] 7.26 Implement timer expiration check
  - Check if `feishu:timer:{session_key}` exists
  - If not exists (expired) and state is buffering, trigger flush
- [x] 7.27 Implement re-enqueue logic for flushed messages
  - Push expired buffered messages back to queue
  - Prevent "last message never consumed" scenario
- [x] 7.28 Add scanner metrics (scans per second, expired sessions found)

## 8. BlockStreaming Module (app/feishu/block_streaming.py)

- [x] 8.1 Implement BlockStreamingState class
- [x] 8.2 Implement text accumulation buffer with min_chars/max_chars/idle_ms
- [x] 8.3 Implement flush logic based on OpenClaw block-reply-coalescer
- [x] 8.4 Implement idle timer management
- [x] 8.5 Implement long text chunking (chunk_size 2000, paragraph boundary)
- [x] 8.6 Implement chunk sending strategy (first chunk streaming, subsequent chunks regular)
- [x] 8.7 Add support for configurable parameters via feishu_access_config
- [x] 8.8 Add fallback to regular message if streaming card fails

## 9. Feishu Consumer Task (app/feishu/tasks.py)

- [x] 9.1 Implement consume_feishu_message main loop
- [x] 9.2 Implement BRPOPLPUSH consumption from Redis List
- [x] 9.3 Implement message parsing (open_id, chat_id, chat_type, content, mentions)
- [x] 9.4 Implement text content extraction and @mention removal
- [x] 9.5 Integrate with DebounceManager for message buffering
- [x] 9.6 Integrate with AccessController for access control
- [x] 9.7 Integrate with UserResolver for user identity
- [x] 9.8 Integrate with MediaDownloader for media processing
- [x] 9.9 Integrate with BlockStreaming for AI reply handling
- [x] 9.10 Implement error handling and retry logic
- [x] 9.11 Implement audit logging

## 10. ARQ Integration (独立队列架构)

### 10.1 ARQ Task 实现 (app/feishu/tasks.py)
- [x] 10.1.1 Create `process_feishu_message` ARQ Task function
  - Accept feishu message as parameter
  - Execute full processing pipeline (debounce → access control → user resolution → media → agent → reply)
  - Return processing result for ARQ tracking
- [x] 10.1.2 Implement debounce logic inside ARQ Task
  - Check debounce state before processing
  - Use Redis for debounce state storage
  - Handle debounce timeout and message merging
- [x] 10.1.3 Add ARQ retry configuration for feishu tasks
  - max_retries: 3
  - retry_delay: exponential backoff
  - retry_on: RetryableError, ConnectionError, TimeoutError
- [x] 10.1.4 Add ARQ timeout configuration
  - job_timeout: 300s (5 minutes for full processing)
  - debounce_timeout: 10s (for debounce state check)

### 10.2 长驻消息桥接任务 (连接外部队列和 ARQ 队列)
**⚠️ 重要: 不是 Cron Job！使用长驻 BRPOP 循环从外部 Webhook 服务的 Redis List 消费消息，并桥接到 ARQ 队列**

**外部 Webhook 服务信息:**
- 项目: `feishu-sunnyagent-api`
- URL: https://larkchannel.51dnbsc.top/webhook
- **队列类型: Redis List** (`feishu:webhook:queue`)
- **推送方式:** LPUSH (外部服务推送消息到队列头部)

**本项目桥接器职责:**
1. 使用 BRPOP 从 `feishu:webhook:queue` 阻塞读取消息
2. 推送到 `feishu:processing:queue` (临时存储，用于故障恢复)
3. 幂等校验 (防止重复处理)
4. 入队到 ARQ `arq:feishu:queue`
5. 从 `feishu:processing:queue` 删除 (确认完成)

- [x] 10.2.1 Create `message_transfer_loop` 长驻任务
  - **使用 Redis List BRPOP** 从 `feishu:webhook:queue` 消费消息
  - 在 Worker startup 中启动 asyncio Task (修复 P0-1: 替代 cron)
  - timeout=1s 便于优雅退出
  - 将飞书原始事件格式转换为内部标准格式
  
- [x] 10.2.2 Implement 幂等校验
  - Check `feishu:processed:{event_id}:{msg_id}`
  - Skip if already processed (24h TTL)
  - Mark as processed before enqueue
  
- [x] 10.2.3 Implement 可靠传输机制
  - **Source Queue:** `feishu:webhook:queue` (外部 Webhook 服务推送)
  - **Temp Queue:** `feishu:processing:queue` (故障恢复用)
  - **Target Queue:** `arq:feishu:queue` (ARQ 队列)
  - 消息格式转换：飞书 schema 2.0 → 内部标准格式
  
- [x] 10.2.4 Handle transfer failures
  - Redis List 消息持久化，不会丢失
  - 解析失败的消息从 processing 队列移除（避免无限重试）
  - 处理失败的消息保留在 processing 队列，支持重启后重试
  - Log transfer errors
  - Brief sleep (0.1s) before retry to avoid busy loop

### 10.3 独立 Feishu Worker 配置 (app/worker_feishu.py)
- [x] 10.3.1 Create `app/worker_feishu.py` 独立 Worker 配置
  - Define `WorkerSettingsFeishu` class
  - Set `queue_name = "arq:feishu:queue"` (独立队列)
  - Set `functions = [process_feishu_message]`
  - **Remove `cron_jobs` (修复 P0-1: 不使用 cron，改为 startup hook 启动长驻任务)**
- [x] 10.3.2 Configure Worker 参数（针对飞书场景优化）
  - `max_jobs`: 10 (并发处理数)
  - `job_timeout`: 300s
  - `retry_jobs`: True
  - `keep_result`: 3600s (1小时，用于审计追踪)
- [x] 10.3.3 Implement startup hook
  - Load feishu_access_config to memory cache
  - Initialize FeishuClient with token caching
  - Initialize Redis connections
  - **Start `message_transfer_loop` asyncio Task (修复 P0-1: 长驻 BRPOPLPUSH 循环)**
  - **Start `DebounceScanner` asyncio Task (修复 P1-3: 主动扫描防止消息积压)**
  - Handle graceful shutdown on Worker stop
- [x] 10.3.4 Implement shutdown hook
  - Cleanup Redis connections
  - Flush pending audit logs
- [x] 10.3.5 Add Feishu-specific logging context
  - Inject feishu message_id into structlog context
  - Add feishu session tracking
- [x] 10.3.6 Start isolation queue monitoring task
  - Periodic check of feishu:isolation:queue length
  - Emit metrics for queue length and oldest message age
  - Trigger alerts when thresholds exceeded
  - Log daily summary of isolation queue statistics

### 10.4 定时任务 Worker 保持不变 (app/worker.py)
- [x] 10.4.1 确认现有 `app/worker.py` 配置保持不变
  - Queue: `arq:queue` (默认队列，仅处理定时任务)
  - Functions: `execute_cron_job`
  - Cron Jobs: `scan_and_enqueue`
- [x] 10.4.2 确保两个 Worker 不互相干扰
  - 不同的 queue_name
  - 独立部署和扩缩容

### 10.5 Docker Compose 配置
- [ ] 10.5.1 Update `docker-compose.yml` 添加 feishu-worker 服务
  ```yaml
  feishu-worker:
    image: sunny-agent:latest
    command: arq app.worker_feishu.WorkerSettings
    environment:
      - ARQ_QUEUE_NAME=arq:feishu:queue
    depends_on:
      - redis
      - postgres
  ```
- [ ] 10.5.2 Update cron-worker 服务配置（保持不变）
  ```yaml
  cron-worker:
    image: sunny-agent:latest
    command: arq app.worker.WorkerSettings
    # 默认 queue_name=arq:queue
  ```

### 10.6 ARQ 监控集成
- [x] 10.4.1 Configure ARQ job tracking for feishu tasks
  - Enable result persistence (for retry tracking)
  - Set result_ttl for audit requirements
- [x] 10.4.2 Add custom ARQ middleware for feishu processing
  - Pre-execution: logging, metrics
  - Post-execution: audit log, cleanup
- [x] 10.4.3 Export ARQ metrics for feishu
  - jobs_enqueued_total (by type: feishu)
  - jobs_succeeded_total
  - jobs_failed_total
  - job_duration_seconds histogram

## 11. Agent Pipeline Integration

- [x] 11.1 Extend run_agent_pipeline to support streaming callbacks
- [x] 11.2 Implement on_token callback for real-time streaming updates
- [x] 11.3 Implement on_complete callback for finalization
- [x] 11.4 Add media_paths parameter to include media context in prompts
- [x] 11.5 Add source="feishu" tracking for analytics

## 12. Configuration and Deployment

- [x] 12.1 Add feishu configuration to app/config.py
- [ ] 12.2 Add dependencies to requirements.txt
  - lark-oapi (Feishu SDK)
  - pycryptodome (AES decryption for webhook security)
- [ ] 12.3 Create docker-compose configuration for feishu-webhook service
- [ ] 12.4 Create environment variable templates (.env.example)
- [ ] 12.5 Create deployment documentation (DEPLOYMENT.md)
- [ ] 12.6 Add health check endpoints monitoring

## 13. Testing

- [ ] 13.1 Write unit tests for FeishuClient
- [ ] 13.2 Write unit tests for AccessController
- [ ] 13.3 Write unit tests for UserResolver
- [ ] 13.4 Write unit tests for MediaDownloader
- [ ] 13.5 Write unit tests for DebounceManager
- [ ] 13.6 Write unit tests for BlockStreaming
- [ ] 13.7 Write integration tests for end-to-end message flow
- [ ] 13.8 Add mock Feishu API server for testing

### Webhook Security Tests (修复 P1-3)
- [ ] 13.8.1 Test X-Signature verification
  - Valid signature should pass
  - Invalid signature should return 401
  - Missing signature should return 401
- [ ] 13.8.2 Test Encrypt Key decryption
  - Valid encrypted message should decrypt successfully
  - Invalid key should raise decryption error
- [ ] 13.8.3 Test replay attack protection
  - Same event_id within 5 minutes should be rejected
  - Expired timestamp (>5 min) should be rejected
  - New event_id should be accepted
- [ ] 13.8.4 Test webhook endpoint security
  - Missing headers should return 400
  - Malformed JSON should return 400
  - Valid request should return 200
- [ ] 13.9 Test access control scenarios (allowlist, disabled, etc.)
- [ ] 13.10 Test media download scenarios (success, failure, size limit)
- [ ] 13.11 Test debounce scenarios
  - Single message (no debounce needed)
  - Consecutive messages (timer reset)
  - No-text debounce (media + text merge)
  - No-text timeout (media-only processing)
  - should_debounce hook (custom logic)
  - Batch draining (multiple messages)
  - Concurrent session protection (distributed lock)
- [ ] 13.12 Test block streaming scenarios (normal, long text, error fallback)

## 14. Monitoring and Alerting

- [ ] 14.1 Add metrics collection: messages_processed_total, messages_failed_total, etc.
- [ ] 14.2 Add queue length monitoring (Redis list length)
- [ ] 14.3 Add processing duration histogram
- [ ] 14.4 Add error rate monitoring
- [ ] 14.5 Configure alerts for high error rates (>5%)
- [ ] 14.6 Configure alerts for queue backlog (>1000 messages)
- [ ] 14.7 Configure alerts for rate limiting

### Isolation Queue Monitoring (Critical)
- [ ] 14.7.1 Add isolation queue length metric (`feishu:isolation:queue`)
- [ ] 14.7.2 Configure Warning alert: isolation queue length > 0 for 5 minutes
- [ ] 14.7.3 Configure Critical alert: isolation queue length > 10 for 1 minute
- [ ] 14.7.4 Configure Critical alert: oldest message in isolation queue > 24 hours
- [ ] 14.7.5 Add isolation queue reason breakdown (missing_message_id, missing_create_time, missing_chat_id)
- [ ] 14.7.6 Create runbook for handling isolation queue alerts
  - How to inspect messages in isolation queue
  - How to manually requeue messages with fixed fields
  - How to clear isolation queue after incident resolution
  - SLA requirement: 30 min response time for P1 alerts
- [ ] 14.7.7 Add isolation queue to Grafana dashboard with drill-down capability

- [ ] 14.8 Add Grafana dashboard configuration

## 15. Documentation

- [ ] 15.1 Write API documentation for feishu-webhook endpoints
- [ ] 15.2 Write configuration guide (how to set up access control)
- [ ] 15.3 Write troubleshooting guide (common issues and solutions)
- [ ] 15.4 Write admin guide (how to manage user bindings)
- [ ] 15.5 Update main README with Feishu integration overview
