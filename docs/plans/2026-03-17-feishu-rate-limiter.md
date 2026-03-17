# 飞书消息限流器 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现基于 Redis 的飞书消息限流器，支持按 app_id 隔离的频率限制和并发控制，保护服务免受单个用户过度使用。

**Architecture:** 
- 在 `app/security/rate_limiter.py` 中实现 RateLimiter 类，使用 Redis 原子操作
- 在 ARQ 任务入口 `app/feishu/tasks.py` 集成限流检查
- 限流触发时发送友好的飞书卡片提示，支持延迟重试机制
- 所有 Redis Key 按 `{app_id}:{user_id}` 隔离，确保不同机器人互不影响

**Tech Stack:** Python, Redis (ARQ), 飞书开放平台 API, ARQ 任务队列

---

## 配置参数

```python
RATE_LIMIT_CONFIG = {
    "max_rpm": 20,           # 每分钟最多 20 条消息
    "max_concurrent": 1,     # 最多 1 个并发请求（用户确认）
    "delay_seconds": 3,      # 延迟处理时间
    "max_delay_attempts": 3, # 最多延迟 3 次
}
```

---

## Task 1: 实现 RateLimiter 核心逻辑

**Files:**
- Modify: `app/security/rate_limiter.py`
- Test: `tests/security/test_rate_limiter.py`

**Step 1: 编写失败的测试**

创建测试文件 `tests/security/test_rate_limiter.py`:

```python
import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from app.security.rate_limiter import RateLimiter


class TestRateLimiter:
    """测试限流器核心功能"""
    
    @pytest.mark.asyncio
    async def test_check_rate_limit_allows_under_limit(self):
        """测试在限制范围内允许通过"""
        limiter = RateLimiter()
        
        # Mock Redis 返回低计数
        with patch('app.security.rate_limiter.redis_client') as mock_redis:
            mock_redis.scard.return_value = 0  # 并发数 0
            mock_redis.get.return_value = None  # RPM 计数器不存在
            
            allowed, reason = await limiter.check_rate_limit(
                app_id="test_app",
                user_id="test_user",
                message_id="test_msg"
            )
            
            assert allowed is True
            assert reason == "ok"
    
    @pytest.mark.asyncio
    async def test_check_rate_limit_blocks_concurrent(self):
        """测试并发数超限时拒绝"""
        limiter = RateLimiter()
        
        # Mock Redis 返回高并发数
        with patch('app.security.rate_limiter.redis_client') as mock_redis:
            mock_redis.scard.return_value = 1  # 并发数已达 1
            mock_redis.get.return_value = None
            
            allowed, reason = await limiter.check_rate_limit(
                app_id="test_app",
                user_id="test_user",
                message_id="test_msg"
            )
            
            assert allowed is False
            assert reason == "concurrent_limit"
    
    @pytest.mark.asyncio
    async def test_check_rate_limit_blocks_rpm(self):
        """测试 RPM 超限时拒绝"""
        limiter = RateLimiter()
        
        # Mock Redis 返回高 RPM
        with patch('app.security.rate_limiter.redis_client') as mock_redis:
            mock_redis.scard.return_value = 0
            mock_redis.get.return_value = b"25"  # RPM=25
            
            allowed, reason = await limiter.check_rate_limit(
                app_id="test_app",
                user_id="test_user",
                message_id="test_msg"
            )
            
            assert allowed is False
            assert reason == "rpm_limit"
    
    @pytest.mark.asyncio
    async def test_start_processing_increments_concurrent(self):
        """测试开始处理时增加并发计数"""
        limiter = RateLimiter()
        
        with patch('app.security.rate_limiter.redis_client') as mock_redis:
            await limiter.start_processing(
                app_id="test_app",
                user_id="test_user",
                message_id="test_msg"
            )
            
            # 验证 SADD 调用
            mock_redis.sadd.assert_called_once()
            call_args = mock_redis.sadd.call_args[0]
            assert call_args[0] == "rl:concurrent:test_app:test_user"
            assert call_args[1] == "test_msg"
    
    @pytest.mark.asyncio
    async def test_end_processing_decrements_concurrent(self):
        """测试处理完成时减少并发计数"""
        limiter = RateLimiter()
        
        with patch('app.security.rate_limiter.redis_client') as mock_redis:
            await limiter.end_processing(
                app_id="test_app",
                user_id="test_user",
                message_id="test_msg"
            )
            
            # 验证 SREM 调用
            mock_redis.srem.assert_called_once()
            call_args = mock_redis.srem.call_args[0]
            assert call_args[0] == "rl:concurrent:test_app:test_user"
            assert call_args[1] == "test_msg"


class TestRateLimiterAppIsolation:
    """测试 app_id 隔离"""
    
    @pytest.mark.asyncio
    async def test_different_app_ids_isolated(self):
        """测试不同 app_id 的限流是隔离的"""
        limiter = RateLimiter()
        
        with patch('app.security.rate_limiter.redis_client') as mock_redis:
            # App A 有高并发
            mock_redis.scard.side_effect = lambda key: 1 if "app_a" in key else 0
            mock_redis.get.return_value = None
            
            # App A 被拒绝
            allowed_a, _ = await limiter.check_rate_limit(
                app_id="app_a",
                user_id="user1",
                message_id="msg1"
            )
            assert allowed_a is False
            
            # App B 允许通过
            allowed_b, _ = await limiter.check_rate_limit(
                app_id="app_b",
                user_id="user1",
                message_id="msg2"
            )
            assert allowed_b is True
```

