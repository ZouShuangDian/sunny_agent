# Observability Spec — 可观测性

## 1. 三层可观测性

```
┌─────────────────────────────────┐
│  Logs（结构化日志）              │
│  structlog + JSON               │
│  每次请求：trace_id + duration  │
├─────────────────────────────────┤
│  Metrics（Prometheus 指标）      │
│  /metrics 端点                  │
│  HTTP 请求 / LLM 调用 / 工具调用 │
├─────────────────────────────────┤
│  Audit（审计日志）               │
│  PG write-behind                │
│  用户操作 + 完整输入文本          │
└─────────────────────────────────┘
```

---

## 2. 结构化日志（structlog）

`app/observability/logging_config.py`

### 初始化
```python
setup_logging(env=settings.ENV)
# development → 彩色可读格式（ConsoleRenderer）
# production  → JSON 格式（JSONRenderer）
```

### 使用方式
```python
log = structlog.get_logger()
log.info("用户 Skill 列表已加载", usernumb=usernumb, count=len(skills))
log.warning("L3 优雅降级", reason=reason, iterations=..., elapsed_ms=...)
log.error("bash_tool 异常", error=str(e), exc_info=True)
```

---

## 3. 请求日志中间件

`app/observability/request_logger.py`

```python
class RequestLoggerMiddleware(BaseHTTPMiddleware):
    # 每次请求自动记录：
    # - method, path, status_code, duration_ms
    # - trace_id（从 context ContextVar 读取）
    # - client_ip
```

---

## 4. Prometheus 指标

`app/observability/metrics.py`

### 指标清单

**请求级别**
```python
sunny_request_total{method, endpoint, status_code}    # Counter
sunny_request_duration_ms{method, endpoint}           # Histogram
# buckets: [50, 100, 200, 500, 1000, 2000, 5000, 10000]
```

**LLM 调用**
```python
sunny_llm_call_total{model, purpose}                  # Counter
# purpose: intent / execution / direct_response
sunny_llm_call_duration_ms{model, purpose}            # Histogram
# buckets: [200, 500, 1000, 2000, 5000, 10000, 30000]
```

**执行层**
```python
sunny_route_total{route, intent}                      # Counter
# route: standard_l1 / deep_l3
sunny_tool_call_total{tool_name, status}              # Counter
# status: success / error
```

**错误**
```python
sunny_error_total{error_type}                         # Counter
# error_type: llm_error / tool_error / guardrails_fallback / unknown
```

### 端点
```
GET /metrics   → Prometheus text format（prometheus_client.make_asgi_app()）
```

---

## 5. 全链路追踪（trace_id）

`app/observability/context.py`

```python
# 每个请求生成唯一 trace_id（UUID）
# 注入到 RequestLoggerMiddleware，贯穿整个请求

def get_trace_id() -> str
def set_trace_id(trace_id: str) -> None
```

trace_id 在以下场景使用：
- 请求日志（request_logger）
- 审计日志（audit.py）
- IntentResult 字段（便于调试）
- GuardrailsValidator 日志

---

## 6. MetricsMiddleware

`app/observability/metrics_middleware.py`

```python
class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = (time.time() - start) * 1000

        REQUEST_TOTAL.labels(method, path, status_code).inc()
        REQUEST_DURATION.labels(method, path).observe(duration)

        return response
```

---

## 7. 告警（stub）

`app/observability/alerting.py`

Phase 1 暂未实现，预留接口。Phase 3 计划：
- 基于 Prometheus AlertManager
- 关键告警：LLM 错误率、L3 降级率、PG 连接失败

---

## 8. 中间件注册顺序

```python
# FastAPI 中间件：注册从下到上，执行从上到下
app.add_middleware(RequestLoggerMiddleware)  # 外层：日志（最先执行）
app.add_middleware(MetricsMiddleware)        # 内层：指标（次之执行）
```
