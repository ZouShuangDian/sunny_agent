"""
SSE 事件协议规范（单一维护点）

所有流式端点（/chat/stream）及 SubAgent 的 SSE 事件名称、数据结构均在此定义。
外部独立 Agent 接入时，只需遵守本文件定义的协议，前端无需特殊适配。

事件流时序（主 Agent，正常路径）：
    status → [thought → context_usage → tool_call → tool_result]* → delta → done

事件流时序（含 SubAgent）：
    ... → subagent_start → [subagent_thought → subagent_tool_call → subagent_tool_result]* → subagent_finish → ...

注意：
- finish 是引擎内部事件，不直接透传给前端，其数据合并进 done 事件。
- error 只在异常时发送，之后必定跟随一条 done（带 error=True）。
"""

import json


# ─────────────────────────────────────────────
#  主 Agent 事件名常量
# ─────────────────────────────────────────────

class SSEEvent:
    """SSE 事件名常量。所有流式路径统一从此取值，禁止在业务代码中写字符串字面量。"""

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    STATUS = "status"
    """
    执行阶段状态通知。
    由 chat.py 在调用执行引擎前主动推送，用于告知前端"已进入执行阶段"。

    data: {"phase": "executing"}

    ⚠️ 与 done/finish 区别：status 是开始信号，done 是结束信号。
    """

    DONE = "done"
    """
    流式响应完全结束（前端终止 SSE 连接的信号）。
    由 chat.py 在所有处理完成后推送，是客户端收到的最后一条事件。
    包含本次请求的汇总元数据（耗时、迭代次数、token 用量等）。

    data: {
        "session_id": str,       # 会话 ID
        "duration_ms": int,      # 本次请求总耗时（毫秒）
        "iterations": int,       # ReAct 循环实际执行轮数
        "is_degraded": bool,     # 是否触发熔断降级
        "token_usage": {...},    # token 用量汇总
        "error": bool            # 异常路径时为 True（可选字段，正常时不含）
    }

    ⚠️ 与 finish 区别：
      - finish 是引擎内部事件，携带 l3_steps 原始步骤数据，供 chat.py 持久化，不透传给前端。
      - done 是 chat.py 合并 finish 数据后对外发出的"终止信号"，前端只处理 done。
    """

    ERROR = "error"
    """
    流式处理过程中发生未预期异常。
    推送后必定紧跟一条 done 事件（done.error=True），前端需同时处理两条事件。

    data: {"message": str}  # 面向用户的错误提示（生产环境不暴露堆栈）

    ⚠️ 与 done 区别：error 描述错误原因，done 标记流的结束；两者同时出现，缺一不可。
    """

    # ── 引擎内部（不透传给前端）────────────────────────────────────────────────

    FINISH = "finish"
    """
    ReAct 引擎内部完成事件（chat.py 消费后不透传给前端）。
    携带 l3_steps 原始步骤消息，供 chat.py 持久化到 PG；
    chat.py 将其中的元数据（iterations / token_usage 等）合并进 done 事件后对外发出。

    data: {
        "iterations": int,
        "llm_calls": int,
        "is_degraded": bool,
        "l3_steps": list[dict] | None,       # 原始推理步骤（持久化用）
        "compaction_summary": str | None,    # Level 2 摘要内容（持久化用）
        "token_usage": {...}
    }

    ⚠️ 前端不会收到此事件，不需要处理。
    """

    # ── 推理过程 ──────────────────────────────────────────────────────────────

    THOUGHT = "thought"
    """
    主 Agent 的单步推理内容（LLM 的"内心独白"，非最终回答）。
    在每次 Think 阶段完成后推送；前端通常以折叠/灰色样式展示。

    data: {
        "step": int,      # 当前 ReAct 循环步骤编号（从 0 开始）
        "content": str    # 本次推理文本
    }

    ⚠️ 与 delta 区别：
      - thought：中间推理过程，任务尚未完成，可能还有后续工具调用。
      - delta：最终回答的流式文本片段，表示 Agent 已判定任务完成，是给用户看的正式输出。
    """

    CONTEXT_USAGE = "context_usage"
    """
    上下文窗口用量快照，每次 Think 后随 thought 事件一起推送。
    前端可据此渲染进度条或在接近上限时给用户提示。

    data: {
        "prompt_tokens": int,   # 本次 Think 消耗的 prompt token 数
        "remaining": int,       # 剩余可用 token 数（= 模型上限 - prompt_tokens）
        "percent": float,       # 已使用百分比（保留 1 位小数）
        "limit": int            # 模型上下文窗口上限
    }
    """

    DELTA = "delta"
    """
    主 Agent 最终回答的文本内容。
    仅在 Agent 判定任务完成时推送（think_result.is_done=True），或熔断降级时推送降级回答。

    data: str  # 回答文本（纯字符串，非 dict）

    ⚠️ 与 thought 区别：见 thought 说明。

    【当前实现】：Thinker 使用非流式 LLM 调用（llm.chat），完整文本一次性返回，
                  因此 delta 事件在一次请求中只推送一次，data 是完整回答。
    【未来规划】：若 Thinker 改用流式 LLM 调用（llm.chat_stream），
                  delta 事件将改为多次推送的 token 片段，前端需 append 拼接。
    """

    # ── 工具调用 ──────────────────────────────────────────────────────────────

    TOOL_CALL = "tool_call"
    """
    主 Agent 发起工具调用（调用前推送，此时工具尚未执行）。
    前端可据此显示"正在执行 xxx 工具..."的 Loading 状态。

    data: {
        "step": int,      # 当前 ReAct 循环步骤编号
        "name": str,      # 工具名称（如 bash_tool / sql_query / skill_call）
        "args": dict      # 工具参数（原始 JSON 结构）
    }

    ⚠️ 与 tool_result 区别：tool_call 是调用请求，tool_result 是执行结果；
       两者成对出现，同一 step + name 下先 call 后 result。
    """

    TOOL_RESULT = "tool_result"
    """
    主 Agent 工具调用执行完毕后的结果（工具执行后推送）。
    前端可据此展示工具输出，或结束 Loading 状态。

    data: {
        "step": int,      # 当前 ReAct 循环步骤编号（与对应 tool_call 相同）
        "name": str,      # 工具名称（与对应 tool_call 相同）
        "result": str     # 工具返回内容（字符串形式）
    }

    ⚠️ 与 tool_call 区别：见 tool_call 说明。
    """

    # ─────────────────────────────────────────────
    #  SubAgent 事件（规划中，待 subagent_sse 方案实现后启用）
    # ─────────────────────────────────────────────

    SUBAGENT_START = "subagent_start"
    """
    SubAgent 开始执行（主 Agent 通过 subagent_call 工具触发时推送）。
    前端可据此展示"子 Agent 启动"的 UI 面板。

    data: {
        "agent_id": str,    # 本次 SubAgent 实例 ID（格式: sub_xxxxxxxx，8位hex）
        "agent_name": str,  # SubAgent 配置名称（来自 DB skill 表或参数）
        "task": str         # 分配给该 SubAgent 的任务描述
    }

    ⚠️ 与 status 区别：status 是主 Agent 的开始信号；subagent_start 是嵌套子 Agent 的开始信号。
       两者可在同一次请求中出现，agent_id 用于区分不同的子 Agent 实例。
    """

    SUBAGENT_THOUGHT = "subagent_thought"
    """
    SubAgent 的单步推理内容（与主 Agent 的 thought 含义相同，但来源是子 Agent）。
    通过 agent_id 与主 Agent 的 thought 区分。

    data: {
        "agent_id": str,  # SubAgent 实例 ID（与 subagent_start 对应）
        "step": int,      # SubAgent 内部的推理步骤编号（从 0 开始，独立计数）
        "content": str    # SubAgent 本次推理文本
    }

    ⚠️ 与 thought 区别：thought 来自主 Agent，subagent_thought 来自子 Agent；
       前端应在子 Agent 面板内渲染 subagent_thought，不与主 Agent 的 thought 混排。
    """

    SUBAGENT_TOOL_CALL = "subagent_tool_call"
    """
    SubAgent 发起工具调用（子 Agent 内部调用前推送）。

    data: {
        "agent_id": str,  # SubAgent 实例 ID
        "step": int,      # SubAgent 内部步骤编号
        "name": str,      # 工具名称
        "args": dict      # 工具参数
    }

    ⚠️ 与 tool_call 区别：tool_call 是主 Agent 的工具调用；
       subagent_tool_call 是子 Agent 的工具调用，归属于特定 agent_id 的子 Agent 面板。
    """

    SUBAGENT_TOOL_RESULT = "subagent_tool_result"
    """
    SubAgent 工具调用执行完毕的结果（子 Agent 内部工具执行后推送）。

    data: {
        "agent_id": str,  # SubAgent 实例 ID
        "step": int,      # SubAgent 内部步骤编号（与对应 subagent_tool_call 相同）
        "name": str,      # 工具名称
        "result": str     # 工具返回内容
    }

    ⚠️ 与 tool_result 区别：见 subagent_tool_call 说明。
    """

    SUBAGENT_FINISH = "subagent_finish"
    """
    SubAgent 执行完成（无论正常完成还是异常/降级，均在 finally 块中保证推送）。
    前端收到此事件后可关闭该 agent_id 对应的子 Agent 面板。

    data: {
        "agent_id": str,      # SubAgent 实例 ID（与 subagent_start 对应）
        "agent_name": str,    # SubAgent 名称
        "iterations": int,    # 实际执行轮数（异常时为 -1，表示未知）
        "is_degraded": bool   # 是否降级（超时/异常时为 True）
    }

    ⚠️ 与 done 区别：done 是整个请求（主 Agent）结束的信号；
       subagent_finish 是某个子 Agent 结束的信号，主 Agent 之后可能还会继续执行。
       一次请求中可出现多个 subagent_finish（对应多次 subagent_call 调用）。
    """


# ─────────────────────────────────────────────
#  格式化工具函数
# ─────────────────────────────────────────────

def format_sse(event: str, data: str | dict) -> str:
    """
    将事件名和数据格式化为标准 SSE 文本帧。

    SSE 协议格式：
        event: <event_name>\\n
        data: <json_or_string>\\n
        \\n

    参数：
        event: 事件名（建议使用 SSEEvent 常量）
        data:  事件数据，dict 自动序列化为 JSON；str 直接写入

    返回值：
        完整的 SSE 文本帧字符串（含末尾空行）
    """
    data_str = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else data
    return f"event: {event}\ndata: {data_str}\n\n"
