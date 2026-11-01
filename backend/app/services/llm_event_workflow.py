"""Exclusive LLM extraction, relation checks and event resolution (no score fusion)."""

import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime

from app.core.config import settings
from app.schemas.llm import (
    EXTRACTION_PROMPT,
    MATCH_PROMPT,
    RELEVANCE_PROMPT,
    LLMBatchDecision,
    LLMExtraction,
    LLMPairDecision,
    LLMRelevance,
)
from app.services.llm_client import (
    LLMBudgetExceeded,
    LLMFailure,
    document,
    get_llm_client,
    validate_quotes,
)

logger = logging.getLogger(__name__)
COMPLETE_OCCURRENCE_TIME = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})"
)


def normalize_occurrence_time(result):
    """Clear only a malformed optional time, after schema and all quotes are valid.

    Never mutate the client response: a cached response is checked and normalized
    in exactly the same way as a fresh one. No time is guessed from metadata.
    """
    fact = result.occurred_at
    if fact is None:
        return result, []
    try:
        occurred = datetime.fromisoformat(fact.value.replace("Z", "+00:00"))
    except ValueError:
        code = "llm_invalid_occurrence_time"
    else:
        if occurred.tzinfo is None:
            code = "llm_occurrence_timezone_missing"
        elif not COMPLETE_OCCURRENCE_TIME.fullmatch(fact.value):
            code = "llm_invalid_occurrence_time"
        else:
            return result, []
    warning = {
        "field": "occurred_at",
        "code": code,
        "action": "set_null",
        "discarded_value": fact.value,
        "evidence": [quote.model_dump(mode="json") for quote in fact.evidence],
    }
    return result.model_copy(update={"occurred_at": None}), [warning]


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

    from app.services.llm_material import canonical_documents, remap_result

    runtime = client or get_llm_client()
    canonical, aliases = canonical_documents(docs, fetched_at=signal.fetched_at)

    def validate_content(result):
        # Rebind before validating: old round IDs can never satisfy new citations.
        validate(remap_result(result, aliases))

    content_payload = {"documents": canonical}
    cached = runtime.read_cached("event_extraction_content", EXTRACTION_PROMPT,
                                 content_payload, LLMExtraction, validate_content)
    if cached:
        result, audit = cached
        result = remap_result(result, aliases)
    else:
        result, audit = runtime.complete("event_extraction", EXTRACTION_PROMPT,
                                         {"documents": docs}, LLMExtraction, validate)
        runtime.store_cached("event_extraction_content", EXTRACTION_PROMPT, content_payload,
                             remap_result(result, {v: k for k, v in aliases.items()}))
    audit = {**audit, "source_id_map": aliases}
    result, warnings = normalize_occurrence_time(result)
    warning_flags = []
    for warning in warnings:
        warning_flags.extend([warning["code"], "llm_occurrence_time_cleared"])
        logger.warning(
            "llm_extraction_warning source_id=%s field=%s code=%s action=set_null",
            signal.source_signal_id, warning["field"], warning["code"],
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
        quality_flags=[*signal.quality_flags, "llm_extraction_used", *warning_flags],
        reasoning_mode="llm",
        reasoning_detail={"facts": facts, "audit": audit, "warnings": warnings},
    )


def extract_signals(input_data):
    from app.schemas.signals import EventSignal, ExtractEventSignalsOutput
    from app.services.analysis_checkpoint import load_checkpoint, save_checkpoint

    previous = load_checkpoint()
    signals = [EventSignal.model_validate(value) for value in previous.get("event_signals", [])]
    audit_ids, errors = [], []
    processed = {key for value in signals for key in value.source_signal_ids}
    client = get_llm_client()
    for source in input_data.source_signals:
        if source.source_signal_id in processed:
            continue
        if source.audit_only or not source.contributes_to_classification:
            audit_ids.append(source.source_signal_id)
            continue
        if not source.title.strip():
            errors.append(f"{source.source_signal_id}: missing title")
            continue
        try:
            signals.append(extract_signal(source, input_data.run_id, client=client))
            processed.add(source.source_signal_id)
            save_checkpoint(ExtractEventSignalsOutput(
                run_id=input_data.run_id, status="partial", event_signals=signals,
                audit_only_source_signal_ids=audit_ids, errors=errors,
            ), processed)
        except LLMBudgetExceeded:
            raise
        except LLMFailure as exc:
            errors.append(f"{source.source_signal_id}: {exc}")
    status = (
        ("partial" if errors else "succeeded")
        if signals
        else "failed"
        if errors
        else "skipped"
    )
    quality_flags = ["llm_extraction_failed"] if errors else ["llm_extraction_used"]
    for signal in signals:
        quality_flags.extend(flag for flag in signal.quality_flags if flag.startswith("llm_"))
    return ExtractEventSignalsOutput(
        run_id=input_data.run_id,
        status=status,
        event_signals=signals,
        audit_only_source_signal_ids=audit_ids,
        errors=errors,
        quality_flags=list(dict.fromkeys(quality_flags)),
    )


