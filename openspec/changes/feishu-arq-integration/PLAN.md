# 飞书 ARQ 集成实施计划

## 1. P0 修复汇总（架构评审后修正）

### 1.1 修复清单

| 问题编号 | 问题描述 | 严重程度 | 修复方案 | 状态 |
|---------|---------|---------|---------|------|
| **P0-1** | `arq.cron(second=0)` 是**每分钟**执行，不是每秒 | Critical | 改为 Worker startup 中的 **BRPOPLPUSH 长驻任务** | ✅ 已修复 |
| **P0-2** | `DebounceManager.timers` 是**内存字典**，跨 arq Job 实例不共享 | Critical | 改为**纯 Redis 实现**（SETEX + TTL 检测） | ✅ 已修复 |
| **P0-3** | `LRANGE + LTRIM` 两步操作之间有**竞态窗口**，会丢失消息 | Critical | 使用 **BRPOPLPUSH 原子操作** 替代 | ✅ 已修复 |
| **P0-4** | 伪代码调用参数与 `run_agent_pipeline()` **实际签名不匹配** | Critical | 对齐实际接口签名（见下方详细说明） | ✅ 已修复 |

### 1.2 P0-1 修复详情：消息转移机制

**原设计（错误）：**
```python
# 每分钟第 0 秒执行，不是每秒！
cron(transfer_feishu_to_arq, second=0)

# LRANGE + LTRIM 有竞态窗口
messages = await redis.lrange("queue", 0, 99)
await redis.ltrim("queue", len(messages), -1)  # 期间新消息会丢失
```

**修复后（示意，完整实现见下方 `message_transfer_loop` 函数）：**
```python
# Worker startup 中启动长驻任务
async def startup(ctx):
    ctx["transfer_task"] = asyncio.create_task(message_transfer_loop(ctx["redis"]))

# 使用 BRPOPLPUSH 原子操作（非 BRPOP，确保崩溃时消息可恢复）
async def message_transfer_loop(redis):
    PROCESSING_QUEUE = "feishu:processing:queue"
    while True:
        # BRPOPLPUSH 原子移动到暂存队列，崩溃时消息仍在 processing:queue 中
        message_bytes = await redis.brpoplpush("feishu:webhook:queue", PROCESSING_QUEUE, timeout=1)
        if message_bytes:
            await redis.enqueue_job("process_feishu_message", message_bytes, _queue_name="arq:feishu:queue")
            await redis.lrem(PROCESSING_QUEUE, 1, message_bytes)
```

### 1.3 P0-2 修复详情：Debounce 纯 Redis 实现

**原设计（错误）：**
```python
class DebounceManager:
    def __init__(self, redis):
        self.timers = {}  # 内存字典，跨 Worker 不共享！
    
    async def _start_timer(self, key):
        self.timers[key] = asyncio.create_task(timer_callback())
```

**修复后：**
```python
class DebounceManager:
    async def _reset_timer(self, session_key: str):
        """使用 Redis SETEX 替代内存定时器"""
        await self.redis.setex(
            f"feishu:timer:{session_key}",
            self.DEBOUNCE_SECONDS,
            "1"
        )
    
    async def _check_timer(self, session_key: str) -> bool:
        """检查定时器是否存在（Redis TTL 机制）"""
        exists = await self.redis.exists(f"feishu:timer:{session_key}")
        return bool(exists)
```

### 1.4 P0-4 修复详情：对齐 run_agent_pipeline 接口

**实际接口签名：**
```python
async def run_agent_pipeline(
    *,
    usernumb: str,           # 用户工号
    user_id: str,            # 用户 UUID
    input_text: str,         # 用户输入（单条字符串）
    session_id: str | None = None,
    trace_id: str | None = None,
    source: str = "chat",    # 来源标识
) -> tuple[str, str]:       # 返回 (reply_text, session_id)
```

**修复后的调用（含 chat_id → session_id 映射）：**
```python
# 1. 合并多条消息为单条输入
merged_text = merge_messages(messages)

# 2. 查询 chat_id → session_id 映射
mapper = ChatSessionMapper(redis)
session_id = await mapper.get_session_id(chat_id)

# 若不存在，生成新 session_id
if session_id is None:
    session_id = str(uuid.uuid4())

# 3. 调用 Agent Pipeline
reply_text, new_session_id = await run_agent_pipeline(
    usernumb=user["usernumb"],
    user_id=str(user["id"]),
    input_text=merged_text,
    session_id=session_id,     # ← 传入当前 session_id
    trace_id=str(uuid.uuid4()),
    source="feishu",           # 来源标识
)

# 4. 对话完成后存储映射（异步）
if await mapper.get_session_id(chat_id) is None:
    await mapper.set_session_id(chat_id, new_session_id)
```

### 1.5 新增 Redis Key（P0-2 修复后）

| Key Pattern | 类型 | 说明 | TTL |
|------------|------|------|-----|
| `feishu:timer:{session_key}` | String | Debounce 定时器标记 | 2s (DEBOUNCE_SECONDS) |
| `feishu:processed:{event_id}:{msg_id}` | String | 幂等去重标记 | 86400s (24h) |

### 1.6 上一轮 P1 问题处理说明（新P1-2）

以下上一轮评审提出的 P1 问题，**本版本接受风险或延后处理**：

| 问题 | 决策 | 说明 |
|------|------|------|
| **Redis 连接池隔离** | ⚠️ **接受风险** | 飞书 Worker 与主业务共享 Redis 连接池。当前架构使用同一 Redis 实例的不同数据库（db0/db1）进行逻辑隔离。若后续出现性能瓶颈，再考虑独立连接池。 |
| **监控指标缺失** | ✅ **已部分解决** | ARCHITECTURE.md 第8章已定义关键指标（队列深度、处理耗时、失败率等）。具体 Grafana Dashboard 配置延至 Phase 2 实施。 |
| **Webhook 签名验证** | ⚠️ **编码阶段补充** | 设计文档已提及，详细实现（Encrypt Key 校验）在编码时补充。 |
| **source="feishu" 注册** | ⚠️ **编码前完成** | 需在主项目 `_SOURCE_SUB_INTENT_MAP` 中注册，编码前完成。 |
| **Token 刷新分布式锁** | ⚠️ **接受风险** | 当前依赖 Redis 缓存 7000s TTL，并发刷新概率低。若出现限流，再考虑分布式锁。 |
| **错误重试退避策略** | ✅ **已解决** | ARQ 内置指数退避（1s, 2s, 4s...）。 |