**Step 2: 运行测试验证失败**

```bash
cd D:\ai_project_2026\sunny_agent
pytest tests/security/test_rate_limiter.py -v
```

Expected: FAIL with "ModuleNotFoundError: No module named 'tests.security'" or test failures

**Step 3: 实现 RateLimiter**

修改 `app/security/rate_limiter.py`:

```python
"""
请求限流器（Phase 2 实现）
基于 Redis 的滑动窗口限流，按 app_id 隔离
"""

import time
from typing import Tuple

from app.cache.redis_client import redis_client


class RateLimiter:
    """
    基于 Redis 的滑动窗口限流
    
    特性:
    - 按 app_id 隔离：不同机器人应用独立限流
    - 双重限制：并发数 + RPM
    - 温和模式：延迟处理而非直接拒绝
    """
    
    def __init__(self):
        self.config = {
            "max_rpm": 20,           # 每分钟最多 20 条
            "max_concurrent": 1,     # 最多 1 个并发（用户确认）
            "delay_seconds": 3,      # 延迟 3 秒
            "max_delay_attempts": 3, # 最多重试 3 次
        }
    
    async def check_rate_limit(
        self,
        app_id: str,
        user_id: str,
        message_id: str
    ) -> Tuple[bool, str]:
        """
        检查限流
        
        Args:
            app_id: 应用 ID（用于隔离）
            user_id: 用户 ID
            message_id: 消息 ID
        
        Returns:
            (是否通过，拒绝原因)
            - (True, "ok"): 通过
            - (False, "concurrent_limit"): 并发超限
            - (False, "rpm_limit"): 频率超限
        """
        # 1. 检查并发数
        concurrent_key = f"rl:concurrent:{app_id}:{user_id}"
        concurrent_count = await redis_client.scard(concurrent_key)
        
        if concurrent_count >= self.config["max_concurrent"]:
            return False, "concurrent_limit"
        
        # 2. 检查 RPM
        rpm_key = self._get_rpm_key(app_id, user_id)
        rpm_count = await redis_client.get(rpm_key)
        rpm_value = int(rpm_count) if rpm_count else 0
        
        if rpm_value >= self.config["max_rpm"]:
            return False, "rpm_limit"
        
        return True, "ok"
    
    async def start_processing(
        self,
        app_id: str,
        user_id: str,
        message_id: str
    ):
        """
        开始处理时，增加并发计数
        
        Args:
            app_id: 应用 ID
            user_id: 用户 ID
            message_id: 消息 ID
        """
        concurrent_key = f"rl:concurrent:{app_id}:{user_id}"
        await redis_client.sadd(concurrent_key, message_id)
        await redis_client.expire(concurrent_key, 300)  # 5 分钟 TTL
    
    async def end_processing(
        self,
        app_id: str,
        user_id: str,
        message_id: str
    ):
        """
        处理完成时，减少并发计数并清理 RPM
        
        Args:
            app_id: 应用 ID
            user_id: 用户 ID
            message_id: 消息 ID
        """
        concurrent_key = f"rl:concurrent:{app_id}:{user_id}"
        await redis_client.srem(concurrent_key, message_id)
        
        # 清理重试计数
        retry_key = f"rl:re
