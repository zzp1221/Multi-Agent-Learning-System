"""测试结构感知压缩器"""

import json
import pytest
from src.ai_modules.runtime.structure_aware_compactor import StructureAwareCompactor


def test_compactor_short_string():
    """测试短字符串不压缩"""
    compactor = StructureAwareCompactor(default_budget=100)
    text = "这是一个短文本"
    result = compactor.compact(text)
    assert result == text


def test_compactor_long_prose():
    """测试长散文压缩"""
    compactor = StructureAwareCompactor(default_budget=50)
    text = "第一句话。第二句话是一个很长很长很长很长的句子。第三句话。最后一句话。"
    result = compactor.compact(text)

    assert "第一句话" in result
    assert "最后一句话" in result
    assert len(result) <= 100


def test_compactor_json():
    """测试JSON压缩"""
    compactor = StructureAwareCompactor(default_budget=50)

    data = {
        "name": "测试",
        "value": 123,
        "metadata": {"debug": "info", "extra": "data"},
        "_debug": "应该被移除",
        "timestamp": "2024-01-01",
    }
    json_str = json.dumps(data, ensure_ascii=False)

    result = compactor.compact(json_str)

    # 因为预算限制，应该触发压缩
    assert "name" in result
    assert len(result) <= 60


def test_compactor_code():
    """测试代码压缩"""
    compactor = StructureAwareCompactor(default_budget=100)

    code = """def hello():
    print("hello")
    x = 1
    return x

class Test:
    pass
"""

    result = compactor.compact(code)

    assert "def hello" in result
    assert "class Test" in result


def test_compactor_dict():
    """测试字典压缩"""
    compactor = StructureAwareCompactor(default_budget=100)

    data = {
        "name": "测试",
        "metadata": {"should_remove": True},
        "_debug": "remove",
        "value": 123,
    }

    result = compactor.compact(data)

    assert isinstance(result, dict)
    assert "name" in result
    assert "metadata" not in result


def test_compactor_list():
    """测试列表压缩"""
    compactor = StructureAwareCompactor(default_budget=100)

    data = list(range(20))
    result = compactor.compact(data)

    assert isinstance(result, list)
    assert len(result) <= 11


def test_compactor_empty_string():
    """测试空字符串"""
    compactor = StructureAwareCompactor(default_budget=100)
    result = compactor.compact("")
    assert result == ""


def test_compactor_none_value():
    """测试None值"""
    compactor = StructureAwareCompactor(default_budget=100)
    result = compactor.compact(None)
    assert result is None


def test_compactor_custom_budget():
    """测试自定义预算"""
    compactor = StructureAwareCompactor(default_budget=100)

    text = "a" * 200
    result = compactor.compact(text, budget=50)
    assert len(result) <= 53
