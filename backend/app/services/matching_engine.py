"""Shared lexical/semantic comparison and independent sparse/dense candidate retrieval."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from app.services.event_constraints import specific_title_anchors, title_conflicts
from app.services.match_documents import MatchDocument
from app.services.semantic_models import SemanticUnavailable, get_semantic_models


def normalize(text: str) -> str:
    return re.sub(r"\W+", "", text or "").lower()


def ngrams(text: str) -> set[str]:
    text = normalize(text)
    return {text[i : i + n] for n in (2, 3) for i in range(len(text) - n + 1)}


def lexical_tokens(text: str) -> list[str]:
    # Chinese character bigrams are explicit, reproducible BM25 tokens; Latin terms stay intact.
    tokens = []
    for part in re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part) and len(part) > 1:
            tokens.extend(part[i : i + 2] for i in range(len(part) - 1))
        else:
            tokens.append(part)
    return tokens


def bm25_scores(query: str, documents: list[str], k1=1.2, b=0.75) -> list[float]:
    """Okapi BM25: corpus IDF and document-length normalization; scores are not probabilities."""
    corpus = [Counter(lexical_tokens(text)) for text in documents]
    if not corpus:
        return []
    lengths = [sum(doc.values()) for doc in corpus]
    average = sum(lengths) / len(corpus) or 1.0
    query_terms = set(lexical_tokens(query))
    df = Counter(term for doc in corpus for term in doc)
    scores = []
    for counts, length in zip(corpus, lengths, strict=True):
        score = 0.0
        for term in query_terms:
            tf = counts.get(term, 0)
            if tf:
                idf = math.log(1 + (len(corpus) - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / average))
        scores.append(score)
    return scores


@dataclass
class Comparison:
    ngram_overlap: float = 0.0
    title_containment: bool = False
    entity_overlap: float = 0.0
    action_overlap: float = 0.0
    embedding_similarity: float | None = None
    rerank_score: float | None = None
    conflicts: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)
    specific_anchors: list[str] = field(default_factory=list)


def term_overlap(terms: list[str], text: str) -> float:
    terms = {normalize(term) for term in terms if normalize(term)}
    return sum(term in normalize(text) for term in terms) / len(terms) if terms else 0.0


def compare_many(
    target: MatchDocument,
    candidates: list[MatchDocument],
    *,
    use_embedding=False,
    use_reranker=False,
    time_window_hours=72,
) -> list[Comparison]:
    results = []
    left_grams = ngrams(target.text)
    for candidate in candidates:
        right_grams = ngrams(candidate.text)
        a, b = normalize(target.title), normalize(candidate.title)
        result = Comparison(
            ngram_overlap=len(left_grams & right_grams) / max(1, len(left_grams | right_grams)),
            title_containment=bool(a and b and min(len(a), len(b)) >= 6 and (a in b or b in a)),
            entity_overlap=term_overlap(target.entities, candidate.text),
            action_overlap=term_overlap(target.action_terms, candidate.text),
        )
        for name in ("entities", "action_terms", "event_time"):
            if not getattr(target, name) or not getattr(candidate, name):
                result.missing.append(name)
        if target.event_time and candidate.event_time:
            if (
                abs((target.event_time - candidate.event_time).total_seconds())
                > time_window_hours * 3600
            ):
                result.conflicts.append("time_conflict")
        result.conflicts.extend(title_conflicts(target.title, candidate.title))
        result.specific_anchors = specific_title_anchors(target.title, candidate.title)
        results.append(result)
    if not candidates:
        return results
    models = get_semantic_models() if use_embedding or use_reranker else None
    if use_embedding:
        try:
            vectors = models.encode([target.text, *[doc.text for doc in candidates]])
            for result, vector in zip(results, vectors[1:], strict=True):
                result.embedding_similarity = max(-1.0, min(1.0, float(vectors[0] @ vector)))
        except SemanticUnavailable as exc:
            for result in results:
                result.quality_flags.append(str(exc))
    if use_reranker:
        try:
            scores = models.rerank([(target.text, doc.text) for doc in candidates])
            for result, score in zip(results, scores, strict=True):
                result.rerank_score = score
        except SemanticUnavailable as exc:
            for result in results:
                result.quality_flags.append(str(exc))
    return results


def retrieve(
    target: MatchDocument,
    candidates: list[MatchDocument],
    *,
    limit=20,
    use_embedding=False,
) -> tuple[list[int], dict]:
    sparse = bm25_scores(target.text, [doc.text for doc in candidates])
    sparse_order = sorted(range(len(candidates)), key=lambda i: (-sparse[i], i))
    sparse_order = [i for i in sparse_order[:limit] if sparse[i] > 0]
    dense = []
    flags = []
    if use_embedding and candidates:
        try:
            vectors = get_semantic_models().encode([target.text, *[d.text for d in candidates]])
            similarities = [float(vectors[0] @ v) for v in vectors[1:]]
            dense = sorted(range(len(candidates)), key=lambda i: (-similarities[i], i))[:limit]
        except SemanticUnavailable as exc:
            flags.append(str(exc))
    fusion = Counter()
    for order in (sparse_order, dense):
        for rank, index in enumerate(order, start=1):
            fusion[index] += 1 / (60 + rank)
    hard = [
        i
        for i, doc in enumerate(candidates)
        if set(target.urls) & set(doc.urls) or set(target.canonical_ids) & set(doc.canonical_ids)
    ]
    order = sorted(fusion, key=lambda i: (-fusion[i], i))
    selected = list(dict.fromkeys([*hard, *order[:limit]]))
    return selected, {
        "bm25_scores": sparse,
        "sparse_indices": sparse_order,
        "dense_indices": dense,
        "selected_indices": selected,
        "quality_flags": flags,
    }
