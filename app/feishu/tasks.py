"""
Feishu ARQ 任务
处理飞书消息的主任务
"""

import asyncio
import json
from datetime import datetime
from typing import Dict, List
from uuid import UUID

import structlog
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.cache.redis_client import FeishuRedisKeys, redis_client
from app.config import get_settings
from app.db.engine import async_session
from app.feishu.access_control import get_access_controller
from app.feishu.block_streaming import get_block_streaming_manager
from app.feishu.card_status_manager import (
    get_card_status_manager,
    CardStatus,
    cleanup_card_status_manager,
)
from app.feishu.client import FeishuClient, FeishuError, get_feishu_client
from app.feishu.debounce import get_debounce_manager
from app.feishu.media_downloader import get_media_downloader
from app.db.models.feishu import FeishuChatSessionMapping, FeishuMessageLogs
from app.feishu.user_resolver import get_user_resolver

settings = get_settings()
logger = structlog.get_logger()

# ARQ 队列名



async def process_feishu_message(ctx: dict, message: dict) -> dict:
    """
    处理飞书消息的ARQ任务
    
    完整处理流程:
    1. 解析消息
    2. 访问控制检查
    3. 用户身份解析
    4. 媒体文件下载
    5. Debounce防抖
    6. AI处理
    7. BlockStreaming回复
    8. 审计日志
    """
    async with async_session() as db:
        return await _process_message_internal(db, message)


