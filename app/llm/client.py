"""
轻量 LLM 客户端：Phase 1 直连 LiteLLM，Phase 4 迁移到 Model Router

统一封装异步调用，屏蔽不同供应商的 API 差异。
外部服务随时可能挂，所有调用都有异常兜底。
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import structlog
from litellm import acompletion
from litellm.exceptions import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    RateLimitError,
    Timeout,
)

from app.config import get_settings

log = structlog.get_logger()
settings = get_settings()


class LLMError(Exception):
    """LLM 调用失败的应用级异常"""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


@dataclass
class LLMResponse:
    """LLM 调用返回结果"""

    content: str  # 模型输出文本
    model: str  # 实际使用的模型
    usage: dict  # token 用量 {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...}
    finish_reason: str  # 结束原因


class LLMClient:
    """统一 LLM 调用入口"""

    def __init__(
        self,
        default_model: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
        timeout: int | None = None,
    ):
        self.default_model = default_model or settings.LLM_DEFAULT_MODEL
        self.api_key = api_key or settings.LLM_API_KEY
        self.api_base = api_base or settings.LLM_API_BASE
        self.timeout = timeout or settings.LLM_TIMEOUT

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        response_format: dict | None = None,
    ) -> LLMResponse:
        """
        调用 LLM 获取回复。

        Args:
            messages: OpenAI 格式的消息列表
            model: 模型名称，不传则用默认模型
            temperature: 温度（意图识别建议 0.0）
            max_tokens: 最大输出 token 数
            response_format: JSON mode（如 {"type": "json_object"}）
        """
        use_model = model or self.default_model

        kwargs: dict = {
            "model": use_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": self.timeout,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if response_format:
            kwargs["response_format"] = response_format

        log.debug("LLM 调用开始", model=use_model, msg_count=len(messages))

        try:
            response = await acompletion(**kwargs)
        except AuthenticationError as e:
            log.error("LLM 认证失败", model=use_model, error=str(e))
            raise LLMError(f"LLM 认证失败，请检查 API Key 配置: {e}", cause=e) from e
        except RateLimitError as e:
            log.warning("LLM 限流", model=use_model, error=str(e))
            raise LLMError(f"LLM 请求限流，请稍后重试: {e}", cause=e) from e
        except Timeout as e:
            log.warning("LLM 调用超时", model=use_model, timeout=self.timeout)
            raise LLMError(f"LLM 调用超时（{self.timeout}s）: {e}", cause=e) from e
        except APIConnectionError as e:
            log.error("LLM 连接失败", model=use_model, error=str(e))
            raise LLMError(f"LLM 服务连接失败: {e}", cause=e) from e
        except APIError as e:
            log.error("LLM API 错误", model=use_model, error=str(e))
            raise LLMError(f"LLM API 返回错误: {e}", cause=e) from e
        except Exception as e:
            log.error("LLM 未知异常", model=use_model, error=str(e), exc_info=True)
            raise LLMError(f"LLM 调用异常: {e}", cause=e) from e

        choice = response.choices[0]
        usage = response.usage

        result = LLMResponse(
            content=choice.message.content or "",
            model=response.model or use_model,
            usage={
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            },
            finish_reason=choice.finish_reason or "stop",
        )

        log.debug(
            "LLM 调用完成",
            model=result.model,
            tokens=result.usage.get("total_tokens", 0),
            finish_reason=result.finish_reason,
        )

        return result

    async def chat_stream(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[dict]:
        """
        流式调用 LLM，逐 chunk yield。

        yield 的 dict 格式：
        - 文本 chunk: {"type": "delta", "content": "..."}
        - 工具调用:   {"type": "tool_call", "id": "...", "name": "...", "arguments": "..."}
        - 结束:       {"type": "finish", "reason": "stop"}
        """
        use_model = model or self.default_model

        kwargs: dict = {
            "model": use_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": settings.LLM_STREAM_TIMEOUT,
            "stream": True,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if tools:
            kwargs["tools"] = tools

        log.debug("LLM 流式调用开始", model=use_model, msg_count=len(messages))

        try:
            response = await acompletion(**kwargs)
        except (AuthenticationError, RateLimitError, Timeout, APIConnectionError, APIError) as e:
            log.error("LLM 流式调用失败", model=use_model, error=str(e))
            raise LLMError(f"LLM 流式调用失败: {e}", cause=e) from e
        except Exception as e:
            log.error("LLM 流式调用异常", model=use_model, error=str(e), exc_info=True)
            raise LLMError(f"LLM 流式调用异常: {e}", cause=e) from e

        # 追踪工具调用片段
        tool_call_buffers: dict[int, dict] = {}

        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            finish_reason = chunk.choices[0].finish_reason if chunk.choices else None

            if delta:
                # 文本内容
                if delta.content:
                    yield {"type": "delta", "content": delta.content}

                # 工具调用（流式分片到达）
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_call_buffers:
                            tool_call_buffers[idx] = {
                                "id": tc.id or "",
                                "name": tc.function.name if tc.function and tc.function.name else "",
                                "arguments": "",
                            }
                        buf = tool_call_buffers[idx]
                        if tc.id:
                            buf["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                buf["name"] = tc.function.name
                            if tc.function.arguments:
                                buf["arguments"] += tc.function.arguments

            if finish_reason:
                # 输出完整的工具调用
                for _idx, buf in sorted(tool_call_buffers.items()):
                    yield {
                        "type": "tool_call",
                        "id": buf["id"],
                        "name": buf["name"],
                        "arguments": buf["arguments"],
                    }
                tool_call_buffers.clear()
                yield {"type": "finish", "reason": finish_reason}

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """
        带工具定义的非流式调用（L1 Bounded Loop 使用）。
        返回标准 LLMResponse，tool_calls 信息附在 raw_response 中。
        """
        use_model = model or self.default_model

        kwargs: dict = {
            "model": use_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": self.timeout,
            "tools": tools,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base

        log.debug("LLM 工具调用开始", model=use_model, tools_count=len(tools))

        try:
            response = await acompletion(**kwargs)
        except (AuthenticationError, RateLimitError, Timeout, APIConnectionError, APIError) as e:
            log.error("LLM 工具调用失败", model=use_model, error=str(e))
            raise LLMError(f"LLM 工具调用失败: {e}", cause=e) from e
        except Exception as e:
            log.error("LLM 工具调用异常", model=use_model, error=str(e), exc_info=True)
            raise LLMError(f"LLM 工具调用异常: {e}", cause=e) from e

        choice = response.choices[0]
        usage = response.usage

        result = LLMResponse(
            content=choice.message.content or "",
            model=response.model or use_model,
            usage={
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            },
            finish_reason=choice.finish_reason or "stop",
        )
        # 将原始 tool_calls 挂到 response 上供调用方使用
        result.tool_calls_raw = choice.message.tool_calls

        log.debug(
            "LLM 工具调用完成",
            model=result.model,
            has_tool_calls=bool(choice.message.tool_calls),
        )

        return result
