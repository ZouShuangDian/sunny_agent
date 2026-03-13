"""
Health check endpoints for Feishu integration
"""
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import datetime
import asyncio
import httpx

from app.cache.redis_client import redis
from app.config import settings
from app.feishu.client import FeishuClientManager

router = APIRouter(prefix="/health/feishu", tags=["feishu-health"])


class HealthStatus(BaseModel):
    """Health check response model"""
    status: str
    timestamp: str
    checks: Dict[str, Dict]
    
    
class ComponentHealth(BaseModel):
    """Individual component health status"""
    status: str  # healthy, degraded, unhealthy
    latency_ms: Optional[float] = None
    message: Optional[str] = None
    details: Optional[Dict] = None


async def check_redis_health() -> ComponentHealth:
    """Check Redis connection health"""
    start_time = datetime.now()
    try:
        # Test Redis connection with a ping
        await redis.ping()
        latency = (datetime.now() - start_time).total_seconds() * 1000
        
        # Get queue depths
        queue_depth = await redis.llen("feishu:webhook:queue")
        debounce_keys = len(await redis.keys("feishu:debounce:*"))
        
        return ComponentHealth(
            status="healthy",
            latency_ms=latency,
            message="Redis connection OK",
            details={
                "queue_depth": queue_depth,
                "active_debounce_keys": debounce_keys
            }
        )
    except Exception as e:
        latency = (datetime.now() - start_time).total_seconds() * 1000
        return ComponentHealth(
            status="unhealthy",
            latency_ms=latency,
            message=f"Redis connection failed: {str(e)}"
        )


async def check_database_health() -> ComponentHealth:
    """Check database connection health"""
    start_time = datetime.now()
    try:
        from app.db.session import async_session
        
        async with async_session() as session:
            # Simple query to test connection
            result = await session.execute("SELECT 1")
            await result.scalar()
        
        latency = (datetime.now() - start_time).total_seconds() * 1000
        return ComponentHealth(
            status="healthy",
            latency_ms=latency,
            message="Database connection OK"
        )
    except Exception as e:
        latency = (datetime.now() - start_time).total_seconds() * 1000
        return ComponentHealth(
            status="unhealthy",
            latency_ms=latency,
            message=f"Database connection failed: {str(e)}"
        )


async def check_feishu_api_health() -> ComponentHealth:
    """Check Feishu API connectivity"""
    start_time = datetime.now()
    try:
        # Check if we have any apps configured
        if not hasattr(settings, 'FEISHU_APPS') or not settings.FEISHU_APPS:
            return ComponentHealth(
                status="degraded",
                message="No Feishu apps configured"
            )
        
        # Try to get a token for the first app
        apps_config = settings.FEISHU_APPS
        if isinstance(apps_config, dict) and len(apps_config) > 0:
            app_id = list(apps_config.keys())[0]
            manager = FeishuClientManager(apps_config)
            client = manager.get_client(app_id)
            
            # Try to get access token
            await client.get_access_token()
            
            latency = (datetime.now() - start_time).total_seconds() * 1000
            return ComponentHealth(
                status="healthy",
                latency_ms=latency,
                message=f"Feishu API connection OK for app {app_id}",
                details={"apps_configured": len(apps_config)}
            )
        
        return ComponentHealth(
            status="degraded",
            message="Feishu apps configuration invalid"
        )
    except Exception as e:
        latency = (datetime.now() - start_time).total_seconds() * 1000
        return ComponentHealth(
            status="unhealthy",
            latency_ms=latency,
            message=f"Feishu API connection failed: {str(e)}"
        )


async def check_worker_health() -> ComponentHealth:
    """Check Feishu worker status"""
    try:
        # Check for worker heartbeat in Redis
        worker_heartbeat = await redis.get("feishu:worker:heartbeat")
        
        if worker_heartbeat:
            last_heartbeat = datetime.fromtimestamp(float(worker_heartbeat))
            seconds_since_heartbeat = (datetime.now() - last_heartbeat).total_seconds()
            
            if seconds_since_heartbeat < 60:
                return ComponentHealth(
                    status="healthy",
                    message=f"Worker active (last heartbeat {seconds_since_heartbeat:.1f}s ago)"
                )
            else:
                return ComponentHealth(
                    status="degraded",
                    message=f"Worker heartbeat stale ({seconds_since_heartbeat:.1f}s ago)"
                )
        else:
            return ComponentHealth(
                status="degraded",
                message="No worker heartbeat found"
            )
    except Exception as e:
        return ComponentHealth(
            status="unhealthy",
            message=f"Worker health check failed: {str(e)}"
        )


@router.get("", response_model=HealthStatus)
async def health_check():
    """
    Comprehensive health check for Feishu integration
    
    Returns the overall health status and individual component checks.
    """
    # Run all health checks concurrently
    results = await asyncio.gather(
        check_redis_health(),
        check_database_health(),
        check_feishu_api_health(),
        check_worker_health(),
        return_exceptions=True
    )
    
    checks = {
        "redis": results[0] if not isinstance(results[0], Exception) else ComponentHealth(
            status="unhealthy",
            message=f"Check failed: {str(results[0])}"
        ),
        "database": results[1] if not isinstance(results[1], Exception) else ComponentHealth(
            status="unhealthy",
            message=f"Check failed: {str(results[1])}"
        ),
        "feishu_api": results[2] if not isinstance(results[2], Exception) else ComponentHealth(
            status="unhealthy",
            message=f"Check failed: {str(results[2])}"
        ),
        "worker": results[3] if not isinstance(results[3], Exception) else ComponentHealth(
            status="unhealthy",
            message=f"Check failed: {str(results[3])}"
        )
    }
    
    # Determine overall status
    statuses = [check.status for check in checks.values()]
    if all(s == "healthy" for s in statuses):
        overall_status = "healthy"
    elif any(s == "unhealthy" for s in statuses):
        overall_status = "unhealthy"
    else:
        overall_status = "degraded"
    
    # Convert ComponentHealth objects to dicts for JSON serialization
    checks_dict = {
        name: {
            "status": check.status,
            "latency_ms": check.latency_ms,
            "message": check.message,
            "details": check.details
        }
        for name, check in checks.items()
    }
    
    response = HealthStatus(
        status=overall_status,
        timestamp=datetime.utcnow().isoformat(),
        checks=checks_dict
    )
    
    if overall_status == "unhealthy":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=response.dict()
        )
    
    return response


@router.get("/ready")
async def readiness_check():
    """
    Kubernetes-style readiness probe
    
    Returns 200 if the service is ready to accept traffic.
    """
    checks = await asyncio.gather(
        check_redis_health(),
        check_database_health(),
        return_exceptions=True
    )
    
    redis_ok = not isinstance(checks[0], Exception) and checks[0].status == "healthy"
    db_ok = not isinstance(checks[1], Exception) and checks[1].status == "healthy"
    
    if redis_ok and db_ok:
        return {"status": "ready"}
    
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"status": "not_ready", "redis": redis_ok, "database": db_ok}
    )


@router.get("/live")
async def liveness_check():
    """
    Kubernetes-style liveness probe
    
    Returns 200 if the service is running (basic check).
    """
    return {"status": "alive"}


@router.get("/metrics")
async def metrics_endpoint():
    """
    Prometheus metrics endpoint for Feishu integration
    
    Returns metrics in Prometheus exposition format.
    """
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


from fastapi import Response