async def _process_message_internal(
    db: AsyncSession,
    message: dict,
) -> dict:
    """内部消息处理逻辑"""
    
    # 1. 提取消息信息
    event_id = message.get("event_id")
    message_id = message.get("message_id")
    chat_id = message.get("chat_id")
    chat_type = message.get("chat_type")
    open_id = message.get("open_id")
    msg_type = message.get("msg_type")
    content = message.get("content", {})
    mentions = message.get("mentions", [])
    
    # 关键：提取 app_id（支持多机器人）
    app_id = message.get("app_id", "")
    if not app_id:
        logger.warning("Message missing app_id, using default", event_id=event_id)
        app_id = settings.FEISHU_APP_ID
    
    logger.info("Processing Feishu message",
               event_id=event_id,
               message_id=message_id,
               chat_type=chat_type)
    
    # ← 新增：创建卡片状态管理器
    card_status = await get_card_status_manager(
        open_id=open_id,
        chat_id=chat_id,
        app_id=app_id,
    )
    
    # ← 新增：开始卡片会话（显示"⏳ 思考中..."）
    receive_id = chat_id if chat_type == "group" else open_id
    receive_id_type = "chat_id" if chat_type == "group" else "open_id"
    
    await card_status.start_session(
        open_id=open_id,
        chat_id=chat_id,
        receive_id=receive_id,
        app_id=app_id,
        receive_id_type=receive_id_type,
    )
    
    try:
        # 2. 创建/更新审计日志
        log_entry = await _create_or_update_log(
            db, event_id, message_id, open_id, chat_id, chat_type, msg_type, content
        )
        
        # 3. 更新状态为处理中
        log_entry.status = "processing"
        log_entry.processing_started_at = datetime.utcnow()
        await db.commit()
        
        # 4. Debounce处理
        debounce_manager = get_debounce_manager()
        should_debounce = await debounce_manager.check_should_debounce(message)
        
        if should_debounce:
            result, messages = await debounce_manager.add_message(open_id, chat_id, message)
            
            if result == "buffered":
                # 消息已缓冲，稍后处理
                log_entry.status = "buffering"
                # ← 新增：更新状态为校验中
                await card_status.update_status(CardStatus.VALIDATING)
                await db.commit()
                logger.info("Message buffered",
                           event_id=event_id,
                           session=f"{open_id}:{chat_id}")
                return {"status": "buffered", "message_id": message_id}
            
            elif result == "processing":
                # 正在处理中，这条消息会被合并到当前处理批次
                # 继续执行，让 should_flush 获取合并后的消息
                logger.info("Session is processing, will merge with current batch",
                           event_id=event_id)
        
        # 5. 检查是否应该flush（Debounced消息）
        should_flush, flushed_messages = await debounce_manager.should_flush(open_id, chat_id)
        
        if should_flush and flushed_messages:
            # 使用合并后的消息
            merged_message = flushed_messages[0]
            content = merged_message.get("content", content)
            logger.info("Using merged message",
                       original_count=merged_message.get("merged_count", 1))
        
        # 6. 用户身份解析（使用消息中的 app_id 支持多机器人）
        try:
            user_resolver = get_user_resolver()
            user, employee_no, error = await user_resolver.resolve_user(
                db, open_id, app_id
            )
            
            if error:
                # 用户解析失败
                log_entry.status = "failed"
                log_entry.error_type = "user_resolution"
                log_entry.error_message = error
                await db.commit()
                
                # 发送错误提示（使用消息中的 app_id 支持多机器人）
                feishu_client = await get_feishu_client(app_id, db)
                await feishu_client.send_text_message(
                    receive_id=open_id,
                    text=f"❌ {error}"
                )
                
                return {"status": "failed", "error": error}
        except FeishuError as e:
            # 飞书客户端配置错误
            error_msg = str(e)
            log_entry.status = "failed"
            log_entry.error_type = "feishu_config_error"
            log_entry.error_message = error_msg
            await db.commit()
            
            logger.error("Feishu configuration error", 
                        error=error_msg,
                        app_id=app_id,
                        event_id=event_id)
            
            # 给用户发送友好的配置错误提示
            user_friendly_msg = (
                "⚠️ 机器人配置异常\n"
                "\n"
                "抱歉，暂时无法为您服务。可能的原因：\n"
                "• 机器人应用未正确配置\n"
                "• 应用凭证缺失或过期\n"
                "\n"
                "请联系管理员检查：\n"
                f"• App ID: {app_id or '未设置'}\n"
                "• 应用配置是否正确入库\n"
                "\n"
                "调试信息: " + error_msg[:100]
            )
            
            # 尝试发送错误提示给用户
            # 由于配置错误，尝试使用默认配置创建客户端发送错误提示
            try:
                # 尝试使用默认应用配置
                default_client = FeishuClient(
                    app_id=settings.FEISHU_APP_ID,
                    app_secret=settings.FEISHU_APP_SECRET
                )
                await default_client.send_text_message(
                    receive_id=open_id,
                    text=user_friendly_msg
                )
                logger.info("Sent error message to user using default config")
            except Exception as send_error:
                logger.error("Failed to send error message to user",
                           error=str(send_error),
                           note="Default config may also be invalid")
            
            return {"status": "failed", "error": error_msg}
        
        # 更新日志用户信息
        log_entry.employee_no = employee_no
        log_entry.user_id = user.id if user else None
        
        # 7. 访问控制检查
        access_controller = get_access_controller()
        
        if chat_type == "p2p":
            allowed, reason = await access_controller.check_dm_access(
                db, app_id, employee_no
            )
        else:
            has_mention = len(mentions) > 0
            allowed, reason = await access_controller.check_group_access(
                db, app_id, chat_id, employee_no, has_mention
            )
        
        if not allowed:
            log_entry.status = "rejected"
            log_entry.error_type = "access_denied"
            log_entry.error_message = reason
            await db.commit()
            
            # ← 新增：拒绝时也显示状态
            await card_status.update_status(CardStatus.VALIDATING)
            await asyncio.sleep(0.5)  # 短暂显示校验状态
            
            # 发送拒绝提示
            feishu_client = await get_feishu_client(app_id, db)
            rejection_msg = access_controller.get_rejection_message(reason)
            await feishu_client.send_text_message(
                receive_id=chat_id if chat_type == "group" else open_id,
                text=rejection_msg,
                receive_id_type="chat_id" if chat_type == "group" else "open_id"
            )
            
            # ← 清理状态管理器
            await cleanup_card_status_manager(open_id=open_id, chat_id=chat_id, app_id=app_id)
            
            return {"status": "rejected", "reason": reason}
        
        # 8. 获取会话映射
        session_id = await _get_or_create_session(db, open_id, chat_id, chat_type, user.id if user else None)
        
        # 9. 媒体文件下载
        media_paths = []
        if msg_type in ["image", "file", "audio", "media", "sticker"]:
            media_downloader = get_media_downloader()
            
            # 提取文件信息
            file_key = content.get("file_key")
            file_name = content.get("file_name", "unknown")
            
            if file_key:
                media_file = await media_downloader.download_with_retry(
                    db=db,
                    file_key=file_key,
                    message_id=message_id,
                    file_name=file_name,
                    file_type=msg_type,
                    user_id=user.id if user else open_id,  # 使用系统用户ID（UUID）
                    open_id=open_id,  # 保留open_id用于记录
                    chat_id=chat_id,
                )
                
                if media_file and media_file.download_status == "completed":
                    media_paths.append(media_file.local_path)
        
        # 10. 提取文本内容
        text_content = _extract_text_content(content, msg_type)
        
        if not text_content and not media_paths:
            log_entry.status = "completed"
            log_entry.reply_content = "未检测到有效内容"
            await db.commit()
            return {"status": "completed", "message": "No content"}
        
        # 11. AI处理 + BlockStreaming回复
        # 获取 BlockStreaming 配置
        from sqlalchemy import select
        from app.db.models.feishu import FeishuAccessConfig
        
        bs_config = None
        try:
            stmt = select(FeishuAccessConfig).where(
                FeishuAccessConfig.app_id == app_id,
                FeishuAccessConfig.is_active == True
            )
            result = await db.execute(stmt)
            access_config = result.scalar_one_or_none()
            if access_config and access_config.block_streaming_config:
                bs_config = access_config.block_streaming_config
        except Exception as e:
            logger.warning("Failed to load block_streaming_config", error=str(e))
        
        # ← 修改：使用 CardStatusManager 统一管理状态和 BlockStreaming
        # 获取接收 ID
        receive_id = chat_id if chat_type == "group" else open_id
        receive_id_type = "chat_id" if chat_type == "group" else "open_id"
        
        # ← 更新状态为"生成答案中"
        await card_status.update_status(CardStatus.GENERATING)
        
        # 11. AI处理 - 使用流式版本（支持媒体文件）
        from app.execution.pipeline import run_agent_pipeline_streaming
        
        reply_text, session_id = await run_agent_pipeline_streaming(
            usernumb=employee_no,
            user_id=str(user.id) if user else "",
            input_text=text_content,
            session_id=session_id,
            source="feishu",
            media_paths=media_paths if media_paths else None,
            feishu_chat_id=chat_id,  # ← 新增：飞书会话 ID
            feishu_open_id=open_id,  # ← 新增：飞书用户 ID
            feishu_chat_type=chat_type,  # ← 新增：飞书会话类型
        )
        
        # 12. 人机延迟 - 模拟人类回复节奏
        # 默认配置（写死在代码中）
        DEFAULT_HUMAN_LIKE_DELAY = {
            "enabled": True,
            "min_ms": 500,
            "max_ms": 1500
        }
        
        # 优先使用数据库配置，如无则使用默认配置
        human_delay_config = DEFAULT_HUMAN_LIKE_DELAY
        if access_config and access_config.human_like_delay:
            # 数据库配置可以覆盖默认配置
            human_delay_config = {**DEFAULT_HUMAN_LIKE_DELAY, **access_config.human_like_delay}
        
        # if human_delay_config.get("enabled", True):
        #     import random
        #     min_ms = human_delay_config.get("min_ms", 500)
        #     max_ms = human_delay_config.get("max_ms", 1500)
        #     delay_ms = random.randint(min_ms, max_ms)
        #     logger.info("Applying human-like delay", delay_ms=delay_ms)
        #     await asyncio.sleep(delay_ms / 1000)
        
        # 13. BlockStreaming 流式累积和发送
        # ← 修改：使用 CardStatusManager 统一更新卡片内容
        import re
        
        # 先按段落分割
        paragraphs = re.split(r'(?<=\n\n)|(?<=\n)', reply_text)
        
        for para in paragraphs:
            if not para.strip():
                continue
            
            # ← 使用 CardStatusManager 更新卡片内容（显示生成的文本）
            await card_status.update_card_content(para)
        
        # 关闭流式卡片，发送剩余内容并清除状态
        await card_status.complete(
            final_answer=reply_text,
            send_as_message=False,  # 更新到同一张卡片，不另发消息
        )
        
        # ← 清理状态管理器
        await cleanup_card_status_manager(
            open_id=open_id,
            chat_id=chat_id,
            app_id=app_id,
        )
        
        # 13. 完成处理
        processing_end = datetime.utcnow()
        log_entry.status = "completed"
        log_entry.processing_completed_at = processing_end
        log_entry.processing_duration_ms = int(
            (processing_end - log_entry.processing_started_at).total_seconds() * 1000
        )
        log_entry.reply_content = reply_text
        await db.commit()
        
        # 14. 完成Debounce处理
        await debounce_manager.complete_processing(open_id, chat_id)
        
        logger.info("Message processing completed",
                   event_id=event_id,
                   duration_ms=log_entry.processing_duration_ms)
        
        return {
            "status": "completed",
            "message_id": message_id,
            "reply_length": len(reply_text),
        }
        
    except Exception as e:
        logger.error("Message processing failed",
                    event_id=event_id,
                    error=str(e),
                    exc_info=True)
        
        # ← 新增：设置错误状态
        if card_status:
            await card_status.set_error(str(e))
            await cleanup_card_status_manager(
                open_id=open_id,
                chat_id=chat_id,
                app_id=app_id,
            )
        
        log_entry.status = "failed"
        log_entry.error_type = "processing_error"
        log_entry.error_message = str(e)
        await db.commit()
        
        raise


