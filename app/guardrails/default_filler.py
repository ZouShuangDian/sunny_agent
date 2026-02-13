"""
M04-3 默认值填充器：当 LLM 输出缺失字段时，按业务规则填入默认值

| 缺失字段            | 默认值            |
|---------------------|-------------------|
| route               | standard_l1       |
| complexity          | simple            |
| confidence          | 0.5               |
| intent.primary      | general_qa        |
| intent.user_goal    | ""                |
| entity_hints        | {}                |
"""


class DefaultFiller:
    """缺失字段默认值填充"""

    # 默认值映射
    _DEFAULTS: dict = {
        "route": "standard_l1",
        "complexity": "simple",
        "confidence": 0.5,
        "needs_clarify": False,
        "clarify_question": None,
    }

    _INTENT_DEFAULTS: dict = {
        "primary": "general_qa",
        "sub_intent": None,
        "user_goal": "",
    }

    def fill(self, data: dict) -> dict:
        """对照 Schema 填充缺失字段的默认值"""
        # 顶层字段
        for key, default in self._DEFAULTS.items():
            if key not in data or data[key] is None:
                data[key] = default

        # intent 子对象
        if "intent" not in data or not isinstance(data.get("intent"), dict):
            data["intent"] = dict(self._INTENT_DEFAULTS)
        else:
            for key, default in self._INTENT_DEFAULTS.items():
                if key not in data["intent"]:
                    data["intent"][key] = default

        # entity_hints（开放字典，缺失时给空字典即可）
        if "entity_hints" not in data or not isinstance(
            data.get("entity_hints"), dict
        ):
            data["entity_hints"] = {}

        return data
