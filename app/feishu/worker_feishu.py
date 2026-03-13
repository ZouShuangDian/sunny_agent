"""
Feishu Worker 独立配置
独立的ARQ Worker，专门处理飞书消息队列
"""

from app.config import get_settings
from app.feishu.tasks import (
    WorkerSettings as FeishuWorkerSettings,
    process_feishu_message,
    startup,
    shutdown,
)

settings = get_settings()


class WorkerSettingsFeishu:
    """Feishu独立Worker配置"""
    
    # ARQ函数列表
    functions = [process_feishu_message]
    
    # Redis设置
    redis_settings = FeishuWorkerSettings.redis_settings
    
    # 独立队列名
    queue_name = "arq:feishu:queue"
    
    # Worker参数（针对飞书场景优化）
    max_jobs = 10              # 并发处理数
    job_timeout = 300          # 单任务超时（5分钟）
    retry_jobs = True
    max_tries = 3
    keep_result = 3600         # 结果保留1小时（用于审计追踪）
    
    # 启动/关闭钩子
    on_startup = startup
    on_shutdown = shutdown
    
    # 不使用Cron Jobs（使用长驻任务替代）
    cron_jobs = []


# 导出配置，供arq命令使用
WorkerSettings = WorkerSettingsFeishu