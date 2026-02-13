"""
M04-2 JSON 修复器：修复 LLM 输出的畸形 JSON

处理常见问题：
- Markdown 代码块包裹 (```json ... ```)
- 多余文字说明 ("以下是结果：{...}")
- 尾部多余逗号、缺失引号、单引号等（由 json-repair 处理）
"""

import json
import re

import structlog
from json_repair import repair_json

log = structlog.get_logger()

# 匹配第一个 JSON 对象 { ... }
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class JsonRepairer:
    """修复 LLM 输出的畸形 JSON"""

    def repair(self, raw: str) -> dict:
        """
        修复流程：
        1. 去除 Markdown 代码块标记
        2. 提取第一个 JSON 对象
        3. json-repair 修复
        4. json.loads 解析

        Raises:
            ValueError: 修复后仍然无法解析为有效 JSON dict
        """
        cleaned = self._strip_markdown(raw)
        cleaned = self._extract_json_object(cleaned)
        repaired = repair_json(cleaned, return_objects=False)

        try:
            data = json.loads(repaired)
        except json.JSONDecodeError as e:
            log.warning("JSON 修复后仍然解析失败", raw_preview=raw[:200], error=str(e))
            raise ValueError(f"JSON 修复失败: {e}") from e

        if not isinstance(data, dict):
            raise ValueError(f"期望 JSON 对象，实际得到 {type(data).__name__}")

        return data

    def _strip_markdown(self, raw: str) -> str:
        """去除 Markdown 代码块标记"""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # 去掉首行 ```json 和末行 ```
            lines = [line for line in lines if not line.strip().startswith("```")]
            cleaned = "\n".join(lines)
        return cleaned

    def _extract_json_object(self, text: str) -> str:
        """从文本中提取第一个 JSON 对象"""
        match = _JSON_OBJECT_RE.search(text)
        if match:
            return match.group()
        # 没找到花括号，原样返回让后续 json-repair 尝试
        return text
