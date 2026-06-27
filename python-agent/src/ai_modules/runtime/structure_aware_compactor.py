"""结构感知压缩器 - 智能压缩不同类型的内容"""

from __future__ import annotations

import json
import re
from typing import Any


class StructureAwareCompactor:
    """结构感知压缩器 - 根据内容类型智能压缩"""

    def __init__(self, default_budget: int = 600):
        self.default_budget = default_budget

    def compact(self, value: Any, budget: int | None = None) -> Any:
        """
        智能压缩内容

        Args:
            value: 要压缩的内容
            budget: 字符预算，默认使用default_budget

        Returns:
            压缩后的内容
        """
        if budget is None:
            budget = self.default_budget

        if isinstance(value, str):
            return self._compact_string(value, budget)
        elif isinstance(value, dict):
            return self._compact_dict(value, budget)
        elif isinstance(value, list):
            return self._compact_list(value, budget)
        else:
            return value

    def _compact_string(self, text: str, budget: int) -> str:
        """根据内容类型智能压缩字符串"""
        if len(text) <= budget:
            return text

        # 检测内容类型
        if self._is_json(text):
            return self._compact_json(text, budget)
        elif self._is_code(text):
            return self._compact_code(text, budget)
        else:
            return self._compact_prose(text, budget)

    def _is_json(self, text: str) -> bool:
        """检测是否为JSON"""
        stripped = text.strip()
        if not stripped:
            return False
        try:
            json.loads(stripped)
            return True
        except (json.JSONDecodeError, ValueError):
            return False

    def _is_code(self, text: str) -> bool:
        """检测是否为代码"""
        code_indicators = [
            "def ", "class ", "import ", "from ",
            "function ", "const ", "let ", "var ",
            "public ", "private ", "static ",
        ]
        return any(indicator in text for indicator in code_indicators)

    def _compact_json(self, json_str: str, budget: int) -> str:
        """压缩JSON - 移除低优先级字段，保持结构完整"""
        try:
            data = json.loads(json_str)
            low_priority_fields = ["metadata", "_debug", "trace", "timestamp"]
            compacted_data = self._remove_fields(data, low_priority_fields)
            result = json.dumps(compacted_data, ensure_ascii=False, separators=(',', ':'))

            if len(result) <= budget:
                return result

            return json_str[:budget - 10] + "...}"

        except (json.JSONDecodeError, ValueError, TypeError):
            return json_str[:budget] + "..."

    def _remove_fields(self, data: Any, fields_to_remove: list[str]) -> Any:
        """递归移除字段"""
        if isinstance(data, dict):
            return {
                k: self._remove_fields(v, fields_to_remove)
                for k, v in data.items()
                if k not in fields_to_remove
            }
        elif isinstance(data, list):
            return [self._remove_fields(item, fields_to_remove) for item in data]
        else:
            return data

    def _compact_code(self, code: str, budget: int) -> str:
        """压缩代码 - 保留函数签名和关键逻辑"""
        if len(code) <= budget:
            return code

        lines = code.split("\n")
        priority_lines = []
        for line in lines:
            if any(kw in line for kw in ["def ", "class ", "import ", "from "]):
                priority_lines.append(line)

        priority_text = "\n".join(priority_lines)
        if priority_text and len(priority_text) <= budget:
            return priority_text + "\n# ... [其余代码已省略]"

        truncated = code[:budget]
        last_newline = truncated.rfind("\n")
        if last_newline > 0:
            truncated = truncated[:last_newline]

        return truncated + "\n# ... [代码已截断]"

    def _compact_prose(self, text: str, budget: int) -> str:
        """压缩散文 - 提取关键句"""
        if len(text) <= budget:
            return text

        sentences = re.split(r'([。！？.!?]\s*)', text)
        full_sentences = []
        for i in range(0, len(sentences) - 1, 2):
            if i + 1 < len(sentences):
                full_sentences.append(sentences[i] + sentences[i + 1])
            else:
                full_sentences.append(sentences[i])

        if len(full_sentences) <= 2:
            return text[:budget] + "..."

        key_sentences = [full_sentences[0]]
        if len(full_sentences) > 2:
            middle_sentences = full_sentences[1:-1]
            longest = max(middle_sentences, key=len) if middle_sentences else ""
            if longest:
                key_sentences.append(longest)
        key_sentences.append(full_sentences[-1])

        result = "".join(key_sentences)
        if len(result) > budget:
            return text[:budget] + "..."

        return result

    def _compact_dict(self, data: dict[str, Any], budget: int) -> dict[str, Any]:
        """压缩字典 - 移除低优先级字段"""
        low_priority = ["metadata", "_debug", "trace", "timestamp"]
        compacted = {}
        for key, value in data.items():
            if key in low_priority:
                continue
            compacted[key] = self.compact(value, budget // max(len(data), 1))
        return compacted

    def _compact_list(self, data: list[Any], budget: int) -> list[Any]:
        """压缩列表 - 限制元素数量"""
        max_items = 10
        if len(data) <= max_items:
            return [self.compact(item, budget // max(len(data), 1)) for item in data]

        compacted = [self.compact(item, budget // max_items) for item in data[:max_items]]
        compacted.append({"_truncated_items": len(data) - max_items})
        return compacted