---

## 2. 项目概述

### 1.1 项目目标

实现飞书与 Sunny Agent 的集成，支持：
- 飞书消息接收与异步处理
- 智能 Debounce 消息合并
- BlockStreaming 流式回复
- 人机延迟模拟
- 访问控制与白名单
- 媒体文件处理
- 完整审计日志

### 1.2 项目范围

**包含:**
- feishu-webhook 服务开发
- Feishu Worker 开发
- 数据库表设计与迁移
- Redis 状态管理
- 飞书 API 集成
- 访问控制实现
- 流式卡片支持
- 监控与告警

**不包含:**
- 飞书主动推送消息
- 复杂审批流程
- 飞书小程序/H5
- 现有 Web 聊天功能修改

## 2. 实施阶段

### 阶段 1: 基础设施准备 (Week 1)

#### 2.1.1 数据库迁移

**任务:**
- [ ] 创建 Alembic migration 脚本
- [ ] 创建 6 张新表
  - feishu_access_config
  - feishu_group_config
  - feishu_user_bindings
  - feishu_media_files
  - feishu_message_logs
  - feishu_chat_session_mapping（chat_id → session_id 双写持久化）

**DDL 脚本:**
```sql
-- feishu_access_config
CREATE TABLE feishu_access_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_id VARCHAR(64) NOT NULL,
    config_name VARCHAR(100) NOT NULL,
    dm_policy VARCHAR(20) DEFAULT 'disabled',
    group_policy VARCHAR(20) DEFAULT 'disabled',
    dm_allowlist JSONB DEFAULT '[]',
    group_allowlist JSONB DEFAULT '[]',
    groups JSONB DEFAULT '{}',
    block_streaming_config JSONB DEFAULT '{"enabled": true, "coalesce": {"min_chars": 800, "max_chars": 1200, "idle_ms": 1000, "joiner": "\\n\\n", "flush_on_enqueue": true, "paragraph_aware": true}}',
    streaming_card JSONB DEFAULT '{"enabled": true, "title": "AI 助手", "update_interval_ms": 50, "chars_per_update": 2}',
    chunk_config JSONB DEFAULT '{"chunk_size": 2000, "chunk_mode": "paragraph"}',
    debounce_wait_seconds INT DEFAULT 2,
    human_like_delay JSONB DEFAULT '{"enabled": true, "min_ms": 500, "max_ms": 1500}',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- feishu_group_config
CREATE TABLE feishu_group_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_id VARCHAR(64) NOT NULL,
    chat_id VARCHAR(64) NOT NULL,
    chat_name VARCHAR(200),
    require_mention BOOLEAN DEFAULT true,
    allow_from JSONB DEFAULT '[]',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(app_id, chat_id)
);

-- feishu_user_bindings
CREATE TABLE feishu_user_bindings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_id VARCHAR(64) NOT NULL,
    open_id VARCHAR(64) NOT NULL,
    union_id VARCHAR(64),
    employee_no VARCHAR(32),
    usernumb VARCHAR(32),
    user_id UUID,
    binding_status VARCHAR(20) DEFAULT 'approved',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(app_id, open_id)
);

-- feishu_media_files
CREATE TABLE feishu_media_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id VARCHAR(64) NOT NULL,
    file_key VARCHAR(128) NOT NULL,
    file_name VARCHAR(255),
    file_type VARCHAR(20),
    file_size BIGINT,
    local_path VARCHAR(500),
    file_hash VARCHAR(64),
    user_id UUID,
    created_at TIMESTAMP DEFAULT NOW()
);

-- feishu_message_logs
CREATE TABLE feishu_message_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    open_id VARCHAR(64) NOT NULL,
    chat_id VARCHAR(64) NOT NULL,
    status VARCHAR(20) DEFAULT 'received',
    processing_time_ms INT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- feishu_chat_session_mapping（新增：双写持久化）
-- 存储 chat_id → session_id 映射，支持会话延续和历史对话
CREATE TABLE feishu_chat_session_mapping (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id VARCHAR(64) NOT NULL,        -- 飞书 chat_id（唯一标识一个对话窗口）
    session_id VARCHAR(64) NOT NULL,     -- 对应 chat_sessions.session_id
    user_id UUID NOT NULL,               -- 系统用户ID（关联 users 表）
    created_at TIMESTAMP DEFAULT NOW(),  -- 首次建立映射时间
    last_active_at TIMESTAMP DEFAULT NOW(), -- 最后活跃时间（每次对话更新）
    UNIQUE(chat_id)  -- 一个 chat_id 只对应一个 session_id
);

-- 创建索引
CREATE INDEX idx_feishu_access_config_app_id ON feishu_access_config(app_id);
CREATE INDEX idx_feishu_group_config_app_id_chat_id ON feishu_group_config(app_id, chat_id);
CREATE INDEX idx_feishu_user_bindings_app_id_open_id ON feishu_user_bindings(app_id, open_id);
CREATE INDEX idx_feishu_user_bindings_employee_no ON feishu_user_bindings(employee_no);
CREATE INDEX idx_feishu_message_logs_message_id ON feishu_message_logs(message_id);
CREATE INDEX idx_feishu_message_logs_created_at ON feishu_message_logs(created_at);
CREATE INDEX idx_feishu_chat_session_mapping_chat_id ON feishu_chat_session_mapping(chat_id);
CREATE INDEX idx_feishu_chat_session_mapping_session_id ON feishu_chat_session_mapping(session_id);
CREATE INDEX idx_feishu_chat_session_mapping_user_id ON feishu_chat_session_mapping(user_id);
```

**验收标准:**
- [ ] Migration 脚本可正常执行
- [ ] 所有表结构正确创建
- [ ] 索引创建成功

#### 2.1.2 Redis 配置

**任务:**
- [ ] 确认 Redis 可访问
- [ ] 配置持久化策略（AOF）
- [ ] 验证 Key 过期策略

