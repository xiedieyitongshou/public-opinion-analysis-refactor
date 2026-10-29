"""Exclusive LLM extraction, relation checks and event resolution (no score fusion)."""

import hashlib
from datetime import datetime

from app.core.config import settings
from app.schemas.llm import (
    EXTRACTION_PROMPT,
    MATCH_PROMPT,
    RELEVANCE_PROMPT,
    LLMBatchDecision,
    LLMExtraction,
    LLMRelevance,
)
from app.services.llm_client import (
    LLMFailure,
    document,
    get_llm_client,
    validate_quotes,
)


def documents_from_item(item, source_id):
    from app.services.match_documents import value
    from app.services.topic_content import cli_source_url, plain

    title = value(item, "title", default="")
    body = (
        value(item, "content_text", default="")
        or value(item, "content", default="")
        or value(item, "summary", default="")
    )
    docs = [
        document(
            source_id,
            plain(title),
            plain(body),
            published_at=str(value(item, "published_at", default="") or ""),
        )
    ]
    raw = value(item, "raw_payload_json", "raw_payload", default={}) or {}
    samples = (raw.get("cli_enrichment") or {}).get("samples", [])
    seen = set()
    for sample in samples:
        relation = sample.get("relation") or {}
        if relation.get("decision") != "accepted" or relation.get("audit_only"):
            continue
        text = plain(sample.get("text") or "")
        if not text or text in seen:
            continue
        seen.add(text)
        sample_id = (
            sample.get("external_id") or hashlib.sha256(text.encode()).hexdigest()[:16]
        )
        docs.append(
            document(
                f"{source_id}:cli:{sample_id}",
                "",
                text,
                published_at=sample.get("created_at"),
                url=cli_source_url(sample),
            )
        )
        if len(docs) == 4:
            break
    return docs


