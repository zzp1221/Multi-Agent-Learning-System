from retrieval.fmm_tokenizer import Token
from retrieval.grep_searcher import GrepSearcher


class StaticTokenizer:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens

    def tokenize(self, text: str) -> list[Token]:
        del text
        return self.tokens

    def get_idf(self, term: str):
        del term
        return None


class RecordingCursor:
    def __init__(self) -> None:
        self.params = []

    def execute(self, sql, params):
        del sql
        self.params.append(params)

    def fetchall(self):
        return []


class BatchTermCursor:
    def __init__(self) -> None:
        self.params = []
        self.sql = []
        self._last_sql = ""

    def execute(self, sql, params):
        self.sql.append(sql)
        self.params.append(params)
        self._last_sql = sql

    def fetchall(self):
        if "FROM rag.knowledge_document kd" in self._last_sql and "FROM rag.knowledge_chunk" not in self._last_sql:
            return [
                (0, "doc-title", "关系模型"),
                (1, "doc-shared", "PBR材质系统"),
                (2, "doc-shared", "图形渲染管线"),
            ]
        if "FROM rag.knowledge_chunk" in self._last_sql:
            return [
                (0, "doc-title", "关系模型"),
                (0, "doc-content", "数据库范式"),
                (1, "doc-shared", "PBR材质系统"),
            ]
        return []


class PartialTitleTermCursor:
    def __init__(self) -> None:
        self.params = []
        self.sql = []
        self._last_sql = ""

    def execute(self, sql, params):
        self.sql.append(sql)
        self.params.append(params)
        self._last_sql = sql

    def fetchall(self):
        if "FROM rag.knowledge_document kd" in self._last_sql and "FROM rag.knowledge_chunk" not in self._last_sql:
            return [(0, "doc-title", "关系模型")]
        if "FROM rag.knowledge_chunk" in self._last_sql:
            return [(1, "doc-content", "PBR材质系统")]
        return []


class TopicPhraseCursor:
    def __init__(self) -> None:
        self.params = []
        self._last_pattern = ""

    def execute(self, sql, params):
        del sql
        self.params.append(params)
        self._last_pattern = params[0]

    def fetchall(self):
        if self._last_pattern == "%DFA最小化（Hopcroft算法）%":
            return [("编译原理/DFA最小化算法", '"DFA最小化（Hopcroft算法）"')]
        return []


def test_search_prefers_quoted_topic_phrase_before_template_terms() -> None:
    searcher = GrepSearcher(StaticTokenizer([]))
    cursor = TopicPhraseCursor()

    result = searcher.search(
        cursor,
        '请解释“DFA最小化（Hopcroft算法）”的核心概念、典型场景和常见误区。',
    )

    assert result["priority"][0][0] == "编译原理/DFA最小化算法"
    assert cursor.params[0][0] == "%DFA最小化（Hopcroft算法）%"


def test_extract_focus_phrases_keeps_single_chinese_concept() -> None:
    searcher = GrepSearcher(StaticTokenizer([]))

    assert searcher._extract_focus_phrases("请解释“群”的核心概念") == ["群"]


def test_topic_focus_skips_graph_relation_queries() -> None:
    searcher = GrepSearcher(StaticTokenizer([]))

    assert searcher._should_focus_topic_phrase(
        "请从知识图谱关系角度说明「安全策略与等保」与「操作系统安全机制、数据库安全与SQL注入防御」之间的多跳联系。"
    ) is False


def test_long_instruction_query_skips_full_phrase_scan() -> None:
    searcher = GrepSearcher(StaticTokenizer([]))
    long_query = (
        "Please explain the relationship between graph coloring and chromatic polynomial, "
        "then compare it with Euler graph, Hamilton graph, NP-completeness, and reduction. "
        "Cover roles, prerequisites, multi-hop links, and the learning path."
    )

    assert searcher._should_search_full_phrase(long_query) is False
    assert searcher._should_search_full_phrase("DFA minimization") is True


def test_term_phrase_search_deduplicates_repeated_terms_before_sql() -> None:
    tokenizer = StaticTokenizer(
        [
            Token("关系", 0, 2, 5.0, "TERM"),
            Token("关系", 2, 4, 5.0, "TERM"),
            Token("PBR", 4, 7, 6.0, "TERM"),
        ]
    )
    cursor = RecordingCursor()

    GrepSearcher(tokenizer)._term_phrase_search(cursor, "关系关系pbr", "COMPUTER_SCIENCE")

    searched_patterns = [params[index] for params in cursor.params for index in range(1, len(params), 3)]
    assert searched_patterns == ["%关系%", "%PBR%", "%关系%", "%PBR%"]
    assert len(cursor.params) == 2


def test_term_phrase_search_batches_queries_and_preserves_scoring() -> None:
    tokenizer = StaticTokenizer(
        [
            Token("关系", 0, 2, 5.0, "TERM"),
            Token("PBR", 2, 5, 6.0, "TERM"),
            Token("渲染", 5, 7, 9.0, "TERM"),
        ]
    )
    cursor = BatchTermCursor()

    results = GrepSearcher(tokenizer)._term_phrase_search(cursor, "关系pbr渲染", "COMPUTER_SCIENCE")

    assert len(cursor.params) == 2
    assert cursor.params[0] == (
        0,
        "%关系%",
        "COMPUTER_SCIENCE",
        1,
        "%PBR%",
        "COMPUTER_SCIENCE",
        2,
        "%渲染%",
        "COMPUTER_SCIENCE",
    )
    assert results[0] == ("doc-shared", "PBR材质系统", 0.98, ["PBR"])
    assert ("doc-title", "关系模型", 0.98, ["关系"]) in results


def test_term_phrase_search_uses_content_for_terms_without_title_hits() -> None:
    tokenizer = StaticTokenizer(
        [
            Token("关系", 0, 2, 5.0, "TERM"),
            Token("PBR", 2, 5, 6.0, "TERM"),
        ]
    )
    cursor = PartialTitleTermCursor()

    results = GrepSearcher(tokenizer)._term_phrase_search(cursor, "关系pbr", "COMPUTER_SCIENCE")

    assert len(cursor.params) == 2
    assert cursor.params[1][-3:] == (1, "%PBR%", "COMPUTER_SCIENCE")
    assert ("doc-title", "关系模型", 0.98, ["关系"]) in results
    assert ("doc-content", "PBR材质系统", 0.98, ["PBR"]) in results


def test_dedupe_terms_keeps_highest_idf_variant() -> None:
    searcher = GrepSearcher(StaticTokenizer([]))

    terms = searcher._dedupe_terms_by_normalized_phrase(
        [
            {"text": "关系", "idf": 5.0, "score_factor": 1.0},
            {"text": "关系", "idf": 7.0, "score_factor": 1.0},
            {"text": "PBR", "idf": 6.0, "score_factor": 1.0},
        ]
    )

    assert terms == [
        {"text": "关系", "idf": 7.0, "score_factor": 1.0},
        {"text": "PBR", "idf": 6.0, "score_factor": 1.0},
    ]
