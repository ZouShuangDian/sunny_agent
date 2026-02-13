"""
M03-2 码表检索服务：从用户文本中提取实体候选，通过码表解析为标准实体

查询路径：Redis 缓存 → PG 回源 → 写缓存
启动时可预热全量码表到 Redis。
"""

import asyncio
import re
from dataclasses import dataclass, field

import redis.asyncio as aioredis
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis_client import RedisKeys
from app.config import get_settings
from app.db.models.codebook import Codebook, normalize_alias

log = structlog.get_logger()
settings = get_settings()


@dataclass
class EntityMatch:
    """单个实体匹配结果"""

    raw: str  # 用户原始输入片段 "a100"
    alias: str  # 归一化后 "a100"
    standard_name: str  # 标准名称 "A-100"
    entity_type: str  # product/line/metric/department
    entity_meta: dict = field(default_factory=dict)  # 扩展属性
    confidence: float = 1.0  # 匹配置信度（精确匹配=1.0）


# ── 实体候选提取正则（Phase 1） ──

# 产品型号：字母开头 + 字母数字混合（如 A-100, B200, XYZ-01）
PRODUCT_PATTERN = re.compile(r"\b[A-Za-z][\w\-]{1,20}\b")
# 产线：L/P 开头 + 数字（如 L1, L2, P1, P2）
LINE_PATTERN = re.compile(r"\b[LlPp]\d{1,3}\b")
# 指标关键词
METRIC_KEYWORDS = ["良率", "不良率", "产量", "OEE", "稼动率", "直通率", "oee"]


class CodebookService:
    """码表检索服务：Redis 缓存 + PG 回源"""

    def __init__(self, redis: aioredis.Redis, db: AsyncSession):
        self.redis = redis
        self.db = db
        self.cache_ttl = settings.CODEBOOK_CACHE_TTL

    # ── 核心接口 ──

    async def extract_and_resolve(self, text: str) -> list[EntityMatch]:
        """从文本中提取实体候选并解析为标准实体（并行查询）"""
        candidates = self._extract_candidates(text)
        if not candidates:
            return []
        # 并行解析所有候选实体，避免串行 N+1 问题
        matches = await asyncio.gather(
            *(self.resolve(raw, entity_type=hint) for raw, hint in candidates)
        )
        return [m for m in matches if m is not None]

    async def resolve(
        self, raw_alias: str, entity_type: str | None = None
    ) -> EntityMatch | None:
        """单个别名解析：缓存 → PG → 写缓存"""
        alias = normalize_alias(raw_alias)
        if not alias:
            return None

        # 1. 查 Redis 缓存
        if entity_type:
            cached = await self._cache_get(entity_type, alias)
            if cached:
                return cached

        # 2. 查 PG
        match = await self._db_lookup(alias, entity_type)
        if match:
            # 写缓存
            await self._cache_set(match)
            return match

        return None

    async def warm_cache(self) -> int:
        """启动时预热码表缓存（全量加载 active 状态的码表到 Redis）"""
        result = await self.db.execute(
            select(Codebook).where(Codebook.status == "active")
        )
        # TODO(Phase 3): 数据量 >10 万时改为 yield_per 流式读取或分批处理
        rows = result.scalars().all()

        count = 0
        async with self.redis.pipeline(transaction=False) as pipe:
            for row in rows:
                cache_key = RedisKeys.codebook(row.entity_type, row.alias)
                cache_value = (
                    f"{row.standard_name}|{row.entity_type}|"
                    f"{row.alias}|{row.alias_display}"
                )
                pipe.setex(cache_key, self.cache_ttl, cache_value)
                count += 1
            await pipe.execute()

        log.info("码表缓存预热完成", count=count)
        return count

    # ── 实体候选提取 ──

    def _extract_candidates(self, text: str) -> list[tuple[str, str | None]]:
        """
        从文本中提取实体候选。
        返回 [(原始片段, 类型提示)]，类型提示可为 None。
        """
        candidates: list[tuple[str, str | None]] = []
        seen: set[str] = set()

        # 指标关键词（精确匹配）
        for kw in METRIC_KEYWORDS:
            if kw in text or kw.lower() in text.lower():
                normalized = normalize_alias(kw)
                if normalized not in seen:
                    candidates.append((kw, "metric"))
                    seen.add(normalized)

        # 产线模式
        for m in LINE_PATTERN.finditer(text):
            raw = m.group()
            normalized = normalize_alias(raw)
            if normalized not in seen:
                candidates.append((raw, "line"))
                seen.add(normalized)

        # 产品型号模式（放最后，范围最广容易误匹配）
        for m in PRODUCT_PATTERN.finditer(text):
            raw = m.group()
            normalized = normalize_alias(raw)
            # 排除常见干扰词
            if normalized in seen or len(normalized) < 2:
                continue
            if normalized in {"的", "是", "在", "了", "和", "或"}:
                continue
            candidates.append((raw, None))
            seen.add(normalized)

        return candidates

    # ── 缓存操作 ──

    async def _cache_get(self, entity_type: str, alias: str) -> EntityMatch | None:
        """从 Redis 缓存读取"""
        cache_key = RedisKeys.codebook(entity_type, alias)
        cached = await self.redis.get(cache_key)
        if not cached:
            return None
        # 格式：standard_name|entity_type|alias|alias_display
        parts = cached.split("|", 3)
        if len(parts) < 4:
            return None
        return EntityMatch(
            raw=parts[3],  # alias_display 作为 raw
            alias=parts[2],
            standard_name=parts[0],
            entity_type=parts[1],
            confidence=1.0,
        )

    async def _cache_set(self, match: EntityMatch) -> None:
        """写入 Redis 缓存"""
        cache_key = RedisKeys.codebook(match.entity_type, match.alias)
        cache_value = (
            f"{match.standard_name}|{match.entity_type}|"
            f"{match.alias}|{match.raw}"
        )
        await self.redis.setex(cache_key, self.cache_ttl, cache_value)

    # ── PG 查询 ──

    async def _db_lookup(
        self, alias: str, entity_type: str | None = None
    ) -> EntityMatch | None:
        """查 PG 码表"""
        query = select(Codebook).where(
            Codebook.alias == alias,
            Codebook.status == "active",
        )
        if entity_type:
            query = query.where(Codebook.entity_type == entity_type)

        result = await self.db.execute(query)
        row = result.scalar_one_or_none()
        if not row:
            return None

        return EntityMatch(
            raw=row.alias_display,
            alias=row.alias,
            standard_name=row.standard_name,
            entity_type=row.entity_type,
            entity_meta=row.entity_meta or {},
            confidence=1.0,
        )
