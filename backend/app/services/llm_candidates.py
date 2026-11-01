"""Sparse/dense recall limits LLM input; scores never decide event identity."""

import logging
from time import perf_counter

from app.core.config import settings
from app.services.llm_client import LLMFailure
from app.services.match_documents import MatchDocument
from app.services.matching_engine import retrieve


def select_candidates(incoming, candidates):
    started = perf_counter()
    if not settings.llm_retrieval_enabled or len(candidates) <= settings.llm_candidate_limit:
        selected, flags = candidates, []
        method = "all_within_limit" if settings.llm_retrieval_enabled else "disabled"
    else:
        target = MatchDocument(title=incoming["title"], summary=incoming["body"])
        docs = [MatchDocument(title=c["title"], summary=c["body"]) for c in candidates]
        indices, audit = retrieve(
            target,
            docs,
            limit=settings.llm_candidate_limit,
            use_embedding=settings.llm_retrieval_use_embedding,
        )
        flags = audit["quality_flags"]
        if flags and settings.llm_retrieval_use_embedding:
            # Do not silently convert a broken dense recall into a negative match.
            raise LLMFailure("llm_candidate_retrieval_unavailable")
        selected = [candidates[i] for i in indices]
        method = "bm25_dense_rrf" if settings.llm_retrieval_use_embedding else "bm25"
    detail = {
        "method": method,
        "pool_count": len(candidates),
        "selected_count": len(selected),
        "candidate_ids": [c["source_id"] for c in selected],
        "excluded_candidate_ids": [c["source_id"] for c in candidates if c not in selected],
        "quality_flags": flags,
        "duration_ms": round((perf_counter() - started) * 1000),
    }
    logging.getLogger(__name__).info(
        "llm_recall method=%s pool=%d selected=%d ms=%d",
        method,
        len(candidates),
        len(selected),
        detail["duration_ms"],
    )
    return selected, detail
