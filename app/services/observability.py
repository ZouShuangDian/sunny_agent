"""
ObservabilityService — Langfuse 用量统计与健康检查服务

配置来源：DB（通过 LangfuseManager.get_decrypted_config() 注入）

提供：
  - get_status()              — 检查 Langfuse 健康状态
  - get_console_url()         — 返回 Langfuse 控制台 URL
  - get_usage_summary(s,e,u)  — 汇总 token 用量
  - get_usage_daily(s,e,u)    — 按天拆分 token 用量
  - get_usage_by_user(s,e)    — 按用户汇总 token 用量
  - refresh_usage()           — 清除 Redis 缓存并重新拉取
"""

import asyncio
import csv
import io
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import httpx
import redis.asyncio as aioredis

from app.cache.redis_client import RedisKeys

CACHE_TTL = 300  # 5 minutes


def _usage_input(usage: dict) -> int:
    """Extract input token count, compatible with both field naming conventions."""
    return usage.get("input", 0) or usage.get("promptTokens", 0) or 0


def _usage_output(usage: dict) -> int:
    """Extract output token count, compatible with both field naming conventions."""
    return usage.get("output", 0) or usage.get("completionTokens", 0) or 0


def _usage_total(usage: dict) -> int:
    """Extract total token count, compatible with both field naming conventions."""
    return usage.get("total", 0) or usage.get("totalTokens", 0) or 0


