# Quickstart: Langfuse 可观测性集成

**Date**: 2026-03-13

---

## 前置条件

- Python 3.11+
- Docker Engine 已安装（内置 Langfuse 服务需要）
- SunnyAgent 已正常运行

## 开发环境快速启动

### Step 1: 添加环境变量

在 `.env` 文件中追加以下配置：

```bash
# Langfuse 基础配置
LANGFUSE_ENABLED=true
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=pk-lf-sunny-dev
LANGFUSE_SECRET_KEY=sk-lf-sunny-dev

# Langfuse 管理员凭据（内置服务自动创建此账号）
LANGFUSE_ADMIN_EMAIL=admin@sunnyagent.local
LANGFUSE_ADMIN_PASSWORD=changeme123

# 采样与上报
LANGFUSE_SAMPLE_RATE=1.0
LANGFUSE_FLUSH_INTERVAL=5

# 加密密钥（首次启动自动生成，无需手动填写）
ENCRYPTION_KEY=
```

### Step 2: 启动内置 Langfuse 服务

```bash
docker compose -f infra/langfuse-compose.yml up -d
```

等待约 30-60 秒，Langfuse 及其依赖（ClickHouse、Redis、MinIO、PostgreSQL）全部启动。

验证服务就绪：
```bash
curl http://localhost:3000/api/public/health
# 期望: {"status":"OK","version":"3.63.0"}
```

### Step 3: 运行数据库迁移

```bash
alembic upgrade head
```

### Step 4: 启动 SunnyAgent

```bash
uvicorn app.main:app --reload
```

启动时会自动：
- 从 `.env` 读取 Langfuse 配置并写入数据库（首次启动）
- 生成 `ENCRYPTION_KEY` 并追加到 `.env`（如为空）
- 配置 LiteLLM Langfuse Callback

### Step 5: 验证 Trace 上报

1. 通过 API 发送一条 chat 消息
2. 打开 Langfuse 控制台 `http://localhost:3000`，使用 `.env` 中配置的 email/password 登录
3. 进入 SunnyAgent 项目，查看 Traces 列表
4. 确认 Trace 包含 `user_id`、`session_id`，Span 层级正确

## 连接外部 Langfuse 服务

如果使用已有的外部 Langfuse 实例，跳过 Step 2，修改 `.env`：

```bash
LANGFUSE_HOST=https://your-langfuse.example.com
LANGFUSE_PUBLIC_KEY=pk-lf-your-key
LANGFUSE_SECRET_KEY=sk-lf-your-key
LANGFUSE_ADMIN_EMAIL=your-admin@example.com
LANGFUSE_ADMIN_PASSWORD=your-password
```

或通过管理 API 配置：
```bash
# 验证连接
curl -X POST http://localhost:8000/api/v1/observability/config/validate \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"langfuseUrl": "https://your-langfuse.example.com"}'

# 更新配置
curl -X PUT http://localhost:8000/api/v1/observability/config \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"langfuseUrl": "https://your-langfuse.example.com", "publicKey": "pk-lf-xxx", "secretKey": "sk-lf-xxx"}'
```

## 常见问题

| 问题 | 排查方式 |
|------|----------|
| Trace 没有出现在 Langfuse | 检查 `LANGFUSE_ENABLED=true`，确认 Langfuse 服务可达 |
| 启动报 ENCRYPTION_KEY 错误 | 删除 `.env` 中的空 `ENCRYPTION_KEY=` 行，重启自动生成 |
| Docker Compose 启动失败 | 确认 Docker 已安装且当前用户在 docker 组中 |
| 管理员无法登录 Langfuse 控制台 | 检查 `.env` 中 `LANGFUSE_ADMIN_EMAIL` / `LANGFUSE_ADMIN_PASSWORD` 是否正确 |
| 用量统计显示费用为 0 | Langfuse 可能未配置对应模型的价格，检查 Langfuse Model Pricing 设置 |
