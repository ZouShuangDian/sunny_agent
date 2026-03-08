"""
LocalAgentExecutor — local_code 类型 SubAgent 的抽象基类

⚠️ v3 简化后暂无调用方：subagent_call 已移除 local_code 类型支持。
保留此文件供未来 Task 系统复用。
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