async def _create_or_update_log(
    db: AsyncSession,
    event_id: str,
    message_id: str,
    open_id: str,
    chat_id: str,
    chat_type: str,
    msg_type: str,
    content: dict,
) -> FeishuMessageLogs:
    """创建或更新审计日志"""
    
    # 检查是否已存在
    from sqlalchemy import select
    result = await db.execute(
        select(FeishuMessageLogs).where(
            FeishuMessageLogs.event_id == event_id
        )
    )
    existing = result.scalar_one_or_none()
    
    if existing:
        return existing
    
    # 创建新日志
    log_entry = FeishuMessageLogs(
        id=uuid7(),
        event_id=event_id,
        message_id=message_id,
        open_id=open_id,
        chat_id=chat_id,
        chat_type=chat_type,
        msg_type=msg_type,
        content=content,
        content_text=_extract_text_content(content, msg_type),
        status="received",
    )
    
    db.add(log_entry)
    await db.commit()
    await db.refresh(log_entry)
    
    return log_entry


async def _get_or_create_session(
    db: AsyncSession,
    open_id: str,
    chat_id: str,
    chat_type: str,
    user_id: UUID = None,
) -> str:
    """获取或创建会话映射"""
    from sqlalchemy import select
    
    result = await db.execute(
        select(FeishuChatSessionMapping).where(
            FeishuChatSessionMapping.chat_id == chat_id,
            FeishuChatSessionMapping.open_id == open_id,
        )
    )
    mapping = result.scalar_one_or_none()
    
    if mapping:
        # 更新活跃时间
        mapping.last_active_at = datetime.utcnow()
        mapping.message_count += 1
        await db.commit()
        return mapping.session_id
    
    # 创建新会话
    # session_id = str(uuid7())
    # new_mapping = FeishuChatSessionMapping(
    #     id=uuid7(),
    #     chat_id=chat_id,
    #     open_id=open_id,
    #     session_id=session_id,
    #     chat_type=chat_type,
    #     user_id=user_id,
    #     message_count=1,
    # )
    
    # db.add(new_mapping)
    # await db.commit()
    
    return None


