"""
System Prompt 注入标记统一管理

所有动态注入到 System Prompt 的内容块均使用此处定义的 marker 作为边界，
防止多个注入模块使用不同标记导致冲突或截断逻辑错误。

命名规范：
- {功能}_MARKER      — 完整注入块的起始 marker（用于截断/替换）
- {功能}_END_MARKER  — 注入块的结束 marker（仅用于可读性，非截断依据）

扩展指引：
- 新增注入功能时，在此处添加对应的 marker 常量
- marker 字符串应唯一且不易与正常内容冲突（使用 HTML 注释格式）
"""

# ── Todo 状态注入 ────────────────────────────────────────────────────────────
# 由 L3ReActEngine._inject_todo_reminder() 使用
# 格式：\n\n---\n<!-- todo-reminder-start -->...<!-- todo-reminder-end -->

TODO_REMINDER_MARKER = "\n\n---\n<!-- todo-reminder-start -->"
TODO_REMINDER_END_MARKER = "<!-- todo-reminder-end -->"

# ── 预留扩展 ─────────────────────────────────────────────────────────────────
# MEMORY_INJECTION_MARKER = "\n\n---\n<!-- memory-injection-start -->"
# CONTEXT_INJECTION_MARKER = "\n\n---\n<!-- context-injection-start -->"