**配置:**
```conf
# redis.conf
appendonly yes
appendfsync everysec
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
maxmemory 2gb
maxmemory-policy allkeys-lru
```

**验收标准:**
- [ ] Redis 服务正常运行
- [ ] 持久化配置生效
- [ ] 可从 Worker 连接

#### 2.1.3 飞书应用创建

**任务:**
- [ ] 在飞书开放平台创建应用
- [ ] 配置事件订阅（Webhook URL）
- [ ] 记录 app_id, app_secret, encrypt_key
- [ ] 配置事件订阅权限
  - 接收消息
  - 接收群组@提及

**验收标准:**
- [ ] 应用创建成功
- [ ] Webhook URL 可访问
- [ ] 事件订阅配置完成

---

### 阶段 2: 核心服务开发 (Week 2-3)

#### 2.2.1 feishu-webhook 服务 ✅ 已完成

**状态:** 已部署完成

**实现概述:**
- FastAPI 独立服务已部署
- 签名验证、消息解密、Redis 队列推送功能已验证
- 服务健康运行中

**关键实现点:**
- BRPOPLPUSH 原子操作实现消息可靠转移
- processing:queue 作为暂存队列确保消息不丢失
- 幂等校验机制（chat_id + message_id + create_time）
- 隔离队列处理（isolation:queue）用于异常消息

**不再重复开发，直接进入下游模块开发。**

#### 2.2.2 飞书集成模块 (app/integrations/feishu/)

**任务:**
- [ ] 创建 client.py - Feishu API 客户端
- [ ] 创建 access_control.py - 访问控制
- [ ] 创建 user_resolver.py - 用户身份解析
- [ ] 创建 session_mapper.py - Chat ID 到 Session ID 映射（Redis+PG双写持久化）
- [ ] 创建 media_downloader.py - 媒体下载
- [ ] 创建 block_streaming.py - BlockStreaming 策略
- [ ] 创建 paragraph_coalescer.py - 段落感知刷新
- [ ] 创建 streaming_card.py - 流式卡片管理
- [ ] 创建 human_like_delay.py - 人机延迟

**模块结构:**
```
app/integrations/feishu/
├── __init__.py
├── client.py              # Feishu API 客户端
├── access_control.py      # 访问控制
├── user_resolver.py       # 用户身份解析
├── session_mapper.py      # Chat ID → Session ID 映射（Redis+PG双写持久化）
├── media_downloader.py    # 媒体下载
├── block_streaming.py     # BlockStreaming 策略
├── paragraph_coalescer.py # 段落感知刷新
├── streaming_card.py      # 流式卡片管理
└── human_like_delay.py    # 人机延迟
```

**核心代码 - client.py:**
```python
# app/integrations/feishu/client.py
import lark_oapi as lark
from lark_oapi.api.im.v1 import *

class FeishuClient:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.client = lark.Client.builder() \
            .app_id(app_id) \
            .app_secret(app_secret) \
            .log_level(lark.LogLevel.INFO) \
            .build()
    
    async def send_text_message(self, receive_id: str, text: str):
        """发送文本消息"""
        req = CreateMessageRequest.builder() \
            .receive_id_type("open_id") \
            .request_body(CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            ).build()
        
        resp = await self.client.im.v1.message.acreate(req)
        return resp
    
    async def create_streaming_card(self, title: str = "AI 助手"):
        """创建流式卡片"""
        card_json = {
            "schema": "2.0",
            "config": {
                "streaming_mode": True,
                "summary": {"content": "[思考中...]"},
                "streaming_config": {
                    "print_frequency_ms": {"default": 50},
                    "print_step": {"default": 2},
                },
            },
            "body": {
                "elements": [{"tag": "markdown", "content": "⏳ 思考中...", "element_id": "streaming_content"}]
            }
        }
        # 调用飞书 Card Kit API
        # ...
    
    async def update_streaming_card(self, card_id: str, element_id: str, text: str, sequence: int):
        """更新流式卡片内容"""
        # 调用飞书 Card Kit API
        # ...
    
    async def get_tenant_access_token(self) -> str:
        """获取 Tenant Access Token"""
        # 优先从 Redis 缓存获取
        # 缓存不存在则调用 API 获取
        # ...
```

**核心代码 - block_streaming.py:**
```python
# app/integrations/feishu/block_streaming.py
import asyncio
from typing import Callable, Optional
from dataclasses import dataclass

@dataclass
class BlockStreamingConfig:
    enabled: bool = True
    min_chars: int = 800
    max_chars: int = 1200
    idle_ms: int = 1000
    joiner: str = "\n\n"
    flush_on_enqueue: bool = True
    paragraph_aware: bool = True

class BlockStreaming:
    """BlockStreaming 累积器"""
    
    PARAGRAPH_BOUNDARIES = [
        "\n\n", "\n- ", "\n* ", "\n1. ", "\n> ", "```\n",
        ".\n", "?\n", "!\n"
    ]
    
    def __init__(self, config: BlockStreamingConfig, on_flush: Callable):
        self.config = config
        self.on_flush = on_flush
        self.buffer = ""
        self.idle_timer = None
    
    def enqueue(self, text: str) -> bool:
        """入队新文本，返回是否触发 flush"""
        if not self.config.enabled:
            self.on_flush(text)
            return True
        
        # 检测段落边界
        if self.config.flush_on_enqueue and self._detect_boundary(text):
            if self.buffer:
                self.flush()
            self.on_flush(text)
            return True
        
        # 累积文本
        if self.buffer:
            self.buffer += self.config.joiner + text
        else:
            self.buffer = text
        
        # 检查 max_chars
        if len(self.buffer) >= self.config.max_chars:
            self.flush()
            return True
        
        # 重置 idle 定时器
        self._reset_idle_timer()
        return False
    
    def _detect_boundary(self, text: str) -> bool:
        """检测段落边界"""
        if not self.config.paragraph_aware:
            return False
        
        combined = self.buffer + text
        for boundary in self.PARAGRAPH_BOUNDARIES:
            if combined.endswith(boundary):
                return True
        return False
    
    def _reset_idle_timer(self):
        """重置 idle 定时器"""
        if self.idle_timer:
            self.idle_timer.cancel()
        
        async def idle_callback():
            await asyncio.sleep(self.config.idle_ms / 1000)
            if self.buffer:
                self.flush()
        
        self.idle_timer = asyncio.create_task(idle_callback())
    
    def flush(self):
        """强制 flush 缓冲区"""
        if self.buffer:
            self.on_flush(self.buffer)
            self.buffer = ""
        if self.idle_timer:
            self.idle_timer.cancel()
            self.idle_timer = None
