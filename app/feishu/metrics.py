"""
Metrics collection for Feishu integration
"""
from prometheus_client import Counter, Histogram, Gauge, Info
from functools import wraps
import time
from typing import Callable, Any

# Message processing metrics
messages_processed = Counter(
    'feishu_messages_processed_total',
    'Total number of messages processed',
    ['app_id', 'status', 'message_type']
)

messages_dropped = Counter(
    'feishu_messages_dropped_total',
    'Total number of messages dropped',
    ['app_id', 'reason']
)

message_processing_duration = Histogram(
    'feishu_message_processing_duration_seconds',
    'Time spent processing messages',
    ['app_id', 'stage'],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

# API call metrics
api_requests = Counter(
    'feishu_api_requests_total',
    'Total number of API requests',
    ['app_id', 'endpoint', 'status']
)

api_request_duration = Histogram(
    'feishu_api_request_duration_seconds',
    'Time spent on API requests',
    ['app_id', 'endpoint'],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5]
)

# Token management metrics
token_refreshes = Counter(
    'feishu_token_refreshes_total',
    'Total number of token refreshes',
    ['app_id', 'status']
)

token_cache_hits = Counter(
    'feishu_token_cache_hits_total',
    'Total number of token cache hits',
    ['app_id']
)

# Queue metrics
queue_depth = Gauge(
    'feishu_queue_depth',
    'Current depth of the message queue',
    ['queue_name']
)

messages_in_queue = Counter(
    'feishu_messages_in_queue_total',
    'Total number of messages added to queue',
    ['app_id']
)

# Debounce metrics
duplicate_messages = Counter(
    'feishu_duplicate_messages_total',
    'Total number of duplicate messages detected',
    ['app_id']
)

debounce_operations = Counter(
    'feishu_debounce_operations_total',
    'Total number of debounce operations',
    ['app_id', 'operation']
)

# User resolution metrics
user_resolutions = Counter(
    'feishu_user_resolutions_total',
    'Total number of user resolutions',
    ['app_id', 'source', 'status']
)

user_resolution_duration = Histogram(
    'feishu_user_resolution_duration_seconds',
    'Time spent resolving users',
    ['app_id', 'source'],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5]
)

# Media download metrics
media_downloads = Counter(
    'feishu_media_downloads_total',
    'Total number of media downloads',
    ['app_id', 'file_type', 'status']
)

media_download_size = Histogram(
    'feishu_media_download_size_bytes',
    'Size of downloaded media files',
    ['app_id', 'file_type'],
    buckets=[1024, 10240, 102400, 1048576, 10485760, 52428800, 104857600]
)

media_deduplications = Counter(
    'feishu_media_deduplications_total',
    'Total number of media deduplications',
    ['app_id']
)

# Streaming metrics
streaming_updates = Counter(
    'feishu_streaming_updates_total',
    'Total number of streaming card updates',
    ['app_id', 'status']
)

streaming_characters = Counter(
    'feishu_streaming_characters_total',
    'Total number of characters streamed',
    ['app_id']
)

# Worker metrics
worker_info = Info(
    'feishu_worker',
    'Feishu worker information'
)

active_workers = Gauge(
    'feishu_active_workers',
    'Number of active worker processes',
    ['app_id']
)

worker_jobs = Counter(
    'feishu_worker_jobs_total',
    'Total number of jobs processed by workers',
    ['app_id', 'status']
)

# Access control metrics
access_checks = Counter(
    'feishu_access_checks_total',
    'Total number of access checks',
    ['app_id', 'decision']
)

blocked_users = Counter(
    'feishu_blocked_users_total',
    'Total number of blocked user attempts',
    ['app_id', 'reason']
)