def validate_pair(row, incoming, candidate):
    if row.candidate_id != candidate["source_id"]:
        raise LLMFailure("llm_candidate_set_mismatch")
    docs = [incoming, candidate]
    required = ((incoming["source_id"], candidate["source_id"])
                if row.decision == "same_event" else ())
    validate_quotes(row.evidence, docs, required_ids=required)
    if row.decision == "same_event" and row.conflicts:
        raise LLMFailure("llm_same_event_has_conflicts")
    validate_quotes(row.same_event_clues, docs)
    if row.decision == "insufficient_evidence":
        if row.uncertainty_kind is None:
            raise LLMFailure("llm_uncertainty_kind_missing")
        if row.uncertainty_kind == "possible_same_event":
            validate_quotes(row.same_event_clues, docs,
                            required_ids=(incoming["source_id"], candidate["source_id"]))
            if not any(value.strip() for value in row.missing_information):
                raise LLMFailure("llm_uncertainty_missing_critical_detail")
        elif row.same_event_clues:
            raise LLMFailure("llm_context_only_has_same_event_clues")


def judge_pairs(incoming, candidates, *, client=None):
    """Cache each pair independently; pack only misses, compare in bounded parallel batches."""
    from app.services.llm_material import canonical_pair, remap_result

    client = client or get_llm_client()
    ids = [c["source_id"] for c in candidates]
    if len(set(ids)) != len(ids) or incoming["source_id"] in ids:
        raise LLMFailure("llm_candidate_set_mismatch")
    found, pending = {}, []
    for candidate in candidates:
        payload = canonical_pair(incoming, candidate)

        def validate_cached(row, payload=payload):
            validate_pair(row, payload["incoming"], payload["candidate"])

        cached = client.read_cached("event_pair", MATCH_PROMPT, payload,
                                    LLMPairDecision, validate_cached)
        if cached:
            row, audit = cached
            row = remap_result(row, {"incoming": incoming["source_id"],
                                     "candidate": candidate["source_id"]})
            validate_pair(row, incoming, candidate)
            found[candidate["source_id"]] = (row, audit)
        else:
            pending.append(candidate)

    def run_batch(chunk):
        by_id = {c["source_id"]: c for c in chunk}

        def validate(output):
            actual = [r.candidate_id for r in output.judgements]
            if len(actual) != len(by_id) or set(actual) != set(by_id):
                raise LLMFailure("llm_candidate_set_mismatch")
            for row in output.judgements:
                validate_pair(row, incoming, by_id[row.candidate_id])

        output, audit = client.complete("event_comparison", MATCH_PROMPT,
                                        {"incoming": incoming, "candidates": chunk},
                                        LLMBatchDecision, validate)
        result = []
        for row in output.judgements:
            candidate = by_id[row.candidate_id]
            canonical = remap_result(row, {incoming["source_id"]: "incoming",
                                          candidate["source_id"]: "candidate"})
            client.store_cached("event_pair", MATCH_PROMPT,
                                canonical_pair(incoming, candidate), canonical)
            result.append((row, audit))
        return result

    size = settings.llm_candidates_per_request
    chunks = [pending[i:i+size] for i in range(0, len(pending), size)]
    errors = []
    if len(chunks) > 1 and settings.llm_max_concurrent_requests > 1:
        with ThreadPoolExecutor(max_workers=settings.llm_max_concurrent_requests) as pool:
            futures = [pool.submit(copy_context().run, run_batch, chunk) for chunk in chunks]
            for future in futures:
                try:
                    for row, audit in future.result():
                        found[row.candidate_id] = (row, audit)
                except LLMFailure as exc:
                    errors.append(exc)
    else:
        for chunk in chunks:
            try:
                for row, audit in run_batch(chunk):
                    found[row.candidate_id] = (row, audit)
            except LLMFailure as exc:
                errors.append(exc)
                if isinstance(exc, LLMBudgetExceeded):
                    break
    logger.info("llm_pairs candidates=%d cache_hits=%d pending=%d batches=%d",
                len(candidates), len(candidates)-len(pending), len(pending), len(chunks))
    if errors:
        # Successful other batches remain cached, but unresolved competitors block merging.
        raise next((e for e in errors if isinstance(e, LLMBudgetExceeded)), errors[0])
    return [found[key] for key in ids]


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


REVIEW_POLICY_VERSION = "qualified-uncertainty-v1"


