"""
L1 快速通道：Bounded Loop (Micro-ReAct) 执行引擎

执行流程：
1. Load: 从 PromptService 标签匹配 System Prompt
2. Build: 组装 messages（System Prompt + 对话历史 + 用户输入）
3. Loop: 进入执行循环（max_loop_steps 限制）
   - Call: 调用 LLM（System Prompt + History + Tools Schema）
   - Check: 无 Tool Call → Break；有 Tool Call → 执行 → 追加到 History
   - Guard: 达到 MaxSteps → 强制停止工具调用，要求 LLM 总结
4. Return: 返回最终自然语言回复

工具集策略：L1 使用固定工具集（所有内置工具），LLM 自行决定是否调用。
Prompt 策略：PromptService 标签匹配，未命中时降级到默认 Prompt。
"""

import json
import time
from collections.abc import AsyncIterator
from datetime import date

import structlog

from app.execution.l1.prompt_retriever import PromptRetriever
from app.execution.schemas import ExecutionResult
from app.guardrails.schemas import IntentResult
from app.llm.client import LLMClient
from app.memory.schemas import ToolCall
from app.tools.registry import ToolRegistry

log = structlog.get_logger()

# L1 固定参数
_MAX_LOOP_STEPS = 3
_TEMPERATURE = 0.7
_MAX_TOKENS = 4096

# 全局基座人设 Prompt（与检索到的任务 Prompt 拼接）
_BASE_PROMPT = (
    "你是 Agent Sunny，舜宇集团的 AI 智能助手。"
    "你乐于助人，回答专业准确，语言简洁友好。"
)

# Prompt 检索器（模块级单例）
prompt_retriever = PromptRetriever()


