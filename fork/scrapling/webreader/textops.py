"""Text helpers for read results: keep only the paragraphs that answer a question (BM25, as in
Crawl4AI's BM25ContentFilter), and turn inline markdown links into numbered footnotes."""

import math
import re
from collections import Counter
from typing import Dict, List, Tuple

_WORD = re.compile(r"[a-z0-9]+|[぀-ヿ㐀-鿿가-힯]+", re.I)
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


def _tokens(s: str) -> List[str]:
    out: List[str] = []
    for w in _WORD.findall(s.lower()):
        if _CJK.match(w):
            # No spaces between CJK words: score on character pairs (and single characters for 1-char words).
            out.extend([w[i : i + 2] for i in range(len(w) - 1)] or [w])
        else:
            out.append(w)
    return out


def _paragraphs(text: str) -> List[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) < 4:  # one big block: fall back to lines
        paras = [p.strip() for p in text.split("\n") if p.strip()]
    return paras


def relevant(text: str, query: str, max_chars: int, k1: float = 1.5, b: float = 0.75) -> Tuple[str, Dict[str, int]]:
    """The paragraphs that score best for the query, in page order, within max_chars."""
    paras = _paragraphs(text)
    q = set(_tokens(query))
    if not paras or not q:
        return text[:max_chars], {"paragraphs": len(paras), "kept": len(paras)}
    docs = [_tokens(p) for p in paras]
    avg = sum(map(len, docs)) / len(docs) or 1
    df = Counter(t for d in docs for t in set(d) if t in q)
    n = len(docs)
    scores = []
    for i, d in enumerate(docs):
        tf = Counter(d)
        s = 0.0
        for t in q:
            if tf[t]:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg))
        scores.append((s, i))
    keep, used = set(), 0
    for s, i in sorted(scores, reverse=True):
        if s <= 0 or max_chars - used < 200:
            continue
        if used + len(paras[i]) > max_chars:  # a long paragraph that matches: keep its start
            paras[i] = paras[i][: max_chars - used - 1] + "…"
        keep.add(i)
        used += len(paras[i]) + 2
    if not keep:
        return text[:max_chars], {"paragraphs": n, "kept": 0, "matched": 0}
    parts, last = [], -2
    for i in sorted(keep):
        if i != last + 1 and parts:
            parts.append("…")
        parts.append(paras[i])
        last = i
    return "\n\n".join(parts), {"paragraphs": n, "kept": len(keep)}


_LINK = re.compile(r"(?<!!)\[([^\]\n]{0,300})\]\(([^)\s]+)\)")


def footnotes(md: str) -> str:
    """[text](url) -> [text][n], with the URLs listed once at the end (Crawl4AI's citations)."""
    refs: Dict[str, int] = {}

    def sub(m: re.Match) -> str:
        n = refs.setdefault(m.group(2), len(refs) + 1)
        return f"[{m.group(1)}][{n}]"

    body = _LINK.sub(sub, md)
    if not refs:
        return md
    return body + "\n\n" + "\n".join(f"[{n}]: {u}" for u, n in refs.items())