```

**核心代码 - human_like_delay.py:**
```python
# app/integrations/feishu/human_like_delay.py
import asyncio
import random
from typing import Optional
from dataclasses import dataclass

@dataclass
class HumanLikeDelayConfig:
    enabled: bool = True
    min_ms: int = 500
    max_ms: int = 1500

class HumanLikeDelay:
    """人机延迟管理器"""
    
    def __init__(self, config: HumanLikeDelayConfig):
        self.config = config
    
    async def delay(self):
        """执行人机延迟"""
        if not self.config.enabled:
            return
        
        delay_ms = random.randint(self.config.min_ms, self.config.max_ms)
        await asyncio.sleep(delay_ms / 1000)
```

**验收标准:**
- [ ] 所有模块可正常导入
- [ ] 单元测试覆盖率 > 80%
- [ ] FeishuClient 可正常调用 API
- [ ] BlockStreaming 逻辑正确
- [ ] HumanLikeDelay 工作正常

#### 2.2.3 Debounce 管理

**任务:**
- [ ] 实现 Debounce 状态机
- [ ] 实现 Redis 状态管理
- [ ] 实现 should_debounce 钩子
- [ ] 实现批量 drain

**核心代码:**
```python
# app/integrations/feishu/debounce.py
import asyncio
from enum import Enum
from typing import List, Optional
import redis.asyncio as redis

class DebounceState(Enum):
    IDLE = "idle"
    BUFFERING = "buffering"
    PROCESSING = "processing"

class DebounceManager:
    """Debounce 管理器 - 纯 Redis 实现（修复 P0-2）
    
    原设计使用内存字典 self.timers 存储定时器，在分布式 Worker 场景下完全失效。
    改为使用 Redis TTL 机制：
    - feishu:state:{session_key} - 会话状态 (idle/buffering/processing)
    - feishu:buffer:{session_key} - 消息缓冲区 (Redis List)
    - feishu:timer:{session_key} - 定时器标记 (SETEX，TTL=debounce_seconds)
    - feishu:no_text:{session_key} - 无文本防抖标记
    
    定时器触发机制：
    - 不依赖内存定时器，而是在 process_feishu_message 中检查 timer key 是否存在
    - 如果 timer key 已过期（不存在），说明防抖时间到，执行 flush
    """
    
    DEBOUNCE_SECONDS = 2
    NO_TEXT_SECONDS = 3
    MAX_BATCH_SIZE = 10
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
    
    async def on_message(self, message: dict) -> Optional[List[dict]]:
        """
        处理新消息
        返回: None (继续缓冲) / List[dict] (需要处理的消息列表)
        
        流程:
        1. 检查 should_debounce
        2. 获取/设置会话状态
        3. 如果是 buffering 状态且定时器已过期 → flush
        4. 否则加入缓冲区并重置定时器
        """
        session_key = self._get_session_key(message)
        
        # 1. 检查 should_debounce
        if not await self._should_debounce(message):
            return [message]
        
        # 2. 获取当前状态
        state = await self._get_state(session_key)
        
        if state == DebounceState.PROCESSING:
            # 正在处理中，加入缓冲区
            await self._add_to_buffer(session_key, message)
            return None
        
        if state == DebounceState.IDLE:
            # 新会话，开始缓冲
            await self._set_state(session_key, DebounceState.BUFFERING)
            await self._add_to_buffer(session_key, message)
            await self._reset_timer(session_key)
            return None
        
        if state == DebounceState.BUFFERING:
            # 检查定时器是否过期（即防抖时间到）
            timer_exists = await self._check_timer(session_key)
            
            if not timer_exists:
                # 防抖时间到，执行 flush
                messages = await self._flush(session_key)
                if messages:
                    # flush 后重置状态，重新处理当前消息
                    await self._set_state(session_key, DebounceState.BUFFERING)
                    await self._add_to_buffer(session_key, message)
                    await self._reset_timer(session_key)
                    return messages
            else:
                # 仍在防抖期，重置定时器并加入缓冲区
                await self._reset_timer(session_key)
                await self._add_to_buffer(session_key, message)
                
                # 检查是否达到最大批处理数
                buffer_len = await self._get_buffer_length(session_key)
                if buffer_len >= self.MAX_BATCH_SIZE:
                    return await self._flush(session_key)
            
            return None
        
        return None
    
    def _get_session_key(self, message: dict) -> str:
        """生成会话 key"""
        return f"{message['open_id']}:{message['chat_id']}"
    
    async def _should_debounce(self, message: dict) -> bool:
        """检查是否需要 debounce"""
        text = message.get("text", "")
        
        # 系统命令不 debounce
        if text.startswith("/"):
            return False
        
        # [URGENT] 标记不 debounce
        if "[URGENT]" in text:
            return False
        
        # TODO: 调用 should_debounce_hook
        
        return True
    
    async def _check_timer(self, session_key: str) -> bool:
        """检查定时器是否存在（即防抖时间是否未到）"""
        exists = await self.redis.exists(f"feishu:timer:{session_key}")
        return bool(exists)
    
    async def _reset_timer(self, session_key: str):
        """重置防抖定时器（使用 Redis SETEX）"""
        await self.redis.setex(
            f"feishu:timer:{session_key}",
            self.DEBOUNCE_SECONDS,
            "1"
        )
    
    async def _flush(self, session_key: str) -> Optional[List[dict]]:
        """执行 flush，返回缓冲的消息列表"""
        # 1. 获取所有缓冲的消息
        messages = await self._get_buffer(session_key)
        if not messages:
            return None
        
        # 2. 设置处理中状态
        await self._set_state(session_key, DebounceState.PROCESSING)
        
        # 3. 清理缓冲区和定时器
        await self._clear_buffer(session_key)
        await self.redis.delete(f"feishu:timer:{session_key}")
        
        # 4. 合并消息
        if len(messages) == 1:
            return messages
        
        merged = self._merge_messages(messages)
        return [merged]
    
    async def complete_processing(self, session_key: str):
        """处理完成，重置状态为 idle"""
        await self._set_state(session_key, DebounceState.IDLE)