def comparison_groups(judgements):
    """Route only the LLM's validated, event-specific uncertainty to semantic review.

    Retain unclassified legacy uncertainty conservatively. Absence of a new field
    in an old response is not evidence that the model found no same-event clues.
    """
    groups = {"same": [], "plausible": [], "context_only": [], "legacy": []}
    for row, _ in judgements:
        if row.decision == "same_event":
            groups["same"].append(row)
        elif row.decision == "insufficient_evidence":
            name = {
                "possible_same_event": "plausible", "insufficient_context": "context_only",
            }.get(row.uncertainty_kind, "legacy")
            groups[name].append(row)
    return groups


def resolve_signal(signal, candidates, source_refs):
    from app.agents.event_matcher import build_event_fingerprint, build_event_id
    from app.schemas.matching import EventResolution, MatchFeatures

    fingerprint = build_event_fingerprint(signal, source_refs=source_refs)
    new_id = build_event_id(signal, fingerprint)
    from app.agents.event_matcher import hard_platform_id_match
    from app.services.llm_material import source_url_match
    from app.services.match_documents import canonical_ids

    features = MatchFeatures(reasoning_mode="llm")
    source_keys = {key for ref in source_refs for key in (
        *ref.canonical_ids, *canonical_ids([ref.url] if ref.url else []))}
    exact = [candidate for candidate in candidates if (
        hard_platform_id_match(source_refs, candidate.platform_ids)
        or source_url_match(source_refs, candidate.source_urls)
        or source_keys.intersection(candidate.canonical_ids + canonical_ids(candidate.source_urls))
        or set(signal.source_signal_ids).intersection(candidate.source_signal_ids)
    )]
    if exact:
        target = exact[0]
        conflict = len({candidate.event_id for candidate in exact}) != 1
        features.id_match = bool(
            hard_platform_id_match(source_refs, target.platform_ids)
            or source_keys.intersection(target.canonical_ids + canonical_ids(target.source_urls))
            or set(signal.source_signal_ids).intersection(target.source_signal_ids))
        features.url_match = source_url_match(source_refs, target.source_urls)
        features.hard_constraints_passed = not conflict
        features.matched_by = ["id_match" if features.id_match else "url_match"]
        features.llm_detail = {"review_policy": REVIEW_POLICY_VERSION,
                               "review_category": (
                                   "competing_matches" if conflict else "identity_reuse"),
                               "identity_targets": [c.event_id for c in exact]}
        return EventResolution(
            event_signal_id=signal.event_signal_id, event_id=target.event_id,
            event_fingerprint=fingerprint, action="candidate_review" if conflict else "merge",
            confidence=0.6 if conflict else 1.0, review_required=conflict,
            matched_candidate_event_id=target.event_id, matched_by=features.matched_by,
            reason=("Source identity has competing owners" if conflict
                    else "Reuse established source ID/URL ownership"),
            match_features_json=features,
        )
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
    from app.services.llm_candidates import select_candidates

    retrieval = {}
    try:
        docs, retrieval = select_candidates(incoming, docs)
        judgements = judge_pairs(incoming, docs)
    except LLMBudgetExceeded:
        raise
    except LLMFailure as exc:
        features.semantic_quality_flags = [str(exc)]
        features.llm_detail = {
            "review_policy": REVIEW_POLICY_VERSION, "retrieval": retrieval,
            "review_category": "technical_error", "error_code": str(exc),
        }
        logger.warning("llm_review_policy signal=%s action=technical_review error=%s",
                       signal.event_signal_id, str(exc))
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
    groups = comparison_groups(judgements)
    same = groups["same"]
    uncertain = groups["plausible"] + groups["legacy"]
    features.llm_detail = {
        "review_policy": REVIEW_POLICY_VERSION, "retrieval": retrieval,
        "candidate_groups": {name: [row.candidate_id for row in rows]
                             for name, rows in groups.items()},
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
    category = (
        "competing_matches" if len(same) > 1
        else "legacy_uncertainty" if groups["legacy"]
        else "plausible_uncertainty" if uncertain
        else "automatic_merge" if same else "independent_topic"
    )
    features.llm_detail["review_category"] = category
    if groups["context_only"]:
        features.semantic_quality_flags.append("llm_context_only_candidates_retained")
    logger.info(
        "llm_review_policy signal=%s action=%s category=%s same=%d plausible=%d "
        "context_only=%d legacy=%d",
        signal.event_signal_id, action, category, len(same), len(groups["plausible"]),
        len(groups["context_only"]), len(groups["legacy"]),
    )
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
        + ("; concrete competing or critical uncertain candidates require review" if review else "")
        + ("; context-only uncertainty retained without a merge association"
           if groups["context_only"] else ""),
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
        document(f"official:{index}", item.title, item.content or item.summary,
                 published_at=item.published_at.isoformat() if item.published_at else None,
                 url=item.url)
        for index, item in enumerate(evidence_items)
    ]
    from app.services.llm_candidates import select_candidates

    retrieval = {}
    try:
        docs, retrieval = select_candidates(incoming, docs)
        judgements = judge_pairs(incoming, docs)
    except LLMBudgetExceeded:
        raise
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
