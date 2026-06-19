"""
FMM (Forward Maximum Matching) tokenizer backed by rag.term_lexicon.
Builds a prefix trie from domain terms and tokenizes Chinese text.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Token:
    text: str
    start: int
    end: int
    idf: float
    term_type: str = "TERM"


class TrieNode:
    __slots__ = ("children", "is_end", "term_info")

    def __init__(self):
        self.children: dict[str, "TrieNode"] = {}
        self.is_end: bool = False
        self.term_info: Optional[dict] = None  # {canonical, idf, term_type}


class FMMTokenizer:
    """Forward Maximum Matching with prefix trie, driven by rag.term_lexicon."""

    def __init__(self):
        self.root = TrieNode()
        self._max_term_len = 0

    def load_from_db(self, cur, domain: str = "COMPUTER_SCIENCE"):
        """Load active terms from rag.term_lexicon into the trie."""
        cur.execute("""
            SELECT canonical_term, normalized_term, idf_score, term_type
            FROM rag.term_lexicon
            WHERE is_active = true AND domain = %s
            ORDER BY char_length(normalized_term) DESC
        """, (domain,))
        rows = cur.fetchall()
        for canonical, normalized, idf, term_type in rows:
            self._insert(normalized, canonical, float(idf), term_type)
        return len(rows)

    def _insert(self, term: str, canonical: str, idf: float, term_type: str):
        node = self.root
        for ch in term:
            if ch not in node.children:
                node.children[ch] = TrieNode()
            node = node.children[ch]
        if not node.is_end or len(term) > len(node.term_info.get("canonical", "")):
            node.is_end = True
            node.term_info = {"canonical": canonical, "idf": idf, "term_type": term_type}
        self._max_term_len = max(self._max_term_len, len(term))

    def tokenize(self, text: str, fallback_idf: float = 1.0) -> list[Token]:
        """Tokenize text using FMM. Single chars get fallback_idf if no match."""
        tokens: list[Token] = []
        i = 0
        n = len(text)
        while i < n:
            node = self.root
            longest_info = None
            longest_end = i
            j = i
            while j < n:
                ch = text[j]
                if ch not in node.children:
                    break
                node = node.children[ch]
                j += 1
                if node.is_end:
                    longest_info = node.term_info
                    longest_end = j
            if longest_info is not None:
                tokens.append(Token(
                    text=text[i:longest_end],
                    start=i,
                    end=longest_end,
                    idf=longest_info["idf"],
                    term_type=longest_info["term_type"],
                ))
                i = longest_end
            else:
                # Single character — not a known term
                ch = text[i]
                tokens.append(Token(text=ch, start=i, end=i + 1, idf=fallback_idf, term_type="CHAR"))
                i += 1
        return tokens

    def get_idf(self, term: str) -> Optional[float]:
        """Look up IDF for an exact term."""
        node = self.root
        for ch in term:
            if ch not in node.children:
                return None
            node = node.children[ch]
        if node.is_end:
            return node.term_info["idf"]
        return None