class DebounceScanner:
    """Debounce 主动扫描器（修复 P1-3: 防止最后一条消息永不消费）
    
    问题场景:
    - 用户发送消息后，Debounce 进入 buffering 状态
    - 用户不再发送新消息，Debounce 依赖新消息触发检查
    - 最后一条消息永远无法被消费（buffer 永久积压）
    
    解决方案:
    - 后台定期扫描所有 buffering 状态的会话
    - 检查 timer key 是否已过期
    - 如果过期且状态仍是 buffering，主动触发 flush
    
    扫描策略:
    - 每 5 秒扫描一次
    - 扫描范围: feishu:state:* = "buffering"
    - 对每个 buffering 会话，检查 feishu:timer:{session_key} 是否存在
    - 如果不存在（已过期），调用 DebounceManager._flush(session_key)
    """
    
    SCAN_INTERVAL_SECONDS = 5
    
    def __init__(self, redis_client: redis.Redis, debounce_manager: DebounceManager):
        self.redis = redis_client
        self.debounce = debounce_manager
        self._running = False
    
    async def start(self):
        """启动扫描循环"""
        self._running = True
        while self._running:
            try:
                await self._scan_and_flush()
            except Exception as e:
                logger.error(f"Debounce scan error: {e}")
            await asyncio.sleep(self.SCAN_INTERVAL_SECONDS)
    
    def stop(self):
        """停止扫描"""
        self._running = False
    
    async def _scan_and_flush(self):
        """扫描并 flush 过期的 buffering 会话"""
        # 1. 获取所有 buffering 状态的会话
        # 使用 Redis SCAN 匹配 feishu:state:*
        buffering_sessions = []
        async for key in self.redis.scan_iter(match="feishu:state:*"):
            state = await self.redis.get(key)
            if state and state.decode() == DebounceState.BUFFERING.value:
                # 提取 session_key (去掉前缀 feishu:state:)
                session_key = key.decode().replace("feishu:state:", "")
                buffering_sessions.append(session_key)
        
        # 2. 检查每个会话的 timer 是否过期
        for session_key in buffering_sessions:
            timer_exists = await self.redis.exists(f"feishu:timer:{session_key}")
            if not timer_exists:
                # Timer 已过期，说明防抖时间到但未被消费
                logger.info(f"Debounce scanner: flushing expired session {session_key}")
                messages = await self.debounce._flush(session_key)
                if messages:
                    # 将消息重新入队处理（或触发处理）
                    # 这里需要与 message processor 集成
                    await self._enqueue_for_processing(session_key, messages)
    
    async def _enqueue_for_processing(self, session_key: str, messages: List[dict]):
        """
        将 flush 后的消息入队处理（修复 P1-1）
        
        场景：Debounce 定时器过期后，将缓冲的消息重新入队 ARQ 进行处理
        注意：这些消息已经被 DebounceManager 合并为单条消息
        """
        if not messages:
            return
        
        # 构造合并后的消息体
        merged_message = self._construct_merged_message(session_key, messages)
        
        # 入队到 ARQ 飞书独立队列（注意：必须指定 _queue_name，否则会进入默认队列被错误的 Worker 消费）
        await self.redis.enqueue_job("process_feishu_message", merged_message, _queue_name="arq:feishu:queue")
        
        logger.info(f"Debounce scanner: enqueued {len(messages)} messages for session {session_key}")
    
    def _construct_merged_message(self, session_key: str, messages: List[dict]) -> dict:
        """构造合并后的消息体"""
        # 提取第一个消息的元数据
        first_msg = messages[0]
        
        # 合并所有消息的文本
        merged_text = self._merge_message_texts(messages)
        
        # 构造新消息体
        merged_message = {
            # 保留原始消息的关键字段
            "event": {
                "message": {
                    "chat_id": first_msg.get("chat_id"),
                    "chat_type": first_msg.get("chat_type"),
                    "message_id": f"merged_{first_msg.get('message_id')}",  # 标记为合并消息
                    "message_type": "text",  # 合并后统一为文本
                    "content": json.dumps({"text": merged_text}),
                    "create_time": first_msg.get("create_time"),
                },
                "sender": {
                    "sender_id": {
                        "open_id": first_msg.get("open_id"),
                        "union_id": first_msg.get("union_id"),
                    },
                    "sender_type": "user"
                }
            },
            # 添加合并标记
            "_debounce_merged": True,
            "_merged_count": len(messages),
            "_original_messages": [m.get("message_id") for m in messages],
            "_session_key": session_key,
        }
        
        return merged_message
    
    def _merge_message_texts(self, messages: List[dict]) -> str:
        """合并消息文本"""
        texts = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                try:
                    content_obj = json.loads(content)
                    text = content_obj.get("text", "")
                except:
                    text = content
            else:
                text = content.get("text", "")
            
            if text:
                texts.append(text)
        
        return "\n\n".join(texts)


def _merge_messages(self, messages: List[dict]) -> dict:
        """合并多条消息"""
        if len(messages) == 1:
            return messages[0]
        
        # 合并文本，使用双换行分隔
        texts = [msg.get("text", "") for msg in messages]
        merged_text = "\n\n".join(texts)
        
        merged = messages[0].copy()
        merged["text"] = merged_text
        merged["merged_count"] = len(messages)
        
        return merged
```

**验收标准:**
- [ ] Debounce 状态机工作正确
- [ ] 定时器重置逻辑正确
- [ ] 消息合并逻辑正确
- [ ] should_debounce 钩子可扩展

---

### 阶段 3: Worker 开发 (Week 3-4)

#### 2.3.1 ARQ Worker 配置

**任务:**
- [ ] 配置独立 ARQ Worker
- [ ] 注册飞书 Task
- [ ] 配置重试策略

**核心代码:**
```python
# app/worker_feishu.py
from arq import create_pool
from arq.connections import RedisSettings
import time
import uuid

