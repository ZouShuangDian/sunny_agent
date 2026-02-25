"""
LocalAgentExecutor — local_code 类型 SubAgent 的抽象基类

使用方式：
    在 agent.md 中指定 type: local_code 和 entry 字段：

        ---
        name: supply_chain_agent
        type: local_code
        entry: app.subagents.builtin_agents.supply_chain.executor::SupplyChainExecutor
        ---

    然后在对应模块中实现 LocalAgentExecutor 子类：

        class SupplyChainExecutor(LocalAgentExecutor):
            async def execute(self, task: str) -> str:
                # 任意复杂逻辑：多阶段 LLM 调用、数据库查询、规则引擎...
                return final_report

规范：
    - execute() 必须是 async 方法
    - 返回值是字符串（最终报告），主 Agent 将其作为 tool result 处理
    - 内部异常应自行捕获并转化为错误描述字符串返回，不要向上抛出
    - 如需访问 LLM、工具等基础设施，通过构造函数注入（SubAgentCallTool 负责传递）
"""

from abc import ABC, abstractmethod


class LocalAgentExecutor(ABC):
    """
    local_code SubAgent 的标准接口。

    接口契约：接受任务描述字符串，返回结果报告字符串。
    内部实现不受约束，可以是任意复杂的业务逻辑。
    """

    @abstractmethod
    async def execute(self, task: str) -> str:
        """
        执行任务并返回结果报告。

        Args:
            task: 主 Agent 传入的任务描述（来自 subagent_call 的 task 参数）

        Returns:
            结果报告字符串，主 Agent 将直接读取此内容继续推理
        """
        ...
