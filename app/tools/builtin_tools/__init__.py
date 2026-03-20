"""
内置工具集：自动注册所有内置工具到 ToolRegistry

使用方式：
    from app.tools.builtin_tools import create_builtin_registry
    registry = create_builtin_registry()
"""

from app.config import get_settings
from app.tools.builtin_tools.ask_user import AskUserTool
from app.tools.builtin_tools.bash_tool import BashTool
from app.tools.builtin_tools.create_task import CreateTaskTool
from app.tools.builtin_tools.cron_create import CronCreateTool
from app.tools.builtin_tools.cron_manage import CronManageTool
from app.tools.builtin_tools.present_files import PresentFilesTool
from app.tools.builtin_tools.read_file import ReadFileTool
from app.tools.builtin_tools.read_uploaded_file import ReadUploadedFileTool
from app.tools.builtin_tools.str_replace_file import StrReplaceFileTool
from app.tools.builtin_tools.todo_read import TodoReadTool
from app.tools.builtin_tools.todo_write import TodoWriteTool
from app.tools.builtin_tools.web_fetch import WebFetchTool
from app.tools.builtin_tools.web_search import WebSearchTool
from app.tools.builtin_tools.write_file import WriteFileTool
from app.tools.registry import ToolRegistry


def create_builtin_registry() -> ToolRegistry:
    """创建并注册所有内置工具的 Registry 实例"""
    settings = get_settings()
    registry = ToolRegistry()

    registry.register(WebSearchTool(api_key=settings.BOCHA_API_KEY))
    registry.register(WebFetchTool())

    # 用户交互工具
    registry.register(AskUserTool())

    # Todo 工具（tier=L3）
    registry.register(TodoWriteTool())
    registry.register(TodoReadTool())

    # 沙箱执行工具（tier=L3）
    registry.register(BashTool())
    registry.register(ReadFileTool())
    registry.register(ReadUploadedFileTool())
    registry.register(WriteFileTool())
    registry.register(StrReplaceFileTool())
    registry.register(PresentFilesTool())

    # 定时任务工具（tier=L3）
    registry.register(CronCreateTool())
    registry.register(CronManageTool())

    # 异步任务工具（tier=L3）
    registry.register(CreateTaskTool())

    return registry