async def startup(ctx):
    """Worker 启动时初始化"""
    ctx["redis"] = await create_pool(RedisSettings(host="localhost", port=6379))
    ctx["feishu_client"] = FeishuClient(app_id="xxx", app_secret="xxx")

    # 启动消息转移长驻任务（修复 P0-1）
    ctx["transfer_task"] = asyncio.create_task(message_transfer_loop(ctx["redis"]))

    # 启动 DebounceScanner 长驻任务（修复 P1-3）
    debounce_manager = DebounceManager(ctx["redis"])
    scanner = DebounceScanner(ctx["redis"], debounce_manager)
    ctx["scanner_task"] = asyncio.create_task(scanner.start())

async def shutdown(ctx):
    """Worker 关闭时清理"""
    for task_key in ("transfer_task", "scanner_task"):
        if task_key in ctx:
            ctx[task_key].cancel()
            try:
                await ctx[task_key]
            except asyncio.CancelledError:
                pass
    await ctx["redis"].close()

def merge_messages(messages: List[dict]) -> str:
    """合并多条消息为单条输入文本（修复 P0-4: 适配 run_agent_pipeline 的 input_text 参数）
    
    Args:
        messages: Debounce 缓冲的多条消息列表
        
    Returns:
        合并后的文本，多条消息间用双换行分隔
    """
    if len(messages) == 1:
        return messages[0].get("text", "")
    
    texts = [msg.get("text", "") for msg in messages]
    return "\n\n".join(texts)


async def process_feishu_message(ctx, message: dict):
    """处理飞书消息"""
    redis = ctx["redis"]
    db = ctx["db"]
    feishu_client = ctx["feishu_client"]
    
    # 1. Debounce 处理
    debounce_manager = DebounceManager(redis)
    messages = await debounce_manager.on_message(message)
    
    if messages is None:
        # 仍在缓冲中
        return
    
    # 2. 访问控制检查
    access_controller = AccessController(redis)
    if not await access_controller.check_access(messages[0]):
        return
    
    # 3. 用户身份解析
    user_resolver = UserResolver(redis)
    user = await user_resolver.resolve(messages[0]["open_id"])
    
    # 4. 查询 chat_id → session_id 映射（简化设计）
    chat_id = message["event"]["message"]["chat_id"]
    mapper = ChatSessionMapper(redis)
    session_id = await mapper.get_session_id(chat_id)
    
    # 若不存在映射，生成新 session_id
    if session_id is None:
        session_id = str(uuid.uuid4())
    
    # 5. 调用 Agent Pipeline（修复 P0-4: 对齐 run_agent_pipeline 接口签名）
    # 接口签名: run_agent_pipeline(*, usernumb, user_id, input_text, session_id, trace_id, source)
    # 返回: tuple[str, str] -> (reply_text, session_id)
    merged_text = merge_messages(messages)  # 合并多条消息为单条输入
    
    reply_text, new_session_id = await run_agent_pipeline(
        usernumb=user["usernumb"],           # 用户工号
        user_id=str(user["id"]),              # 用户 UUID
        input_text=merged_text,               # 合并后的用户输入
        session_id=session_id,                # ← 传入当前 session_id
        trace_id=generate_trace_id(),         # 生成 trace_id 用于日志追踪
        source="feishu",                      # 来源标识为飞书
    )
    
    # 6. 存储映射关系（异步，对话完成后）
    if await mapper.get_session_id(chat_id) is None:
        await mapper.set_session_id(chat_id, new_session_id)
    
    # 5. BlockStreaming 回复
    streaming_config = BlockStreamingConfig()
    streaming = BlockStreaming(streaming_config, on_flush=...)
    
    # 6. 人机延迟
    delay_config = HumanLikeDelayConfig()
    delay_manager = HumanLikeDelay(delay_config)
    
    # 7. 发送回复
    # ...

async def message_transfer_loop(redis):
    """长驻任务: 使用 BRPOPLPUSH 原子操作将消息从 Redis List 转移到 ARQ 队列
    
    修复 P0-1: 原 arq.cron(second=0) 是每分钟执行，改为长驻循环
    修复 P0-3: 原 LRANGE + LTRIM 有竞态窗口，改为 BRPOPLPUSH 原子操作
    修复 P1: BRPOPLPUSH + processing:queue 确保消息不丢失（即使 Worker 崩溃）
    
    新流程:
    1. BRPOPLPUSH 从 webhook:queue 移到 processing:queue（原子操作）
    2. 处理消息（幂等检查、入队 ARQ）
    3. 成功后再从 processing:queue 删除（LREM）
    4. 失败时消息保留在 processing:queue，可重试或人工恢复
    """
    PROCESSING_QUEUE = "feishu:processing:queue"
    
    while True:
        try:
            # BRPOPLPUSH: 原子操作，将消息从 webhook:queue 移到 processing:queue
            # 即使 Worker 崩溃，消息也在 processing:queue 中，不会丢失
            # timeout=1 表示最多等待1秒，便于优雅退出
            message_bytes = await redis.brpoplpush("feishu:webhook:queue", PROCESSING_QUEUE, timeout=1)
            
            if message_bytes is None:
                # 超时，继续循环
                continue
            
            # 解析消息
            if isinstance(message_bytes, bytes):
                message = json.loads(message_bytes.decode('utf-8'))
            else:
                message = json.loads(message_bytes)
            
            # 幂等校验：检查是否已处理过（修复 P1：使用稳定幂等键）
            # 组合键：chat_id + message_id + create_time
            # 理由：message_id 唯一但可能跨重发，create_time 防重发变体，chat_id 防跨会话冲突
            # Fallback：如果任一字段缺失，使用兜底策略
            msg_id = message.get("message_id", "")
            create_time = message.get("create_time", "")
            chat_id = message.get("chat_id", "")
            
            # Fallback：如果关键字段缺失，进入隔离队列（避免生成不可重现 key 导致重复处理）
            missing_fields = []
            if not msg_id:
                missing_fields.append("message_id")
            if not create_time:
                missing_fields.append("create_time")
            if not chat_id:
                missing_fields.append("chat_id")
            
            if missing_fields:
                logger.error(f"消息缺少关键字段 {missing_fields}，无法保证幂等性，进入隔离队列: {message}")
                # 放入隔离队列，人工处理或记录告警
                await redis.lpush("feishu:isolation:queue", json.dumps({
                    "message": message,
                    "missing_fields": missing_fields,
                    "timestamp": int(time.time()),
                    "reason": "missing_key_fields"
                }))
                # 从 processing:queue 中删除，避免阻塞
                await redis.lrem(PROCESSING_QUEUE, 1, message_bytes)
                continue
            
            duplicate_key = f"feishu:processed:{chat_id}:{msg_id}:{create_time}"
            
            exists = await redis.exists(duplicate_key)
            if exists:
                logger.info(f"跳过重复消息: {msg_id}")
                continue
            
            # 消息转移 ACK 机制（修复 P1-1：防止丢消息）
            # 策略：先标记"处理中"，入队成功后再改为"已处理"
            processing_key = f"feishu:processing:{chat_id}:{msg_id}:{create_time}"
            
            # 检查是否正在处理（防止重复入队）
            if await redis.exists(processing_key):
                logger.info(f"消息正在处理中: {msg_id}")
                continue
            
            # 标记为"处理中"（5分钟过期，防止死锁）
            await redis.setex(processing_key, 300, "1")
            
            try:
            # 入队到 ARQ
            await redis.enqueue_job("process_feishu_message", message)
            
            # 入队成功，从 processing:queue 中删除（使用 LREM 删除特定值）
            # 注意：如果 processing:queue 中有重复消息（不应该发生），只删除一个
            removed = await redis.lrem(PROCESSING_QUEUE, 1, message_bytes)
            if removed == 0:
                logger.warning(f"消息已从 processing:queue 中消失: {msg_id}")
            else:
                logger.debug(f"消息从 processing:queue 移除成功: {msg_id}")
            
        except asyncio.CancelledError:
            # 优雅退出
            logger.info("消息转移任务被取消")
            break
        except Exception as e:
            logger.error(f"消息转移失败: {e}")
            # 短暂休眠后重试，避免高频错误
            await asyncio.sleep(0.1)