class L1FastTrack:
    """L1 快速通道执行引擎"""

    def __init__(self, llm: LLMClient, tool_registry: ToolRegistry):
        self.llm = llm
        self.tool_registry = tool_registry

    async def execute(
        self,
        intent_result: IntentResult,
        session_id: str,
    ) -> ExecutionResult:
        """
        非流式执行 L1 任务。

        1. 从 Milvus 检索匹配的 Prompt
        2. 组装 messages（system + history + user）
        3. Bounded Loop（工具调用循环）
        4. 返回最终回复
        """
        start = time.time()

        # 1. 标签匹配 Prompt（intent_primary → PromptService）
        task_prompt = await prompt_retriever.retrieve(
            intent_primary=intent_result.intent.primary,
        )
        today = date.today().strftime("%Y年%m月%d日")
        system_prompt = f"{_BASE_PROMPT}\n当前日期：{today}\n\n{task_prompt}"

        # 2. 组装 messages：system + 对话历史 + 当前用户输入
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            *intent_result.history_messages,
            {"role": "user", "content": intent_result.raw_input},
        ]

        # 3. Bounded Loop — 固定工具集
        tool_schemas = self.tool_registry.get_all_schemas()
        all_tool_calls: list[ToolCall] = []
        response = None

        for step in range(_MAX_LOOP_STEPS):
            # 最后一步不传工具，强制 LLM 总结
            use_tools = tool_schemas if step < _MAX_LOOP_STEPS - 1 else None

            if use_tools:
                response = await self.llm.chat_with_tools(
                    messages=messages,
                    tools=use_tools,
                    temperature=_TEMPERATURE,
                    max_tokens=_MAX_TOKENS,
                )
                raw_tool_calls = getattr(response, "tool_calls_raw", None)
            else:
                response = await self.llm.chat(
                    messages=messages,
                    temperature=_TEMPERATURE,
                    max_tokens=_MAX_TOKENS,
                )
                raw_tool_calls = None

            # 没有工具调用，任务完成
            if not raw_tool_calls:
                break

            # 将 assistant 消息（含 tool_calls）追加到 history
            assistant_msg: dict = {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in raw_tool_calls
                ],
            }
            messages.append(assistant_msg)

            # 执行每个工具调用
            for tc in raw_tool_calls:
                tool_start = time.time()
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                result_str = await self.tool_registry.execute(tc.function.name, args)
                tool_duration = int((time.time() - tool_start) * 1000)

                tool_call_record = ToolCall(
                    tool_call_id=tc.id,
                    tool_name=tc.function.name,
                    arguments=args,
                    result=result_str,
                    status="success",
                    duration_ms=tool_duration,
                )
                all_tool_calls.append(tool_call_record)

                # 工具结果追加到 messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

            log.info(
                "L1 工具调用完成",
                step=step + 1,
                tools=[tc.function.name for tc in raw_tool_calls],
            )

        duration_ms = int((time.time() - start) * 1000)

        return ExecutionResult(
            reply=response.content if response else "",
            tool_calls=all_tool_calls,
            source="standard_l1",
            duration_ms=duration_ms,
        )

    async def execute_stream(
        self,
        intent_result: IntentResult,
        session_id: str,
    ) -> AsyncIterator[dict]:
        """
        流式执行 L1 任务。

        yield 事件格式：
        - {"event": "tool_call", "data": {"name": "...", "args": {...}}}
        - {"event": "tool_result", "data": {"name": "...", "result": "..."}}
        - {"event": "delta", "data": "文本片段"}
        - {"event": "finish", "data": {}}
        """
        # 标签匹配 Prompt（intent_primary → PromptService）
        task_prompt = await prompt_retriever.retrieve(
            intent_primary=intent_result.intent.primary,
        )
        today = date.today().strftime("%Y年%m月%d日")
        system_prompt = f"{_BASE_PROMPT}\n当前日期：{today}\n\n{task_prompt}"

        # 组装 messages：system + 对话历史 + 当前用户输入
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            *intent_result.history_messages,
            {"role": "user", "content": intent_result.raw_input},
        ]

        tool_schemas = self.tool_registry.get_all_schemas()

        for step in range(_MAX_LOOP_STEPS):
            # 最后一步不传工具，强制 LLM 总结（与 execute 逻辑一致）
            use_tools = tool_schemas if step < _MAX_LOOP_STEPS - 1 else None

            if use_tools:
                # 带工具的非流式调用（工具决策阶段）
                response = await self.llm.chat_with_tools(
                    messages=messages,
                    tools=use_tools,
                    temperature=_TEMPERATURE,
                    max_tokens=_MAX_TOKENS,
                )
                raw_tool_calls = getattr(response, "tool_calls_raw", None)
            else:
                raw_tool_calls = None

            if not use_tools or not raw_tool_calls:
                if use_tools and response and response.content:
                    # chat_with_tools 已返回完整回复但未调用工具，直接输出避免双重生成
                    yield {"event": "delta", "data": response.content}
                    yield {"event": "finish", "data": {}}
                else:
                    # 无工具场景（最后一步 / 无配置工具），流式生成
                    async for chunk in self.llm.chat_stream(
                        messages=messages,
                        temperature=_TEMPERATURE,
                        max_tokens=_MAX_TOKENS,
                    ):
                        if chunk["type"] == "delta":
                            yield {"event": "delta", "data": chunk["content"]}
                        elif chunk["type"] == "finish":
                            yield {"event": "finish", "data": {}}
                return

            # 有工具调用 → 执行工具
            assistant_msg: dict = {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in raw_tool_calls
                ],
            }
            messages.append(assistant_msg)

            for tc in raw_tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                # 通知前端：工具调用中
                yield {
                    "event": "tool_call",
                    "data": {"name": tc.function.name, "args": args},
                }

                result_str = await self.tool_registry.execute(tc.function.name, args)

                # 通知前端：工具结果
                yield {
                    "event": "tool_result",
                    "data": {"name": tc.function.name, "result": result_str},
                }

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

        # 达到 max_loop_steps → 最终流式输出
        async for chunk in self.llm.chat_stream(
            messages=messages,
            temperature=_TEMPERATURE,
            max_tokens=_MAX_TOKENS,
        ):
            if chunk["type"] == "delta":
                yield {"event": "delta", "data": chunk["content"]}
            elif chunk["type"] == "finish":
                yield {"event": "finish", "data": {}}
