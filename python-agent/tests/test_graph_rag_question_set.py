from pathlib import Path

from knowledge.generate_graph_rag_100 import _read_json, validate


def test_graph_rag_100_question_set_contract() -> None:
    question_set_path = Path(__file__).resolve().parent.parent / "reports" / "graph_rag_100_questions.json"

    payload = _read_json(question_set_path)
    validate(payload)

    assert payload["suite"] == "graph_rag_100"
    assert payload["count"] == 100
    assert len({item["graphIntent"] for item in payload["questions"]}) >= 5
    assert all(len(item["expectedRelatedSlugs"]) >= 2 for item in payload["questions"])