async def startup(ctx):
    """Worker 启动时初始化"""
    ctx["redis"] = await create_pool(RedisSettings(host="localhost", port=6379, database=0))
    ctx["feishu_client"] = FeishuClient(app_id="xxx", app_secret="xxx")

    # 启动消息转移长驻任务（修复 P0-1）
    ctx["transfer_task"] = asyncio.create_task(message_transfer_loop(ctx["redis"]))

    # 启动 DebounceScanner 长驻任务（修复 P1-3: 防止最后一条消息永不消费）
    debounce_manager = DebounceManager(ctx["redis"])
    scanner = DebounceScanner(ctx["redis"], debounce_manager)
    ctx["scanner_task"] = asyncio.create_task(scanner.start())

async def shutdown(ctx):
    """Worker 关闭时清理"""
    # 取消消息转移任务
    if "transfer_task" in ctx:
        ctx["transfer_task"].cancel()
        try:
            await ctx["transfer_task"]
        except asyncio.CancelledError:
            pass

    # 取消 DebounceScanner 任务
    if "scanner_task" in ctx:
        ctx["scanner_task"].cancel()
        try:
            await ctx["scanner_task"]
        except asyncio.CancelledError:
            pass

    await ctx["redis"].close()

class WorkerSettings:
    redis_settings = RedisSettings(host="localhost", port=6379, database=0)
    queue_name = "arq:feishu:queue"
    
    functions = [process_feishu_message]
    # 修复 P0-1: 移除 cron_jobs，改为 startup 中的长驻任务
    # cron_jobs = []
    
    on_startup = startup
    on_shutdown = shutdown
    
    max_jobs = 10
    job_timeout = 300
    retry_limit = 3
```

**验收标准:**
- [ ] Worker 可正常启动
- [ ] Task 可正常消费
- [ ] ~~Cron Job~~ **长驻 BRPOPLPUSH 任务** 实时转移消息（修复 P0-1/P0-3）
- [ ] 重试机制工作正常
- [ ] **消息不丢失**（BRPOPLPUSH + processing:queue 验证）
- [ ] **Debounce 定时器跨 Worker 共享**（Redis TTL 验证）

#### 2.3.2 消息消费逻辑

**任务:**
- [ ] 实现完整的消息处理 pipeline
- [ ] 集成所有模块
- [ ] 错误处理和降级

**核心代码:**
```python
# app/tasks/feishu_consumer.py
from typing import List
import asyncio

