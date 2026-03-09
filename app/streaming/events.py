"""
SSE 事件协议规范（单一维护点）

所有流式端点（/chat/stream）及 SubAgent 的 SSE 事件名称、数据结构均在此定义。
外部独立 Agent 接入时，只需遵守本文件定义的协议，前端无需特殊适配。

事件流时序（主 Agent，正常路径）：
    status → [delta? → context_usage → tool_call → tool_result]* → delta → context_usage → done

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

    data: {
        "phase": "executing",
        "session_id": str,               # 始终携带（首次对话为后端生成的新 ID）
        "is_new_session": bool | absent,  # 仅首次对话时为 true（前端据此在侧栏新增会话）
        "title": str | absent             # 仅首次对话时携带（用户消息前 50 字）
    }

    前端收到 status 事件后：
    1. 缓存 session_id 用于后续请求
    2. 若 is_new_session=true，直接用 session_id + title 在侧栏列表顶部插入新会话
       （无需额外查询 session 列表接口）

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
    ⚠️ v2 废弃：execute_stream() 不再 emit 此事件。
    LLM 文本内容（包含中间步骤推理和最终回答）统一通过 delta 实时推送，thought 与 delta 合并。

    常量保留供：
    - 历史日志分析（v1 日志中存在此事件名）
    - SubAgent 事件名构造（subagent_thought 与本常量独立，不受影响）
    """

    CONTEXT_USAGE = "context_usage"
    """
    上下文窗口用量快照，每步流式 Think 完成后推送（v2：统一在每步 delta 之后推送）。
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
    LLM 文本内容的流式片段（逐 token 实时推送）。
    v2：所有步骤统一通过 delta 推送，中间步骤（function calling 模型）content 通常为空（delta 极少触发）；
    最终步骤 delta 为用户可见的回答，逐 token 连续推送；熔断降级时为单次降级回答。

    data: {"content": str}  # 文本片段，前端 JSON.parse 后取 content 字段 append 拼接

    示例：{"content": "你好"}
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
        "step": int,           # 当前 ReAct 循环步骤编号（与对应 tool_call 相同）
        "name": str,           # 工具名称（与对应 tool_call 相同）
        "result": str | dict   # 工具返回内容；JSON 可解析时自动反序列化为对象，否则保留字符串
    }

    ⚠️ 与 tool_call 区别：见 tool_call 说明。
    """

    # ─────────────────────────────────────────────
    #  SubAgent 事件（v3 简化：未启用，常量保留供未来 Task 系统复用）
    # ─────────────────────────────────────────────

    SUBAGENT_START = "subagent_start"
    SUBAGENT_THOUGHT = "subagent_thought"
    SUBAGENT_TOOL_CALL = "subagent_tool_call"
    SUBAGENT_TOOL_RESULT = "subagent_tool_result"
    SUBAGENT_FINISH = "subagent_finish"


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
        data:  事件数据，统一经 json.dumps 序列化（str 和 dict 均处理）

    返回值：
        完整的 SSE 文本帧字符串（含末尾空行）

    注意：所有 data 统一经 json.dumps 序列化，确保含 \\n 的文本不会截断 SSE 帧。
          前端对每条事件均需 JSON.parse(event.data)；delta 事件解析后取 content 字段拼接。
    """
    data_str = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {data_str}\n\n"