class ObservabilityService:
    """Langfuse observability data service with Redis caching.

    Args:
        config: Decrypted Langfuse config dict from DB, or None if not configured.
                Keys: host, public_key, secret_key, sample_rate, flush_interval
        redis: Async Redis client for caching.
    """

    def __init__(self, config: dict | None, redis: aioredis.Redis):
        self.redis = redis
        if config:
            self._host = config["host"]
            self._public_key = config["public_key"]
            self._secret_key = config["secret_key"]
            self._configured = True
        else:
            self._host = ""
            self._public_key = ""
            self._secret_key = ""
            self._configured = False

    def _auth(self) -> tuple[str, str]:
        return (self._public_key, self._secret_key)

    # ------------------------------------------------------------------
    # get_status
    # ------------------------------------------------------------------

    async def get_status(self) -> dict:
        """Check Langfuse health via GET /api/public/health."""
        if not self._configured:
            return {"status": "not_configured"}

        cache_key = RedisKeys.langfuse_health()
        cached = await self.redis.get(cache_key)
        if cached:
            return json.loads(cached)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self._host}/api/public/health",
                    auth=self._auth(),
                )

            if resp.status_code == 200:
                data = resp.json()
                result = {
                    "status": data.get("status", "OK"),
                    "version": data.get("version", "unknown"),
                }
            else:
                result = {"status": "error", "message": f"HTTP {resp.status_code}"}

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, Exception):
            result = {"status": "unreachable"}

        await self.redis.set(cache_key, json.dumps(result), ex=CACHE_TTL)
        return result

    # ------------------------------------------------------------------
    # get_console_url
    # ------------------------------------------------------------------

    async def get_console_url(self) -> dict:
        if not self._configured:
            return {"url": None}
        return {"url": self._host}

    async def proxy_login(self, admin_email: str, admin_password: str) -> dict:
        """代理登录 Langfuse，返回 redirect URL 和 cookies."""
        if not self._configured:
            return {"url": None, "cookies": None, "error": "not_configured"}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Step 1: 获取 CSRF token
                csrf_resp = await client.get(f"{self._host}/api/auth/csrf")
                csrf_data = csrf_resp.json()
                csrf_token = csrf_data.get("csrfToken")

                # Step 2: 代理登录
                login_resp = await client.post(
                    f"{self._host}/api/auth/callback/credentials",
                    data={
                        "email": admin_email,
                        "password": admin_password,
                        "csrfToken": csrf_token,
                        "redirect": "false",
                    },
                    follow_redirects=False,
                )

                # Step 3: 提取 session cookies
                cookies = login_resp.headers.get_list("set-cookie")
                if not cookies:
                    return {"url": self._host, "cookies": None, "error": "login_failed"}

                return {"url": self._host, "cookies": cookies}
        except Exception:
            return {"url": self._host, "cookies": None, "error": "login_failed"}

    # ------------------------------------------------------------------
    # Internal: fetch traces from Langfuse
    # ------------------------------------------------------------------

    async def _fetch_traces(
        self, start: str, end: str, user_id: Optional[str] = None
    ) -> list[dict]:
        if not self._configured:
            return []

        # Append time component so date-only strings include the full day
        from_ts = f"{start}T00:00:00.000Z" if "T" not in start else start
        to_ts = f"{end}T23:59:59.999Z" if "T" not in end else end
        params: dict = {
            "fromTimestamp": from_ts,
            "toTimestamp": to_ts,
        }
        if user_id:
            params["userId"] = user_id

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self._host}/api/public/traces",
                params=params,
                auth=self._auth(),
            )

        if resp.status_code == 200:
            data = resp.json()
            return data.get("data", [])
        return []

    # ------------------------------------------------------------------
    # Internal: fetch observations (GENERATION type) from Langfuse
    # ------------------------------------------------------------------

    async def _fetch_observations(
        self, start: str, end: str
    ) -> list[dict]:
        """Fetch all observations with pagination.

        Note: No type filter is applied because OTEL-based integrations
        (e.g. litellm langfuse_otel callback) may create observations with
        types other than GENERATION (e.g. SPAN). Token usage data can appear
        on any observation type.
        """
        if not self._configured:
            return []

        from_ts = f"{start}T00:00:00.000Z" if "T" not in start else start
        to_ts = f"{end}T23:59:59.999Z" if "T" not in end else end

        all_observations: list[dict] = []
        page = 1

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                resp = await client.get(
                    f"{self._host}/api/public/observations",
                    params={
                        "fromStartTime": from_ts,
                        "toStartTime": to_ts,
                        "page": page,
                        "limit": 100,
                    },
                    auth=self._auth(),
                )

                if resp.status_code != 200:
                    import structlog
                    structlog.get_logger().warning(
                        "Langfuse observations API error",
                        status=resp.status_code,
                        body=resp.text[:200],
                    )
                    break

                data = resp.json()
                items = data.get("data", [])
                # Only keep observations that have usage data (token counts)
                for item in items:
                    usage = item.get("usage")
                    if usage and (_usage_total(usage) > 0 or _usage_input(usage) > 0):
                        all_observations.append(item)

                # Stop if we got fewer items than the page limit
                if len(items) < 100:
                    break
                page += 1

        return all_observations

    # ------------------------------------------------------------------
    # Internal: fetch both traces and observations concurrently
    # ------------------------------------------------------------------

    async def _fetch_traces_and_observations(
        self, start: str, end: str, user_id: Optional[str] = None
    ) -> tuple[list[dict], list[dict]]:
        traces, observations = await asyncio.gather(
            self._fetch_traces(start, end, user_id),
            self._fetch_observations(start, end),
        )
        # If filtered by user_id, only keep observations belonging to those traces
        if user_id:
            trace_ids = {t["id"] for t in traces}
            observations = [o for o in observations if o.get("traceId") in trace_ids]
        return traces, observations

    # ------------------------------------------------------------------
    # get_usage_summary
    # ------------------------------------------------------------------

    async def get_usage_summary(
        self, start: str, end: str, user_id: Optional[str] = None
    ) -> dict:
        uid = user_id or "all"
        cache_key = RedisKeys.langfuse_usage_summary(start, end, uid)
        cached = await self.redis.get(cache_key)
        if cached:
            return json.loads(cached)

        traces, observations = await self._fetch_traces_and_observations(
            start, end, user_id
        )
        result = self._aggregate_summary(traces, observations)

        await self.redis.set(cache_key, json.dumps(result), ex=CACHE_TTL)
        return result

    @staticmethod
    def _aggregate_summary(traces: list[dict], observations: list[dict]) -> dict:
        # Call count: from traces, excluding non-business traces
        biz_traces = [
            t for t in traces if t.get("name") not in ("litellm_request", "test_connection")
        ]
        # Token usage: from observations
        total_tokens = sum(_usage_total(o.get("usage") or {}) for o in observations)
        input_tokens = sum(_usage_input(o.get("usage") or {}) for o in observations)
        output_tokens = sum(_usage_output(o.get("usage") or {}) for o in observations)
        total_cost = sum(o.get("calculatedTotalCost", 0) or 0 for o in observations)
        return {
            "totalCalls": len(biz_traces),
            "totalTokens": total_tokens,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "estimatedCost": round(total_cost, 6),
        }

    # ------------------------------------------------------------------
    # get_usage_daily
    # ------------------------------------------------------------------

    async def get_usage_daily(
        self, start: str, end: str, user_id: Optional[str] = None
    ) -> dict:
        uid = user_id or "all"
        cache_key = RedisKeys.langfuse_usage_daily(start, end, uid)
        cached = await self.redis.get(cache_key)
        if cached:
            return json.loads(cached)

        traces, observations = await self._fetch_traces_and_observations(
            start, end, user_id
        )
        result = self._aggregate_daily(traces, observations)

        await self.redis.set(cache_key, json.dumps(result), ex=CACHE_TTL)
        return result

    @staticmethod
    def _aggregate_daily(traces: list[dict], observations: list[dict]) -> dict:
        by_day: dict[str, dict] = defaultdict(
            lambda: {"tokens": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "calls": 0}
        )

        # Calls: from traces (excluding non-business)
        for t in traces:
            if t.get("name") in ("litellm_request", "test_connection"):
                continue
            ts = t.get("timestamp", "")
            date = ts[:10] if ts else "unknown"
            by_day[date]["calls"] += 1

        # Token usage: from observations by startTime
        for o in observations:
            ts = o.get("startTime", "")
            date = ts[:10] if ts else "unknown"
            usage = o.get("usage") or {}
            by_day[date]["tokens"] += _usage_total(usage)
            by_day[date]["input_tokens"] += _usage_input(usage)
            by_day[date]["output_tokens"] += _usage_output(usage)
            by_day[date]["cost"] += o.get("calculatedTotalCost", 0) or 0

        days = [
            {
                "date": d,
                "calls": v["calls"],
                "tokens": v["tokens"],
                "inputTokens": v["input_tokens"],
                "outputTokens": v["output_tokens"],
                "cost": round(v["cost"], 6),
            }
            for d, v in sorted(by_day.items())
        ]
        return {"days": days}

    # ------------------------------------------------------------------
    # get_usage_by_user
    # ------------------------------------------------------------------

    async def get_usage_by_user(self, start: str, end: str) -> dict:
        cache_key = RedisKeys.langfuse_usage_by_user(start, end)
        cached = await self.redis.get(cache_key)
        if cached:
            return json.loads(cached)

        traces, observations = await self._fetch_traces_and_observations(start, end)
        result = self._aggregate_by_user(traces, observations)

        await self.redis.set(cache_key, json.dumps(result), ex=CACHE_TTL)
        return result

    @staticmethod
    def _aggregate_by_user(traces: list[dict], observations: list[dict]) -> dict:
        by_user: dict[str, dict] = defaultdict(
            lambda: {"tokens": 0, "cost": 0.0, "calls": 0}
        )

        # Map traceId -> userId for observation lookup
        trace_user = {t["id"]: t.get("userId") or "unknown" for t in traces}

        # Calls: from traces (excluding non-business)
        for t in traces:
            if t.get("name") in ("litellm_request", "test_connection"):
                continue
            uid = t.get("userId") or "unknown"
            by_user[uid]["calls"] += 1

        # Token usage: from observations, mapped via traceId
        for o in observations:
            uid = trace_user.get(o.get("traceId"), "unknown")
            usage = o.get("usage") or {}
            by_user[uid]["tokens"] += _usage_total(usage)
            by_user[uid]["cost"] += o.get("calculatedTotalCost", 0) or 0

        users = [
            {
                "userId": uid,
                "userName": "",
                "calls": v["calls"],
                "tokens": v["tokens"],
                "cost": round(v["cost"], 6),
            }
            for uid, v in sorted(by_user.items())
        ]
        return {"users": users}

    # ------------------------------------------------------------------
    # refresh_usage
    # ------------------------------------------------------------------

    async def refresh_usage(self) -> dict:
        await self.redis.delete(RedisKeys.langfuse_health())

        cursor = 0
        deleted = 0
        while True:
            cursor, keys = await self.redis.scan(
                cursor=cursor, match="sunny:langfuse:usage:*", count=100
            )
            if keys:
                await self.redis.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break

        return {"cleared": True, "keys_deleted": deleted}

    # ------------------------------------------------------------------
    # export_traces
    # ------------------------------------------------------------------

    EXPORT_LIMIT = 10000
    CSV_COLUMNS = [
        "id",
        "name",
        "userId",
        "timestamp",
        "totalCost",
        "inputTokens",
        "outputTokens",
        "totalTokens",
    ]

    async def export_traces(
        self,
        start: str,
        end: str,
        fmt: str = "json",
        user_id: Optional[str] = None,
    ) -> dict | str:
        traces, observations = await self._fetch_traces_and_observations(
            start, end, user_id
        )

        if len(traces) > self.EXPORT_LIMIT:
            return {
                "error": True,
                "message": (
                    f"导出数据量超过 {self.EXPORT_LIMIT} 条限制，"
                    f"当前 {len(traces)} 条，请缩小时间范围或添加用户过滤"
                ),
            }

        # Merge observation token data into trace dicts for export
        trace_tokens: dict[str, dict] = defaultdict(
            lambda: {"input": 0, "output": 0, "total": 0, "cost": 0.0}
        )
        for o in observations:
            tid = o.get("traceId", "")
            usage = o.get("usage") or {}
            trace_tokens[tid]["input"] += _usage_input(usage)
            trace_tokens[tid]["output"] += _usage_output(usage)
            trace_tokens[tid]["total"] += _usage_total(usage)
            trace_tokens[tid]["cost"] += o.get("calculatedTotalCost", 0) or 0

        if fmt == "csv":
            return self._format_csv(traces, trace_tokens)

        # Enrich traces with aggregated token data for JSON export
        for t in traces:
            tid = t.get("id", "")
            tokens = trace_tokens.get(tid)
            if tokens:
                t["usage"] = {
                    "input": tokens["input"],
                    "output": tokens["output"],
                    "total": tokens["total"],
                }
                t["totalCost"] = round(tokens["cost"], 6)

        return self._format_json(traces, start, end)

    def _format_json(self, traces: list[dict], start: str, end: str) -> dict:
        result: dict = {
            "exportedAt": datetime.now(timezone.utc).isoformat(),
            "period": {"start": start, "end": end},
            "totalCount": len(traces),
            "traces": traces,
        }
        if len(traces) == 0:
            result["warning"] = "所选时间段内无匹配的 Trace 数据"
        return result

    def _format_csv(self, traces: list[dict], trace_tokens: dict[str, dict]) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(self.CSV_COLUMNS)
        for t in traces:
            tid = t.get("id", "")
            tokens = trace_tokens.get(tid, {})
            writer.writerow(
                [
                    tid,
                    t.get("name", ""),
                    t.get("userId", ""),
                    t.get("timestamp", ""),
                    round(tokens.get("cost", 0), 6),
                    tokens.get("input", 0),
                    tokens.get("output", 0),
                    tokens.get("total", 0),
                ]
            )
        return output.getvalue()
