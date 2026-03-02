# Security Spec — 安全网关

## 1. 概述

安全网关是所有请求的第一道防线，Phase 1 实现 JWT 鉴权 + 审计日志，限流/注入检测留 stub。

---

## 2. JWT 鉴权（M01-1）

`app/security/auth.py`

### Token 双 Token 设计

| Token | 有效期 | 用途 |
|-------|-------|------|
| access_token | 30min（ACCESS_TOKEN_EXPIRE_MINUTES） | API 鉴权 |
| refresh_token | 7天（REFRESH_TOKEN_EXPIRE_MINUTES=10080min） | 刷新 access_token |

### JWT Payload
```python
{
    "sub": user_id,              # 用户 UUID
    "jti": uuid4(),              # Token 唯一 ID（黑名单用）
    "usernumb": "1131618",       # 工号（数据隔离主键）
    "role": "viewer",            # 角色
    "department": "制造部",      # 部门
    "data_scope": {},            # 数据权限范围
    "permissions": [],           # 权限列表
    "iat": now,                  # 签发时间
    "exp": now + timedelta(...), # 过期时间
}
```

### 校验流程（get_current_user FastAPI 依赖）
```
Bearer Token
    │
    ▼ jwt.decode(token, JWT_SECRET, HS256)
    │ → ExpiredSignatureError → 401 "Token 已过期"
    │ → InvalidTokenError    → 401 "无效 Token"
    │
    ▼ Redis 黑名单检查（key: blacklist:{jti}）
    │ → 存在 → 401 "Token 已注销"
    │
    ▼ 返回 AuthenticatedUser(id, usernumb, username, role, ...)
```

### AuthenticatedUser
所有接口通过 `Depends(get_current_user)` 获取用户上下文，贯穿整个请求生命周期：
```python
@dataclass
class AuthenticatedUser:
    id: str           # user UUID
    usernumb: str     # 工号（核心标识，用于数据隔离）
    username: str
    role: str
    department: str | None
    data_scope: dict
    permissions: list[str]
```

---

## 3. 登录系统

`app/security/login.py`

### 端点
```
POST /api/auth/login         → {access_token, refresh_token}
POST /api/auth/refresh       → {access_token}
POST /api/auth/logout        → 将 access_token jti 加入 Redis 黑名单
```

### 密码校验
使用 `bcrypt` 库直接调用（注意：passlib 1.7 与 bcrypt >= 4.1 不兼容，已规避）：
```python
bcrypt.checkpw(password.encode(), stored_hash.encode())
```

---

## 4. 审计日志（M01-2）

`app/security/audit.py`

### 写入策略
```python
# Fire-and-forget，不阻塞请求
audit_logger.log_background(
    trace_id=trace_id,
    user_id=user.id,
    usernumb=user.usernumb,
    action="chat",              # chat | chat_stream | plugin_command | ...
    route=final_result.route,
    input_text=body.message,    # 用户原始输入（不加密/哈希）
    duration_ms=duration_ms,
    metadata={                  # JSON 扩展字段
        "intent": "writing",
        "confidence": 0.95,
        "iterations": 5,        # L3 时
        "is_degraded": False,
        "token_usage": {...},
    },
)
```

### DB 表：`sunny_agent.audit_logs`
```sql
id UUID PRIMARY KEY,
trace_id VARCHAR(64),
user_id UUID,
usernumb VARCHAR(50),
action VARCHAR(100),
route VARCHAR(50),
input_text TEXT,          -- 用户原始输入
duration_ms INTEGER,
metadata JSONB,
created_at TIMESTAMPTZ
```

---

## 5. 注入检测（stub）

`app/security/injection_detector.py`

Phase 1 暂未实现，预留接口：
```python
class InjectionDetector:
    def check(self, text: str) -> DetectionResult:
        return DetectionResult(is_safe=True)  # 暂时放行所有
```

Phase 3 计划：正则 + 向量语义双重检测 SQL/Prompt/Path 注入。

---

## 6. 限流（stub）

`app/security/rate_limiter.py`

Phase 1 暂未实现，预留接口：
```python
class RateLimiter:
    async def check(self, key: str) -> RateLimitResult:
        return RateLimitResult(allowed=True)
```

Phase 3 计划：Redis Sliding Window，按 usernumb 或 IP 限流。

---

## 7. 生产环境安全约束

`config.py` 中的 `@model_validator`：
```python
if self.ENV == "production" and (
    self.JWT_SECRET.startswith("dev-") or len(self.JWT_SECRET) < 32
):
    raise ValueError("生产环境 JWT_SECRET 不能使用默认值，且长度必须 >= 32 位")
```
