"""
Feishu ARQ 任务
处理飞书消息的主任务
"""

import asyncio
import time
from datetime import datetime
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.config import get_settings
from app.db.engine import async_session
from app.feishu.access_control import get_access_controller
from app.feishu.card_status_manager import get_card_status_manager
from app.feishu.client import FeishuClient, FeishuError, get_feishu_client

from app.feishu.media_downloader import get_media_downloader
from app.feishu.context_manager import get_media_context_manager
from app.db.models.feishu import FeishuMessageLogs
from app.feishu.user_resolver import get_user_resolver
from app.feishu.session_mapping_service import get_or_rotate_feishu_session
from app.security.rate_limiter import rate_limiter
from app.execution.pipeline import run_agent_pipeline_stream

settings = get_settings()
logger = structlog.get_logger()


async def _send_rejected_card(
    app_id: str,
    message_id: str,
    reason: str,
) -> None:
    """发送限流拒绝回复"""
    feishu_client = await get_feishu_client(app_id)
    
    text = (
        "🤖 主人，您的消息来得太快啦！\n\n"
        "⏳ 请稍等片刻再重试，让我喘口气～"
    )
    
    await feishu_client.reply_message(
        message_id=message_id,
        text=text,
        reply_in_thread=False,
    )


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

    log_entry = None
    processing_message_id = message_id
    incoming_message_id = message_id
    
    logger.info("Processing Feishu message",
               event_id=event_id,
               message_id=message_id,
               chat_type=chat_type)
    
    try:
        receive_id = chat_id if chat_type == "group" else open_id
        receive_id_type = "chat_id" if chat_type == "group" else "open_id"

        progress_client = await get_feishu_client(app_id, db)
        last_progress_text: str | None = None
        last_progress_sent_at = 0.0

        async def _send_progress_text(text_message: str, *, min_interval_seconds: float = 6.0) -> None:
            nonlocal last_progress_text, last_progress_sent_at
            now = time.monotonic()
            if text_message == last_progress_text and (now - last_progress_sent_at) < min_interval_seconds:
                return
            await progress_client.reply_message(
                message_id=incoming_message_id,
                text=text_message,
                reply_in_thread=False,
            )
            last_progress_text = text_message
            last_progress_sent_at = now

        def _build_generation_preview(full_text: str, limit: int = 240) -> str:
            compact = full_text.strip()
            if len(compact) <= limit:
                return compact
            return compact[-limit:]
        try:
            allowed, reason = await rate_limiter.check_rate_limit(app_id, open_id, message_id, chat_id, msg_type)
        except Exception as e:
            logger.error("Rate limiter check failed", error=str(e), app_id=app_id, open_id=open_id)
            allowed, reason = True, "ok"  # 限流检查失败时放行

        if not allowed:
            try:
                await _send_rejected_card(app_id, message_id, reason)
            except Exception as e:
                logger.warning("Failed to send rejected card", error=str(e))

            return {"status": "rejected", "error": "rate_limit_exceeded", "reason": reason}

        try:
            await rate_limiter.increment_rpm(app_id, open_id)
        except Exception as e:
            logger.error("Failed to increment RPM", error=str(e))

        # 开始处理
        await rate_limiter.start_processing(app_id, open_id, message_id, chat_id)
        processing_message_id = message_id  # 保存原始消息ID，供 finally 清理限流状态
        
        # 2. 创建/更新审计日志
        log_entry = await _create_or_update_log(
            db, event_id, message_id, open_id, chat_id, chat_type, msg_type, content
        )
        
        # 3. 更新状态为处理中（不提交，最后统一提交）
        log_entry.status = "processing"
        log_entry.processing_started_at = datetime.utcnow()
        
        # 4. 用户身份解析（提前到 Debounce 之前，以便获取工号用于预下载）
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
                await db.commit()  # 错误时需要提交
                
                # 发送错误提示
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
            await db.commit()  # 错误时需要提交
            
            logger.error("Feishu configuration error", 
                        error=error_msg,
                        app_id=app_id,
                        event_id=event_id)
            
            return {"status": "failed", "error": error_msg}
        
        # 更新日志用户信息
        log_entry.employee_no = employee_no
        log_entry.user_id = user.id if user else None
        
        # 6. 访问控制检查
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
        
        if not allowed and msg_type in ["text"]:
            log_entry.status = "rejected"
            log_entry.error_type = "access_denied"
            log_entry.error_message = reason
            await db.commit()  # 拒绝时需要提交
            
            # ← 新增：拒绝时也显示状态
            rejection_msg = access_controller.get_rejection_message(reason)
            await _send_progress_text(rejection_msg, min_interval_seconds=0.0)
            
            # 清理状态管理器
            
            return {"status": "rejected", "reason": reason}
        
        # 7. 获取会话映射
        session_id = await _get_or_create_session(db, open_id, chat_id, chat_type, user.id if user else None)
        
        # 8. 媒体文件下载
        media_paths = []
        download_error_msg = None
        
        # 支持的媒体类型：image, file, audio
        # 跳过的类型：media(视频), sticker(贴纸) - 暂不支持
        if msg_type in ["image", "file", "audio"]:
            # 传入 app_id 以获取正确的 FeishuClient
            media_downloader = get_media_downloader(app_id=app_id)
            
            # 根据消息类型提取对应的 key
            if msg_type == "image":
                file_key = content.get("image_key")
                default_name = "image.jpg"
            else:  # file, audio
                file_key = content.get("file_key")
                default_name = f"{msg_type}.bin"
            
            file_name = content.get("file_name") or default_name
            
            if file_key:
                media_file = await media_downloader.download_with_retry(
                    db=db,
                    file_key=file_key,
                    message_id=message_id,
                    file_name=file_name,
                    file_type=msg_type,
                    user_id=user.usernumb if user else employee_no,  # 使用工号（usernumb）
                    open_id=open_id,  # 保留 open_id 用于记录
                    chat_id=chat_id,
                    app_id=app_id,  # 用于上下文隔离
                    user=user if user else None,  # 用户对象（私聊文件落盘用）
                    chat_type=chat_type,  # 聊天类型（p2p/group）
                )
                
                if media_file:
                    # 下载成功
                    media_paths.append(media_file.local_path)
                else:
                    # 下载失败（包括文件过大），发送提示但不中断流程
                    logger.warning("Media download failed or file too large",
                                  file_key=file_key,
                                  file_name=file_name)
                    
                    # 发送错误提示给用户
                    try:
                        feishu_client = await get_feishu_client(app_id, db)
                        error_hint = f"⚠️ 文件 {file_name} 下载失败（可能超过30MB限制或网络错误）"
                        await feishu_client.send_text_message(
                            receive_id=chat_id if chat_type == "group" else open_id,
                            text=error_hint,
                            receive_id_type="chat_id" if chat_type == "group" else "open_id"
                        )
                    except Exception as send_err:
                        logger.warning("Failed to send download error message",
                                      error=str(send_err))
        elif msg_type in ["media", "sticker"]:
            # 暂不支持视频和贴纸类型
            logger.info(f"Media type '{msg_type}' not supported yet, skipping download",
                       message_id=message_id,
                       msg_type=msg_type)
        
        # 10. 提取文本内容
        text_content = _extract_text_content(content, msg_type)
        
        if not text_content and not media_paths:
            log_entry.status = "completed"
            log_entry.reply_content = "未检测到有效内容"
            await db.commit()  # 提前返回时需要提交
            return {"status": "completed", "message": "No content"}
        
        # 只有媒体文件但没有文本内容，静默处理不回复
        if media_paths and not text_content:
            log_entry.status = "completed"
            log_entry.reply_content = "媒体文件已接收，等待后续提问"
            await db.commit()  # 提前返回时需要提交
            logger.info("Media files received without text, waiting for user question",
                       media_count=len(media_paths),
                       message_id=message_id)
            return {"status": "completed", "message": "Media received, waiting for question"}
        
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
        await _send_progress_text("生成答案中")
        
        # 11. AI处理 - 使用流式版本（支持媒体文件）
        # 加载最近媒体上下文（支持历史引用）
        context_manager = get_media_context_manager()
        recent_media = await context_manager.get_recent_media(
            app_id=app_id,
            open_id=open_id,
            chat_id=chat_id,
            limit=50,
        )
        
        # 构建带上下文的输入文本
        if recent_media:
            context_lines = ["[历史媒体文件]"]
            for media in recent_media:
                file_name = media.get("file_name", "unknown")
                file_type = media.get("file_type", "file")
                path_exists = media.get("path_exists", False)
                file_path = media.get("local_path", "")
                
                if path_exists:
                    file_path = Path(file_path)
                    context_lines.append(f"- {file_type}: {file_path.name}")
                else:
                    context_lines.append(f"- {file_type}: {file_name} [文件已过期]")
            
            context_lines.append("")
            context_lines.append("[当前消息]")
            context_lines.append(text_content)
            
            full_input_text = "\n".join(context_lines)
            
            logger.info("Media context loaded for AI",
                       context_count=len(recent_media),
                       app_id=app_id,
                       open_id=open_id,
                       chat_id=chat_id)
        else:
            full_input_text = text_content
        await _send_progress_text("正在分析问题，请稍候。")
        
        # 流式处理相关状态
        reply_chunks: list[str] = []
        current_step: str | None = None
        final_answer = ""
        current_session_id = session_id
        stream_completed = False
        stream_activity_at = time.monotonic()
        stream_stop_event = asyncio.Event()
        last_preview_sent_at = 0.0
        last_preview_length = 0
        heartbeat_messages = [
            "正在分析问题，请稍候。",
            "正在整理上下文和答案结构",
            "历史对话较长，正在整理上下文。",
        ]

        async def _stream_status_heartbeat():
            heartbeat_index = 0
            while not stream_stop_event.is_set():
                try:
                    await asyncio.wait_for(stream_stop_event.wait(), timeout=4.0)
                    break
                except asyncio.TimeoutError:
                    idle_seconds = time.monotonic() - stream_activity_at
                    if idle_seconds < 4:
                        continue

                    if current_step:
                        heartbeat_text = current_step
                    else:
                        heartbeat_text = heartbeat_messages[min(heartbeat_index, len(heartbeat_messages) - 1)]
                        if heartbeat_index < len(heartbeat_messages) - 1:
                            heartbeat_index += 1

                    try:
                        await _send_progress_text(heartbeat_text, min_interval_seconds=8.0)
                    except Exception as heartbeat_err:
                        logger.warning(
                            "Failed to refresh Feishu heartbeat status",
                            event_id=event_id,
                            message_id=message_id,
                            error=str(heartbeat_err),
                        )

        heartbeat_task = asyncio.create_task(_stream_status_heartbeat())
        
        try:
            # 使用流式管线执行
            async for event in run_agent_pipeline_stream(
                usernumb=employee_no,
                user_id=str(user.id) if user else "",
                input_text=full_input_text,
                session_id=session_id,
                source="feishu",
                sub_intent="feishu",
                feishu_chat_id=chat_id,
                feishu_open_id=open_id,
                feishu_chat_type=chat_type,
            ):
                evt_type = event.get("event")
                evt_data = event.get("data", {})
                stream_activity_at = time.monotonic()

                if evt_type == "delta":
                    content = evt_data.get("content", "")
                    if content:
                        reply_chunks.append(content)
                        final_answer = "".join(reply_chunks)

                elif evt_type == "generating":
                    preview = evt_data.get("preview", "")
                    now = time.monotonic()
                    preview_length = len(preview)
                    if preview_length >= 160 and (preview_length - last_preview_length >= 360 or now - last_preview_sent_at >= 12.0):
                        preview_text = _build_generation_preview(preview)
                        if preview_text:
                            await _send_progress_text(f"生成中预览：\n{preview_text}", min_interval_seconds=0.0)
                            last_preview_sent_at = now
                            last_preview_length = preview_length


                elif evt_type == "thinking":
                    current_step = evt_data.get("message", "正在分析问题，请稍候。")
                    await _send_progress_text(current_step)

                elif evt_type == "tool_call":
                    tool_name = evt_data.get("name", "unknown")
                    current_step = f"正在调用工具：{tool_name}"
                    await _send_progress_text(current_step)

                elif evt_type == "compaction_start":
                    current_step = "历史对话较长，正在整理上下文。"
                    await _send_progress_text(current_step)

                elif evt_type == "compaction_done":
                    current_step = "历史整理完成，继续生成答案。"
                    await _send_progress_text(current_step)

                elif evt_type == "finish":
                    stream_completed = True
                    finish_meta = evt_data if isinstance(evt_data, dict) else {}
                    iterations = finish_meta.get("iterations", 0)
                    logger.info(
                        "Feishu pipeline stream finished",
                        event_id=event_id,
                        message_id=message_id,
                        session_id=session_id,
                        iterations=iterations,
                        reply_length=len(final_answer),
                    )
                    if iterations > 0:
                        current_step = f"已完成 {iterations} 轮处理。"
                        await _send_progress_text(current_step)

                elif evt_type == "done":
                    current_session_id = evt_data.get("session_id", session_id)
                    final_answer = evt_data.get("reply", final_answer)
                    message_id = evt_data.get("message_id")
                    logger.info(
                        "Feishu pipeline stream done",
                        event_id=event_id,
                        message_id=message_id,
                        session_id=current_session_id,
                        reply_length=len(final_answer),
                    )

                elif evt_type == "error":
                    error_msg = evt_data.get("message", evt_data.get("error", "处理失败"))
                    current_step = f"处理失败：{error_msg}"
                    logger.error(
                        "Feishu pipeline stream error event",
                        event_id=event_id,
                        message_id=message_id,
                        session_id=session_id,
                        error=error_msg,
                    )
                    await _send_progress_text(current_step)

            if current_session_id:
                session_id = current_session_id
            
            # 最终卡片更新 - 只显示最终答案，步骤完全清除
            
        except asyncio.TimeoutError:
            # AI 处理超时
            logger.warning("AI processing timeout",
                          event_id=event_id,
                          message_id=message_id,
                          duration_ms=int((datetime.utcnow() - log_entry.processing_started_at).total_seconds() * 1000))
            
            # 更新日志状态
            log_entry.status = "failed"
            log_entry.error_type = "timeout"
            log_entry.error_message = "AI 处理超时"
            await db.commit()
            
            # 发送友好提示给用户
            timeout_message = (
                "处理超时。\n\n"
                "当前请求的上下文较长，或者模型响应较慢。你可以稍后重试，或者缩小问题范围后再试。"
            )
            try:
                await _send_progress_text(timeout_message, min_interval_seconds=0.0)
            except Exception as send_err:
                logger.error("Failed to send timeout message", error=str(send_err))
            
            return {"status": "failed", "error": "timeout", "message": "AI 处理超时"}
        except Exception as e:
            logger.error("Error in pipeline processing", exception=str(e))
            await _send_progress_text(f"处理出错：{str(e)}", min_interval_seconds=0.0)
            return {"status": "failed", "error": "processing_error", "message": str(e)}
        finally:
            stream_stop_event.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        
        # 私聊文件落盘：在 media_downloader.py 中完成
        # File 记录创建和 FeishuMediaFiles 关联已在下载时完成
        # 更新 File 记录的 session_id（AI 管线返回）
        if chat_type == "p2p" and media_paths and session_id:
            try:
                await media_downloader.update_file_session_id(
                    db=db,
                    message_id=message_id,
                    session_id=session_id,
                )
            except Exception as update_err:
                logger.error("Failed to update File session_id",
                            message_id=message_id,
                            session_id=session_id,
                            error=str(update_err))
        
        # 关闭流式卡片并发送最终答案
        # 最终答案通过卡片发送，过程消息继续使用普通文本
        card_status = await get_card_status_manager(
            open_id=open_id,
            chat_id=chat_id,
            app_id=app_id,
        )
        complete_ok = await card_status.complete(
            final_answer=final_answer,
            send_as_message=True,
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            open_id=open_id,
            chat_id=chat_id,
            reply_to_message_id=incoming_message_id,
        )
        if not complete_ok:
            raise RuntimeError("Feishu final reply delivery failed")
        
        # 13. 完成处理
        processing_end = datetime.utcnow()
        log_entry.status = "completed"
        log_entry.processing_completed_at = processing_end
        log_entry.processing_duration_ms = int(
            (processing_end - log_entry.processing_started_at).total_seconds() * 1000
        )
        log_entry.reply_content = final_answer
        await db.commit()
        await _update_webhook_dedup_status(event_id, message_id, status="completed")
        
        logger.info("Message processing completed",
                   event_id=event_id,
                   duration_ms=log_entry.processing_duration_ms)
        
        return {
            "status": "completed",
            "message_id": message_id,
            "reply_length": len(final_answer),
        }
        
    except Exception as e:
        logger.error("Message processing failed",
                    event_id=event_id,
                    error=str(e),
                    exc_info=True)
        try:
            await _send_progress_text(f"Processing failed: {str(e)}", min_interval_seconds=0.0)
        except Exception:
            pass

        if log_entry is not None:
            log_entry.status = "failed"
            log_entry.error_type = "processing_error"
            log_entry.error_message = str(e)
            await db.commit()
        await _update_webhook_dedup_status(event_id, message_id, status="failed")
        
        raise
    
    finally:
        # ← 新增：清理限流状态
        try:
            await rate_limiter.end_processing(app_id, open_id, processing_message_id, chat_id)
        except Exception as e:
            logger.error("Failed to end processing", error=str(e))


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


async def _update_webhook_dedup_status(event_id: str, message_id: str, status: str) -> None:
    """Keep the webhook ingress dedup state in sync with downstream processing result."""
    if not event_id or not message_id:
        return

    dedup_key = f"msg:{event_id}:{message_id}"
    ttl_seconds = 86400 if status == "completed" else 604800
    try:
        await redis_client.set(dedup_key, status, ex=ttl_seconds)
    except Exception as e:
        logger.warning(
            "Failed to update webhook dedup status",
            event_id=event_id,
            message_id=message_id,
            status=status,
            error=str(e),
        )


async def _get_or_create_session(
    db: AsyncSession,
    open_id: str,
    chat_id: str,
    chat_type: str,
    user_id: UUID = None,
) -> str:
    """获取或创建飞书会话 session"""
    return await get_or_rotate_feishu_session(
        db,
        open_id=open_id,
        chat_id=chat_id,
        chat_type=chat_type,
        user_id=user_id,
    )


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