def _extract_text_content(content: dict, msg_type: str) -> str:
    """提取文本内容"""
    if msg_type == "text":
        text = content.get("text", "")
        if isinstance(text, str):
            # 移除@提及
            import re
            text = re.sub(r'@_user_\d+', '', text)
            return text.strip()
    
    return ""


async def _startup(ctx):
    """Worker启动钩子（内部实现）"""
    logger.info("Feishu Worker starting up")
    
    # 预初始化执行管线（避免请求时延迟）
    # 这会触发 pipeline.py 模块级初始化：
    # - LLMClient 创建
    # - ExecutionRouter 初始化（工具注册、SubAgent 加载）
    logger.info("Pre-initializing execution pipeline...")
    import time
    start_time = time.time()
    
    from app.execution import pipeline
    # 触发模块级单例初始化
    _ = pipeline._execution_router
    _ = pipeline._llm_client
    
    elapsed_ms = int((time.time() - start_time) * 1000)
    logger.info(f"Execution pipeline pre-initialized", elapsed_ms=elapsed_ms)
    
    # 启动消息转移循环
    ctx["message_transfer_task"] = asyncio.create_task(
        message_transfer_loop()
    )
    
    # 启动防抖扫描器
    from app.feishu.debounce import get_debounce_scanner
    scanner = get_debounce_scanner()
    ctx["debounce_scanner_task"] = asyncio.create_task(
        scanner.start()
    )


