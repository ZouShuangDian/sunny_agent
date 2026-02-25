"""
L3 深度推理引擎：模块化 ReAct 循环

组件：
- react_engine: 编排器（Loop + 降级 + 结果组装）
- thinker: 决策者（Prompt + LLM + 解析）
- actor: 执行者（安全工具执行）
- observer: 观察者（轨迹 + 预算 + 熔断）
"""