def extract_signal(signal, run_id, *, client=None):
    from app.agents.event_extractor import _event_signal_id
    from app.schemas.signals import EventSignal

    docs = signal.platform_features.get("llm_documents") or [
        document(
            signal.source_signal_id,
            signal.title,
            signal.platform_features.get("topic_context", ""),
            published_at=str(signal.published_at or ""),
            fetched_at=str(signal.fetched_at),
        )
    ]

    def validate(result):
        facts = [
            *result.subjects,
            *result.actions,
            *result.objects,
            *result.locations,
            *result.keywords,
            result.occurred_at,
            result.event_type,
        ]
        for fact in facts:
            if fact:
                validate_quotes(fact.evidence, docs)
        if result.occurred_at:
            try:
                occurred = datetime.fromisoformat(
                    result.occurred_at.value.replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise LLMFailure("llm_invalid_occurrence_time") from exc
            if occurred.tzinfo is None:
                raise LLMFailure("llm_occurrence_timezone_missing")

    result, audit = (client or get_llm_client()).complete(
        "event_extraction",
        EXTRACTION_PROMPT,
        {"documents": docs},
        LLMExtraction,
        validate,
    )
    facts = result.model_dump(mode="json")
    summary = "\n".join(doc["body"] for doc in docs if doc["body"])
    return EventSignal(
        event_signal_id=_event_signal_id(run_id, signal.source_signal_id),
        source_signal_ids=[signal.source_signal_id],
        title=signal.title,
        summary=summary[:6000],
        has_accepted_cli_context=len(docs) > 1,
        keywords=[x.value for x in result.keywords],
        entities=[x.value for x in [*result.subjects, *result.objects]][:8],
        action_terms=[x.value for x in result.actions],
        event_type=result.event_type.value if result.event_type else None,
        event_time_hint=result.occurred_at.value if result.occurred_at else None,
        event_text_for_match=signal.title + "\n" + summary[:6000],
        confidence=0.9 if result.event_specific else 0.55,
        is_weak_signal=not result.event_specific,
        weak_signal_reason=None
        if result.event_specific
        else "llm_event_information_insufficient",
        source_statuses=[signal.source_status],
        source_roles=[signal.signal_role],
        platforms=[signal.platform],
        quality_flags=[*signal.quality_flags, "llm_extraction_used"],
        reasoning_mode="llm",
        reasoning_detail={"facts": facts, "audit": audit},
    )


def extract_signals(input_data):
    from app.schemas.signals import ExtractEventSignalsOutput

    signals, audit_ids, errors = [], [], []
    client = get_llm_client()
    for source in input_data.source_signals:
        if source.audit_only or not source.contributes_to_classification:
            audit_ids.append(source.source_signal_id)
            continue
        if not source.title.strip():
            errors.append(f"{source.source_signal_id}: missing title")
            continue
        try:
            signals.append(extract_signal(source, input_data.run_id, client=client))
        except LLMFailure as exc:
            errors.append(f"{source.source_signal_id}: {exc}")
    status = (
        ("partial" if errors else "succeeded")
        if signals
        else "failed"
        if errors
        else "skipped"
    )
    return ExtractEventSignalsOutput(
        run_id=input_data.run_id,
        status=status,
        event_signals=signals,
        audit_only_source_signal_ids=audit_ids,
        errors=errors,
        quality_flags=["llm_extraction_failed"] if errors else ["llm_extraction_used"],
    )


def judge_pairs(incoming, candidates, *, client=None):
    """Evaluate every supplied candidate in bounded batches; never discard by old scores."""
    client = client or get_llm_client()
    results = []
    size = settings.llm_candidates_per_request
    for offset in range(0, len(candidates), size):
        chunk = candidates[offset : offset + size]
        docs = [incoming, *chunk]
        expected = {row["source_id"] for row in chunk}

        def validate(output, expected=expected, docs=docs):
            ids = [row.candidate_id for row in output.judgements]
            if len(ids) != len(expected) or set(ids) != expected:
                raise LLMFailure("llm_candidate_set_mismatch")
            for row in output.judgements:
                relevant_docs = [
                    doc
                    for doc in docs
                    if doc["source_id"]
                    in {
                        incoming["source_id"],
                        row.candidate_id,
                    }
                ]
                required = (
                    (incoming["source_id"], row.candidate_id)
                    if row.decision == "same_event"
                    else ()
                )
                validate_quotes(row.evidence, relevant_docs, required_ids=required)
                if row.decision == "same_event" and row.conflicts:
                    raise LLMFailure("llm_same_event_has_conflicts")

        output, audit = client.complete(
            "event_comparison",
            MATCH_PROMPT,
            {"incoming": incoming, "candidates": chunk},
            LLMBatchDecision,
            validate,
        )
        results.extend((row, audit) for row in output.judgements)
    return results


def relevance(parent, text, *, client=None):
    topic, result = document("topic", parent), document("result", "", text)

    def validate(output):
        required = (
            ("topic", "result")
            if output.decision in {"same_event", "opinion_on_event"}
            else ()
        )
        validate_quotes(output.evidence, [topic, result], required_ids=required)

    output, audit = (client or get_llm_client()).complete(
        "search_relevance",
        RELEVANCE_PROMPT,
        {"topic": topic, "result": result},
        LLMRelevance,
        validate,
    )
    return output, audit


def evaluate_relevance(
    parent_title,
    text,
    *,
    source,
    parent_candidate_id=None,
    parent_signal_id=None,
    user_query_id=None,
    quality_flags=(),
):
    from app.schemas.signals import SearchEnrichmentRelation

    flags = list(quality_flags)
    structural = sorted(
        set(flags) & {"historical_content_pollution", "time_window_conflict"}
    )
    audit = {}
    if structural:
        decision, score, matched, rejected = "rejected", 0.0, [], structural
    else:
        try:
            output, audit = relevance(parent_title, text)
            audit = {**audit, "judgement": output.model_dump(mode="json")}
            if output.decision in {"same_event", "opinion_on_event"}:
                decision, score, matched, rejected = (
                    "accepted",
                    1.0,
                    ["llm_relevance"],
                    [],
                )
            elif output.decision in {"unrelated", "related_but_distinct"}:
                decision, score, matched, rejected = (
                    "rejected",
                    0.0,
                    [],
                    ["llm_unrelated_result"],
                )
            else:
                decision, score, matched, rejected = "audit_only", 0.0, [], []
                flags.append("llm_relevance_insufficient")
        except LLMFailure as exc:
            decision, score, matched, rejected = "audit_only", 0.0, [], []
            flags.append(str(exc))
    return SearchEnrichmentRelation(
        source=source,
        parent_candidate_id=parent_candidate_id,
        parent_signal_id=parent_signal_id,
        user_query_id=user_query_id,
        parent_title=parent_title,
        enrichment_title=text[:500],
        decision=decision,
        confidence=score,
        matched_by=matched,
        rejected_by=rejected,
        quality_flags=sorted({*flags, *rejected}),
        audit_only=decision != "accepted",
        reasoning_detail={"mode": "llm", **audit},
    )


def resolve_signal(signal, candidates, source_refs):
    from app.agents.event_matcher import build_event_fingerprint, build_event_id
    from app.schemas.matching import EventResolution, MatchFeatures

    fingerprint = build_event_fingerprint(signal, source_refs=source_refs)
    new_id = build_event_id(signal, fingerprint)
    # Identity only affects fingerprint/ownership; every semantic relation is decided by LLM.
    features = MatchFeatures(reasoning_mode="llm")
    if not candidates:
        return EventResolution(
            event_signal_id=signal.event_signal_id,
            event_id=new_id,
            event_fingerprint=fingerprint,
            action="create",
            confidence=0.75,
            reason="LLM backend: no existing event; keep independent topic",
            match_features_json=features,
        )
    incoming = document(
        "incoming",
        signal.title,
        signal.summary,
        occurred_at=str(signal.event_time_hint or ""),
    )
    docs = [
        document(candidate.event_id, candidate.title, candidate.summary)
        for candidate in candidates
    ]
    try:
        judgements = judge_pairs(incoming, docs)
    except LLMFailure as exc:
        features.semantic_quality_flags = [str(exc)]
        return EventResolution(
            event_signal_id=signal.event_signal_id,
            event_id=new_id,
            event_fingerprint=fingerprint,
            action="candidate_review",
            confidence=0.0,
            reason=f"LLM comparison unavailable; no rule fallback: {exc}",
            review_required=True,
            blocks_auto_analysis=True,
            blocks_publish=True,
            match_features_json=features,
        )
    same = [row for row, _ in judgements if row.decision == "same_event"]
    uncertain = [
        row for row, _ in judgements if row.decision == "insufficient_evidence"
    ]
    features.llm_detail = {
        "comparisons": [
            {"judgement": row.model_dump(mode="json"), "audit": audit}
            for row, audit in judgements
        ]
    }
    if len(same) == 1 and not uncertain:
        selected, action, review = same[0], "merge", False
        features.semantic_event_support = True
        features.hard_constraints_passed = True
        features.matched_by = ["llm_event_judgement"]
        score = 1.0  # Categorical acceptance marker, not an LLM probability.
    elif same or uncertain:
        selected = (same or uncertain)[0]
        action, review, score = "candidate_review", True, 0.6
        features.matched_by = ["llm_event_judgement"]
    else:
        selected, action, review, score = None, "create", False, 0.75
    return EventResolution(
        event_signal_id=signal.event_signal_id,
        event_id=selected.candidate_id if selected else new_id,
        event_fingerprint=fingerprint,
        action=action,
        confidence=score,
        matched_candidate_event_id=selected.candidate_id if selected else None,
        reason=(
            "LLM: " + selected.reason
            if selected
            else "LLM: no candidate describes the same event"
        )
        + ("; competing or insufficient candidates require review" if review else ""),
        review_required=review,
        matched_by=features.matched_by,
        match_features_json=features,
    )


def match_official(event, event_data, evidence_items, config):
    from app.schemas.official_support import (
        OfficialReference,
        OfficialSupportDetail,
        OfficialSupportResult,
    )
    from app.services.official_support import official_match_document

    target = official_match_document(event)
    incoming = document("incoming", target.title, target.summary)
    docs = [
        document(f"official:{index}", item.title, item.content or item.summary)
        for index, item in enumerate(evidence_items)
    ]
    try:
        judgements = judge_pairs(incoming, docs)
    except LLMFailure as exc:
        return OfficialSupportResult(
            event_id=event_data.event_id,
            official_support_status="not_checked",
            quality_flags=[str(exc), "llm_official_support_unavailable"],
        )
    references, audits = [], []
    for row, audit in judgements:
        index = int(row.candidate_id.split(":")[1])
        item = evidence_items[index]
        detail = {
            "llm_decision": row.decision,
            "llm_judgement": row.model_dump(mode="json"),
            "llm_audit": audit,
        }
        audits.append({"item_id": item.item_id, **detail})
        if row.decision != "same_event" or not item.url or item.published_at is None:
            continue
        references.append(
            OfficialReference(
                item_id=item.item_id,
                title=item.title or "",
                url=item.url,
                source_name=item.source_name,
                source_status=item.source_status or "unknown",
                published_at=item.published_at.isoformat(),
                match_reason="LLM: " + row.reason,
                match_features=detail,
                quality_flags=["llm_official_support_used"]
                + (["fallback_source"] if item.source_status == "fallback" else []),
            )
        )
    uncertain = any(
        row.decision in {"same_event", "insufficient_evidence"} for row, _ in judgements
    )
    strong = any(row.source_status == "use" for row in references)
    status = (
        "supported"
        if strong
        else "weak_supported"
        if references
        else "not_checked"
        if uncertain
        else "not_found"
    )
    return OfficialSupportResult(
        event_id=event_data.event_id,
        official_support_status=status,
        official_references=references,
        official_support_detail=OfficialSupportDetail(
            official_source_count=len({row.source_name for row in references}),
            authority_sources=sorted({row.source_name for row in references}),
            match_quality="strong"
            if strong
            else "weak"
            if references
            else "unknown"
            if uncertain
            else "none",
            matched_item_ids=[row.item_id for row in references if row.item_id],
            candidate_comparisons=audits,
            quality_flags=["llm_official_support_used"],
        ),
        quality_flags=["llm_official_support_used"],
    )