class FeishuMessageProcessor:
    """飞书消息处理器"""
    
    def __init__(self, redis, db, feishu_client):
        self.redis = redis
        self.db = db
        self.feishu_client = feishu_client
        self.debounce = DebounceManager(redis)
        self.access = AccessController(redis)
        self.user_resolver = UserResolver(redis)
        self.media_downloader = MediaDownloader()
    
    async def process(self, message: dict):
        """处理单条消息"""
        try:
            # 1. 记录日志
            await self._log_message(message, "received")
            
            # 2. Debounce
            messages = await self._debounce(message)
            if not messages:
                return
            
            await self._log_message(messages[0], "processing")
            
            # 3. 访问控制
            if not await self._check_access(messages[0]):
                await self._log_message(messages[0], "blocked")
                return
            
            # 4. 用户解析
            user = await self._resolve_user(messages[0])
            
            # 5. 查询 chat_id → session_id 映射（简化设计）
            chat_id = messages[0]["chat_id"]
            mapper = ChatSessionMapper(self.redis)
            session_id = await mapper.get_session_id(chat_id)
            
            # 若不存在映射，生成新 session_id
            if session_id is None:
                session_id = str(uuid.uuid4())
            
            # 6. 下载媒体
            media_files = await self._download_media(messages)
            
            # 7. 调用 AI
            response, new_session_id = await self._call_ai(messages, user, session_id, media_files)
            
            # 8. 对话完成后存储映射关系
            if await mapper.get_session_id(chat_id) is None:
                await mapper.set_session_id(chat_id, new_session_id)
            
            # 7. 发送回复
            await self._send_reply(messages[0], response)
            
            await self._log_message(messages[0], "completed")
            
        except Exception as e:
            await self._log_message(message, "failed", str(e))
            raise
    
    async def _debounce(self, message: dict) -> List[dict]:
        """Debounce 处理"""
        return await self.debounce.on_message(message)
    
    async def _check_access(self, message: dict) -> bool:
        """访问控制检查"""
        return await self.access.check(message)
    
    async def _resolve_user(self, message: dict):
        """用户身份解析"""
        return await self.user_resolver.resolve(message["open_id"])
    
    async def _download_media(self, messages: List[dict]) -> List[str]:
        """下载媒体文件"""
        files = []
        for msg in messages:
            if msg.get("message_type") == "image":
                file_path = await self.media_downloader.download(msg)
                files.append(file_path)
        return files
    
    async def _call_ai(self, messages, user, session_id, media_files):
        """调用 AI 生成回复（修复 P0-4: 对齐 run_agent_pipeline 接口签名）"""
        from app.execution.pipeline import run_agent_pipeline
        import uuid
        
        # 合并多条消息为单条输入
        merged_text = self._merge_messages(messages)
        
        # 调用 Agent Pipeline
        # 接口签名: run_agent_pipeline(*, usernumb, user_id, input_text, session_id, trace_id, source)
        reply_text, new_session_id = await run_agent_pipeline(
            usernumb=user["usernumb"],
            user_id=str(user["id"]),
            input_text=merged_text,
            session_id=session_id,  # ← 传入当前 session_id
            trace_id=str(uuid.uuid4()),
            source="feishu",
        )
        
        return reply_text, new_session_id
    
    async def _send_reply(self, message: dict, response: str):
        """发送回复"""
        # 1. BlockStreaming
        config = await self._get_streaming_config(message)
        
        if not config.enabled:
            # 直接发送
            await self.feishu_client.send_text_message(
                message["open_id"],
                response
            )
            return
        
        # 2. 创建流式卡片
        card_id = await self.feishu_client.create_streaming_card()
        
        # 3. BlockStreaming 发送
        streaming = BlockStreaming(config, on_flush=...)
        
        # 4. 人机延迟
        delay = HumanLikeDelay(await self._get_delay_config(message))
        await delay.delay()
        
        # 5. 发送
        # ...
    
    async def _log_message(self, message: dict, status: str, error: str = None):
        """记录消息日志"""
        # 写入 feishu_message_logs
        # ...
        pass
```

**验收标准:**
- [ ] 完整 pipeline 可运行
- [ ] 各模块集成正常
- [ ] 错误处理完善
- [ ] 降级策略生效

---

### 阶段 4: 测试与优化 (Week 4-5)

#### 2.4.1 单元测试

**任务:**
- [ ] 编写 feishu-webhook 单元测试
- [ ] 编写各模块单元测试
- [ ] 编写集成测试

**测试覆盖:**
- 签名验证
- 消息解密
- Debounce 逻辑
- BlockStreaming 逻辑
- 访问控制
- 用户解析
- 流式卡片

**验收标准:**
- [ ] 测试覆盖率 > 80%
- [ ] 所有测试通过

#### 2.4.2 集成测试

**任务:**
- [ ] 端到端测试
- [ ] 性能测试
- [ ] 压力测试

**测试场景:**
1. 私聊消息处理
2. 群聊 @提及处理
3. 媒体文件处理
4. 超长文本分块
5. Debounce 合并
6. 并发消息处理
7. 错误恢复

**验收标准:**
- [ ] 端到端测试通过
- [ ] 性能满足要求（< 5s 响应）
- [ ] 压力测试稳定（1000 QPS）

#### 2.4.3 灰度发布

**任务:**
- [ ] 小范围用户测试
- [ ] 收集反馈
- [ ] 问题修复

**灰度策略:**
- 先开放给内部团队
- 再开放给 10% 用户
- 逐步扩大到 100%

**验收标准:**
- [ ] 错误率 < 1%
- [ ] 用户满意度 > 90%

---

## 3. 里程碑

| 里程碑 | 日期 | 交付物 | 验收标准 |
|--------|------|--------|----------|
| M1 | Week 1 | 基础设施 | 数据库表创建、Redis 配置完成 |
| M2 | Week 2 | feishu-webhook | Webhook 服务可接收消息 |
| M3 | Week 3 | 核心模块 | 所有模块开发完成、单元测试通过 |
| M4 | Week 4 | Worker | Worker 可消费队列、集成测试通过 |
| M5 | Week 5 | 上线 | 灰度发布完成、全量上线 |

## 4. 资源需求

### 4.1 人力资源

| 角色 | 人数 | 职责 |
|------|------|------|
| 后端开发 | 2 | 核心模块开发 |
| DevOps | 1 | 部署、监控 |
| 测试 | 1 | 测试、QA |
| 产品 | 1 | 需求、验收 |

### 4.2 技术资源

| 资源 | 规格 | 数量 | 用途 |
|------|------|------|------|
| Docker 主机 | 4C8G | 3 | 部署服务 |
| Redis | 2C4G | 1 | 队列、缓存 |
| PostgreSQL | 2C4G | 1 | 数据存储 |

## 5. 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 飞书 API 变更 | 低 | 高 | 封装 API 调用、预留适配层 |
| 消息积压 | 中 | 高 | 监控告警、自动扩容 |
| Worker 故障 | 低 | 高 | 多实例、健康检查 |
| 安全漏洞 | 低 | 高 | 签名验证、加密传输 |

## 6. 附录

### 6.1 环境变量清单

```bash
# feishu-webhook
FEISHU_WEBHOOK_PORT=8001
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_ENCRYPT_KEY=xxx
REDIS_URL=redis://localhost:6379/0

# Feishu Worker
REDIS_URL=redis://localhost:6379/0
DATABASE_URL=postgresql://user:pass@localhost:5432/db
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
```

### 6.2 API 文档

#### Webhook 接收
```
POST /webhook
Content-Type: application/json
X-Signature: sha256=xxx

{
  "encrypt": "base64_encoded_encrypted_message"
}
```

#### 健康检查
```
GET /health

Response:
{
  "status": "healthy"
}
```

---

**文档版本**: 1.0  
**最后更新**: 2026-03-12  
**作者**: AI Assistant
