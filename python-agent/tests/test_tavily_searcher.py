from retrieval import tavily_searcher
from retrieval.tavily_searcher import TavilySearcher


class _LegacySettings:
    pass


def test_tavily_searcher_tolerates_legacy_settings_without_tavily_fields(monkeypatch) -> None:
    monkeypatch.setattr(tavily_searcher, "get_settings", lambda: _LegacySettings())
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    assert TavilySearcher().search("当前问题", top_k=1) == []