async def _shutdown(ctx):
    """Worker关闭钩子（内部实现）"""
    logger.info("Feishu Worker shutting down")
    
    # 取消任务
    if "message_transfer_task" in ctx:
        ctx["message_transfer_task"].cancel()
    
    if "debounce_scanner_task" in ctx:
        from app.feishu.debounce import get_debounce_scanner
        scanner = get_debounce_scanner()
        await scanner.stop()
    
    # 关闭所有 FeishuClient 实例（多应用支持）
    from app.feishu.client import close_all_feishu_clients
    await close_all_feishu_clients()


# ARQ Worker 配置
class WorkerSettings:
    """ARQ Worker配置"""
    
    functions = [process_feishu_message]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    queue_name = FeishuRedisKeys.ARQ_QUEUE
    
    max_jobs = 10
    job_timeout = 300  # 5分钟
    retry_jobs = True
    max_tries = 3
    keep_result = 3600  # 1小时
    
    on_startup = _startup
    on_shutdown = _shutdown


# 保持向后兼容的导出
startup = _startup
shutdown = _shutdown


async def message_transfer_loop():
    """
    消息转移循环（桥接器）
    
    从外部 Webhook 服务的 Redis List 消费消息
    转移到 ARQ 队列进行处理
    
    外部 Webhook 服务信息：
    - 项目: feishu-sunnyagent-api
    - URL: https://larkchannel.51dnbsc.top/webhook
    - 推送队列: Redis List "feishu:webhook:queue"
    
    工作流程：
    1. 使用 BRPOP 从 feishu:webhook:queue 阻塞读取消息
    2. 消息推送到 processing:queue（用于故障恢复）
    3. 幂等校验（防止重复处理）
    4. 入队到 ARQ 队列
    5. 从 processing:queue 删除（确认处理）
    """
    logger.info("Message transfer loop started",
               source_queue=FeishuRedisKeys.EXTERNAL_WEBHOOK_QUEUE,
               target_queue=FeishuRedisKeys.ARQ_QUEUE)
    
    while True:
        try:
            # 使用 BRPOP 从外部队列阻塞读取消息
            # timeout=1 秒，便于优雅退出
            result = await redis_client.brpop(
                FeishuRedisKeys.EXTERNAL_WEBHOOK_QUEUE,
                timeout=1
            )
            
            if not result:
                continue
            
            # result 是 (queue_name, message_data) 元组
            queue_name, message_json = result
            
            # 解析消息
            try:
                # 外部服务推送的是飞书原始事件格式
                feishu_event = json.loads(message_json)
                
                # 转换为内部标准格式
                message = _convert_feishu_event_to_message(feishu_event)
                
                event_id = message.get("event_id")
                message_id = message.get("message_id")
                
                # 推送到 processing 队列（用于可靠传输）
                await redis_client.lpush(FeishuRedisKeys.PROCESSING_QUEUE, message_json)
                
                # 幂等校验
                processed_key = FeishuRedisKeys.processed(event_id, message_id)
                already_processed = await redis_client.exists(processed_key)
                
                if already_processed:
                    logger.info("Message already processed, skipping",
                               event_id=event_id,
                               message_id=message_id)
                    # 从 processing 队列移除
                    await redis_client.lrem(FeishuRedisKeys.PROCESSING_QUEUE, 0, message_json)
                    continue
                
                # 标记为已处理（24小时TTL）
                await redis_client.setex(processed_key, 86400, "1")
                
                # 入队到 ARQ
                arq_pool = await create_pool(WorkerSettings.redis_settings)
                await arq_pool.enqueue_job(
                    "process_feishu_message",
                    message,
                    _queue_name=FeishuRedisKeys.ARQ_QUEUE,
                )
                await arq_pool.close()
                
                # 从 processing 队列移除（确认已入队）
                await redis_client.lrem(FeishuRedisKeys.PROCESSING_QUEUE, 0, message_json)
                
                logger.info("Message transferred to ARQ queue",
                           event_id=event_id,
                           message_id=message_id)
                
            except json.JSONDecodeError as e:
                logger.error("Failed to parse message JSON",
                            error=str(e),
                            message_preview=message_json[:200] if len(message_json) > 200 else message_json)
                # 解析失败的消息，从 processing 队列移除（避免无限重试）
                await redis_client.lrem(FeishuRedisKeys.PROCESSING_QUEUE, 0, message_json)
                
            except Exception as e:
                logger.error("Failed to process message",
                            error=str(e),
                            event_id=event_id if 'event_id' in locals() else "unknown")
                # 处理失败，保留在 processing 队列中，稍后重试
                # 注意：这里不会无限重试，因为每次重启会清理 processing 队列
                        
        except asyncio.CancelledError:
            logger.info("Message transfer loop cancelled")
            break
        except Exception as e:
            logger.error("Message transfer error", error=str(e))
            await asyncio.sleep(0.1)  # 短暂休息避免忙循环