class FeishuMetrics:
    """Helper class for recording Feishu metrics"""
    
    @staticmethod
    def record_message_processed(app_id: str, status: str, message_type: str = "text"):
        """Record a processed message"""
        messages_processed.labels(
            app_id=app_id,
            status=status,
            message_type=message_type
        ).inc()
    
    @staticmethod
    def record_message_dropped(app_id: str, reason: str):
        """Record a dropped message"""
        messages_dropped.labels(
            app_id=app_id,
            reason=reason
        ).inc()
    
    @staticmethod
    def time_message_processing(app_id: str, stage: str):
        """Context manager to time message processing stages"""
        return message_processing_duration.labels(
            app_id=app_id,
            stage=stage
        ).time()
    
    @staticmethod
    def record_api_request(app_id: str, endpoint: str, status: str, duration: float):
        """Record an API request"""
        api_requests.labels(
            app_id=app_id,
            endpoint=endpoint,
            status=status
        ).inc()
        api_request_duration.labels(
            app_id=app_id,
            endpoint=endpoint
        ).observe(duration)
    
    @staticmethod
    def record_token_refresh(app_id: str, success: bool):
        """Record a token refresh"""
        token_refreshes.labels(
            app_id=app_id,
            status="success" if success else "failure"
        ).inc()
    
    @staticmethod
    def record_token_cache_hit(app_id: str):
        """Record a token cache hit"""
        token_cache_hits.labels(app_id=app_id).inc()
    
    @staticmethod
    def set_queue_depth(queue_name: str, depth: int):
        """Set the current queue depth"""
        queue_depth.labels(queue_name=queue_name).set(depth)
    
    @staticmethod
    def record_duplicate_message(app_id: str):
        """Record a duplicate message detection"""
        duplicate_messages.labels(app_id=app_id).inc()
    
    @staticmethod
    def record_user_resolution(app_id: str, source: str, success: bool, duration: float):
        """Record a user resolution"""
        user_resolutions.labels(
            app_id=app_id,
            source=source,
            status="success" if success else "failure"
        ).inc()
        user_resolution_duration.labels(
            app_id=app_id,
            source=source
        ).observe(duration)
    
    @staticmethod
    def record_media_download(app_id: str, file_type: str, success: bool, size_bytes: int):
        """Record a media download"""
        media_downloads.labels(
            app_id=app_id,
            file_type=file_type,
            status="success" if success else "failure"
        ).inc()
        if success:
            media_download_size.labels(
                app_id=app_id,
                file_type=file_type
            ).observe(size_bytes)
    
    @staticmethod
    def record_media_deduplication(app_id: str):
        """Record a media deduplication"""
        media_deduplications.labels(app_id=app_id).inc()
    
    @staticmethod
    def record_streaming_update(app_id: str, success: bool, characters: int = 0):
        """Record a streaming update"""
        streaming_updates.labels(
            app_id=app_id,
            status="success" if success else "failure"
        ).inc()
        if characters > 0:
            streaming_characters.labels(app_id=app_id).inc(characters)
    
    @staticmethod
    def record_access_check(app_id: str, allowed: bool):
        """Record an access check"""
        access_checks.labels(
            app_id=app_id,
            decision="allowed" if allowed else "denied"
        ).inc()
    
    @staticmethod
    def record_blocked_user(app_id: str, reason: str):
        """Record a blocked user attempt"""
        blocked_users.labels(
            app_id=app_id,
            reason=reason
        ).inc()
    
    @staticmethod
    def set_active_workers(app_id: str, count: int):
        """Set the number of active workers"""
        active_workers.labels(app_id=app_id).set(count)
    
    @staticmethod
    def record_worker_job(app_id: str, success: bool):
        """Record a worker job completion"""
        worker_jobs.labels(
            app_id=app_id,
            status="success" if success else "failure"
        ).inc()


def timed(stage: str, app_id_getter: Callable[[Any], str] = None):
    """Decorator to time function execution and record metrics"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Get app_id if a getter is provided
            app_id = "unknown"
            if app_id_getter:
                try:
                    app_id = app_id_getter(*args, **kwargs)
                except:
                    pass
            
            start_time = time.time()
            try:
                with message_processing_duration.labels(
                    app_id=app_id,
                    stage=stage
                ).time():
                    result = await func(*args, **kwargs)
                return result
            except Exception as e:
                raise e
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            app_id = "unknown"
            if app_id_getter:
                try:
                    app_id = app_id_getter(*args, **kwargs)
                except:
                    pass
            
            with message_processing_duration.labels(
                app_id=app_id,
                stage=stage
            ).time():
                return func(*args, **kwargs)
        
        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator


import asyncio