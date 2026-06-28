import pytest

from src.ai_modules.config import get_settings
from src.ai_modules.llms import (
    HeuristicSubjectiveJudgeEvaluator,
    OpenAICompatibleSubjectiveJudgeEvaluator,
    SubjectiveJudgeEvaluatorFactory,
)
from src.ai_modules.models import PracticeQuestion


def _build_question() -> PracticeQuestion:
    return PracticeQuestion(
        questionId="q3",
        questionType="SHORT_ANSWER",
        stem="请说明联合索引的使用条件。",
        options=[],
        answer="先判断使用前提，再结合具体查询场景说明容易误判的位置。",
        knowledgeTags=["联合索引", "使用条件"],
        difficultyLevel="BASIC",
        explanation="回答应覆盖条件和场景。",
    )


@pytest.mark.asyncio
async def test_heuristic_subjective_evaluator_handles_empty_answer() -> None:
    evaluator = HeuristicSubjectiveJudgeEvaluator()

    result = await evaluator.evaluate(question=_build_question(), learner_answer="")

    assert result.score == 0.0
    assert result.is_correct is False
    assert result.confidence_level == "LOW"


def test_openai_compatible_subjective_evaluator_extracts_json_payload() -> None:
    evaluator = OpenAICompatibleSubjectiveJudgeEvaluator(api_key="test-key")

    payload = evaluator._extract_json(
        """```json
        {"score": 16, "isCorrect": true, "reason": "回答基本完整", "feedback": "建议补充例子", "confidenceLevel": "MEDIUM"}
        ```"""
    )

    assert payload["score"] == 16
    assert payload["isCorrect"] is True


def test_openai_compatible_subjective_evaluator_defaults_to_main_judge_model(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("ACTIVE_PROVIDER", "mimo")
    monkeypatch.setenv("MIMO_API_KEY", "test-key")

    evaluator = OpenAICompatibleSubjectiveJudgeEvaluator()

    assert evaluator.provider_name == "mimo"
    assert evaluator.model_name == "mimo-v2-omni"

    get_settings.cache_clear()


def test_subjective_evaluator_factory_requires_real_llm(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("ENABLE_LOCAL_JUDGE", "false")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "")
    monkeypatch.setenv("SPARK_API_KEY", "")
    monkeypatch.setenv("MIMO_API_KEY", "")

    with pytest.raises(RuntimeError, match="subjective questions require a real LLM judge"):
        SubjectiveJudgeEvaluatorFactory.create()

    get_settings.cache_clear()