def _convert_feishu_event_to_message(feishu_event: dict) -> dict:
    """
    将飞书原始事件格式转换为内部标准消息格式
    
    飞书事件格式示例（schema 2.0）：
    {
        "schema": "2.0",
        "header": {
            "event_id": "556857e248f93e03a71fa1fad3aa5dbe",
            "token": "...",
            "create_time": "1234567890",
            ...
        },
        "event": {
            "message": {
                "message_id": "om_12345",
                "chat_type": "p2p",
                "chat_id": "oc_12345",
                "sender": {
                    "sender_id": {
                        "open_id": "ou_12345"
                    }
                },
                "message_type": "text",
                "content": '{"text": "hello"}',
                "mentions": []
            }
        }
    }
    """
    header = feishu_event.get("header", {})
    event = feishu_event.get("event", {})
    message = event.get("message", {})
    sender = event.get("sender", {})  # sender 在 event 下，不是 message 下
    sender_id = sender.get("sender_id", {})
    
    # 解析 content（飞书的 content 是 JSON 字符串）
    content_str = message.get("content", "{}")
    try:
        content = json.loads(content_str)
    except json.JSONDecodeError:
        content = {"text": content_str}
    
    # 解析 mentions
    mentions = message.get("mentions", [])
    
    # 提取 app_id（支持多机器人）
    app_id = header.get("app_id", "")
    
    return {
        "event_id": header.get("event_id", ""),
        "message_id": message.get("message_id", ""),
        "event_type": header.get("event_type", ""),
        "app_id": app_id,  # ← 关键：提取 app_id 支持多机器人
        "open_id": sender_id.get("open_id", ""),
        "chat_id": message.get("chat_id", ""),
        "chat_type": message.get("chat_type", "p2p"),
        "msg_type": message.get("message_type", "text"),  # 飞书字段名是 message_type
        "content": content,
        "mentions": mentions,
        "create_time": header.get("create_time", ""),
        # 保留原始事件，便于调试
        "_raw_event": feishu_event,
    }
