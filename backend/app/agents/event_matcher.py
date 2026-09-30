"""Hybrid event matching and resolution for Day 39."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime
from typing import Any

from pydantic import ValidationError

from app.schemas import (
    EventMatchConfig,
    EventResolution,
    EventSignal,
    MatchAndResolveEventsInput,
    MatchAndResolveEventsOutput,
    MatchFeatures,
    MatchMethod,
    RerankEventMatchResult,
    RetrievedEventMatchCandidate,
    SourceSignalMatchRef,
)
from app.services.match_documents import MatchDocument, canonical_ids, event_document
from app.services.matching_engine import Comparison, bm25_scores, compare_many, retrieve

TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


class EventMatcher:
    """Resolve EventSignal records into stable event IDs."""

    def resolve(self, input_data: MatchAndResolveEventsInput) -> MatchAndResolveEventsOutput:
        if not input_data.event_signals:
            return MatchAndResolveEventsOutput(
                run_id=input_data.run_id,
                status="skipped",
                quality_flags=["empty_event_signals"],
            )

        resolutions: list[EventResolution] = []
        errors: list[str] = []
        quality_flags: list[str] = []

        for signal in input_data.event_signals:
            try:
                resolutions.append(
                    self.match_event(
                        signal=signal,
                        candidates=input_data.existing_events,
                        config=input_data.match_config,
                        source_refs_by_signal_id=input_data.source_refs_by_signal_id,
                    )
                )
            except (TypeError, ValueError, ValidationError) as exc:
                errors.append(f"{signal.event_signal_id}: {exc}")

        if resolutions:
            quality_flags.extend(
                flag
                for resolution in resolutions
                for flag in resolution.match_features_json.semantic_quality_flags
            )
            status = "partial" if errors or quality_flags else "succeeded"
        else:
            status = "failed"

        if not input_data.match_config.use_embedding:
            quality_flags.append("embedding_disabled_rule_bm25_ngram_fallback")

        return MatchAndResolveEventsOutput(
            run_id=input_data.run_id,
            status=status,
            event_resolutions=resolutions,
            created_event_count=sum(1 for item in resolutions if item.action == "create"),
            merged_event_count=sum(1 for item in resolutions if item.action == "merge"),
            review_required_count=sum(
                1 for item in resolutions if item.action == "candidate_review"
            ),
            rejected_event_count=sum(1 for item in resolutions if item.action == "reject"),
            quality_flags=dedupe(quality_flags),
            errors=errors,
        )

    def match_event(
        self,
        *,
        signal: EventSignal,
        candidates: list[RetrievedEventMatchCandidate],
        config: EventMatchConfig,
        source_refs_by_signal_id: dict[str, SourceSignalMatchRef] | None = None,
    ) -> EventResolution:
        source_refs = source_refs_for_signal(signal, source_refs_by_signal_id or {})
        fingerprint = build_event_fingerprint(signal, source_refs=source_refs)
        target = event_document(signal)
        target.urls = [ref.url for ref in source_refs if ref.url]
        target.canonical_ids = canonical_ids(target.urls)
        target.canonical_ids += [key for ref in source_refs for key in ref.canonical_ids]
        exact = [
            i
            for i, candidate in enumerate(candidates)
            if signal.event_signal_id in candidate.event_signal_ids
            or set(signal.source_signal_ids) & set(candidate.source_signal_ids)
            or hard_platform_id_match(source_refs, candidate.platform_ids)
            or hard_url_match(source_refs, candidate.source_urls)
            or set(target.canonical_ids)
            & set(candidate.canonical_ids + canonical_ids(candidate.source_urls))
        ]
        # Established identity bypasses neural inference, but still checks contradictions.
        selected, retrieval = retrieve(
            target,
            [event_document(candidate) for candidate in candidates],
            limit=config.retrieval_limit,
            use_embedding=config.use_embedding and not exact,
        )
        # Source IDs remain an exact route even when titles share no lexical tokens.
        selected = exact or selected
        candidates = [candidates[i] for i in selected]
        comparisons = compare_many(
            target,
            [event_document(candidate) for candidate in candidates],
            use_embedding=config.use_embedding and not exact,
            use_reranker=config.use_reranker and not exact,
            time_window_hours=config.time_window_same_event_hours,
        )
        for comparison in comparisons:
            comparison.quality_flags = dedupe(comparison.quality_flags + retrieval["quality_flags"])
        ranked = [
            self.rerank_candidate(
                signal=signal,
                candidate=candidate,
                config=config,
                source_refs=source_refs,
                comparison=comparison,
                bm25_raw_score=retrieval["bm25_scores"][index],
            )
            for index, candidate, comparison in zip(selected, candidates, comparisons, strict=True)
        ]
        ranked.sort(
            key=lambda item: (item.confidence, item.features.rerank_score or 0.0), reverse=True
        )

        if ranked:
            best = ranked[0]
            action, reason, review_required = self._decide(best, signal=signal, config=config)
            event_id = (
                best.candidate.event_id
                if action in {"merge", "candidate_review", "reject"}
                else build_event_id(signal, fingerprint)
            )
            return EventResolution(
                event_signal_id=signal.event_signal_id,
                event_id=event_id,
                event_fingerprint=fingerprint,
                action=action,
                confidence=best.confidence,
                reason=reason,
                matched_by=best.features.matched_by,
                match_features_json=best.features,
                matched_candidate_event_id=best.candidate.event_id,
                review_required=review_required,
            )

        return EventResolution(
            event_signal_id=signal.event_signal_id,
            event_id=build_event_id(signal, fingerprint),
            event_fingerprint=fingerprint,
            action="create",
            confidence=0.75 if not signal.is_weak_signal else 0.55,
            reason="no acceptable existing event candidate; create stable event_id",
            matched_by=[],
            match_features_json=MatchFeatures(semantic_quality_flags=retrieval["quality_flags"]),
            review_required=signal.is_weak_signal,
        )

    def rerank_candidate(
        self,
        *,
        signal: EventSignal,
        candidate: RetrievedEventMatchCandidate,
        config: EventMatchConfig,
        source_refs: list[SourceSignalMatchRef] | None = None,
        comparison: Comparison | None = None,
        bm25_raw_score: float | None = None,
    ) -> RerankEventMatchResult:
        features = build_match_features(
            signal=signal,
            candidate=candidate,
            config=config,
            source_refs=source_refs or [],
            comparison=comparison,
            bm25_raw_score=bm25_raw_score,
        )
        confidence = score_match_confidence(features, signal=signal, config=config)
        reason = build_match_reason(features)
        return RerankEventMatchResult(
            candidate=candidate,
            confidence=confidence,
            features=features,
            reason=reason,
        )

    def _decide(
        self,
        result: RerankEventMatchResult,
        *,
        signal: EventSignal,
        config: EventMatchConfig,
    ) -> tuple[str, str, bool]:
        features = result.features
        if "cluster_conflict" in features.guardrail_flags:
            return "candidate_review", "representative event records conflict", True
        if "entity_conflict" in features.guardrail_flags:
            return "reject", "entity conflict blocks merge", False
        if "time_conflict" in features.guardrail_flags:
            return "reject", "time window conflict blocks merge", False
        if any(flag.endswith("_conflict") for flag in features.guardrail_flags):
            return "reject", "explicit event facts conflict", False
        if features.id_match or features.url_match:
            return "merge", "hard ID or URL match reuses existing event_id", False
        if features.title_containment and signal.is_weak_signal:
            return "candidate_review", "weak title match needs event-specific evidence", True
        if result.confidence >= config.auto_merge_confidence_threshold:
            if signal.is_weak_signal and not _has_entity_or_time_support(features):
                return "candidate_review", "weak signal requires human review", True
            return "merge", result.reason, False
        if result.confidence >= config.candidate_review_confidence_threshold:
            return "candidate_review", result.reason, True
        return "create", "candidate confidence below review threshold; create separate event", False


def build_match_features(
    *,
    signal: EventSignal,
    candidate: RetrievedEventMatchCandidate,
    config: EventMatchConfig,
    source_refs: list[SourceSignalMatchRef] | None = None,
    comparison: Comparison | None = None,
    bm25_raw_score: float | None = None,
) -> MatchFeatures:
    keyword_overlap = overlap_ratio(signal.keywords, candidate.keywords)
    entity_overlap = overlap_ratio(signal.entities, candidate.entities)
    action_overlap = overlap_ratio(signal.action_terms, candidate.action_terms)
    ngram_overlap = char_ngram_overlap(signal.event_text_for_match, candidate_match_text(candidate))
    target = event_document(signal)
    comparison = (
        comparison
        or compare_many(
            target,
            [event_document(candidate)],
            use_embedding=config.use_embedding,
            use_reranker=config.use_reranker,
            time_window_hours=config.time_window_same_event_hours,
        )[0]
    )
    raw_score = (
        bm25_raw_score
        if bm25_raw_score is not None
        else bm25_scores(target.text, [event_document(candidate).text])[0]
    )
    bm25_score = raw_score / (raw_score + 1) if config.use_bm25_ngram else 0.0
    embedding_similarity = comparison.embedding_similarity
    # Observation time is not the time at which an event happened.
    time_distance = time_distance_hours(signal.event_time_hint, candidate.event_time_hint)
    ref_ids = canonical_ids([ref.url for ref in source_refs or [] if ref.url])
    ref_ids += [key for ref in source_refs or [] for key in ref.canonical_ids]
    canonical_match = bool(
        set(ref_ids) & set(candidate.canonical_ids + canonical_ids(candidate.source_urls))
    )
    id_match = bool(
        signal.event_signal_id in candidate.event_signal_ids
        or set(signal.source_signal_ids).intersection(candidate.source_signal_ids)
        or hard_platform_id_match(source_refs or [], candidate.platform_ids)
        or canonical_match
    )
    url_match = hard_url_match(source_refs or [], candidate.source_urls)
    id_match = bool(id_match)
    url_match = bool(url_match)
    title_containment = comparison.title_containment or bool(
        normalize_text(signal.title)
        and normalize_text(signal.title) == normalize_text(candidate.title)
    )

    matched_by: list[MatchMethod] = []
    if id_match:
        matched_by.append("id_match")
    if canonical_match:
        matched_by.append("canonical_id")
    if url_match:
        matched_by.append("url_match")
    if title_containment:
        matched_by.append("title_containment")
    if keyword_overlap >= config.keyword_overlap_high:
        matched_by.append("keyword_overlap")
    if ngram_overlap >= config.ngram_overlap_high or bm25_score >= config.bm25_candidate_min_score:
        matched_by.append("bm25_ngram")
    if entity_overlap > 0 and _has_no_time_conflict(time_distance, config):
        matched_by.append("entity_time_rule")
    if (
        embedding_similarity is not None
        and embedding_similarity >= config.embedding_candidate_min_similarity
    ):
        matched_by.append("embedding_rerank")
    if comparison.rerank_score is not None:
        matched_by.append("cross_encoder")

    flags: list[str] = list(comparison.conflicts)
    if signal.entities and candidate.entities and entity_overlap == 0:
        flags.append("entity_conflict")
    if time_distance is not None and time_distance > config.time_window_same_event_hours:
        flags.append("time_conflict")
    if "explicit_date_conflict" in flags:
        flags.append("time_conflict")
    if candidate.representative_documents and not (id_match or url_match):
        representatives = [
            MatchDocument.model_validate(doc) for doc in candidate.representative_documents
        ]
        checks = compare_many(
            target, representatives, time_window_hours=config.time_window_same_event_hours
        )
        if any(check.conflicts for check in checks):
            flags.append("cluster_conflict")
    if (
        embedding_similarity is not None
        and embedding_similarity >= config.embedding_auto_merge_min_similarity
        and ("entity_conflict" in flags or "time_conflict" in flags)
    ):
        flags.extend(["embedding_conflict", "semantic_false_positive_risk"])
    if (
        embedding_similarity is not None
        and embedding_similarity >= config.embedding_auto_merge_min_similarity
        and not (id_match or url_match)
        and not _has_any_hard_constraint(entity_overlap, action_overlap, time_distance, config)
    ):
        flags.append("embedding_only_without_hard_constraint")

    hard_constraints_passed = (
        id_match
        or url_match
        or _has_any_hard_constraint(
            entity_overlap,
            action_overlap,
            time_distance,
            config,
        )
    )

    return MatchFeatures(
        id_match=id_match,
        url_match=url_match,
        title_containment=title_containment,
        keyword_overlap=keyword_overlap,
        ngram_overlap=ngram_overlap,
        bm25_score=bm25_score,
        bm25_raw_score=raw_score,
        embedding_similarity=embedding_similarity,
        rerank_score=comparison.rerank_score,
        semantic_quality_flags=comparison.quality_flags,
        missing_features=comparison.missing,
        entity_overlap=entity_overlap,
        action_overlap=action_overlap,
        time_distance_hours=time_distance,
        hard_constraints_passed=hard_constraints_passed,
        guardrail_flags=dedupe(flags),
        matched_by=dedupe(matched_by),
    )


def score_match_confidence(
    features: MatchFeatures,
    *,
    signal: EventSignal,
    config: EventMatchConfig,
) -> float:
    if any(flag.endswith("_conflict") for flag in features.guardrail_flags):
        return 0.0
    if features.id_match or features.url_match:
        return 0.98
    score = 0.0
    if features.title_containment:
        score += 0.35
    score += 0.20 * features.keyword_overlap
    score += 0.20 * max(features.ngram_overlap, features.bm25_score)
    score += 0.15 * features.entity_overlap
    score += 0.10 * features.action_overlap
    if features.time_distance_hours is not None and _time_within_window(
        features.time_distance_hours, config
    ):
        score += 0.10
    if features.embedding_similarity is not None:
        score += 0.15 * features.embedding_similarity
    if features.rerank_score is not None:
        # Cross-encoder relevance still needs event-specific lexical/entity agreement.
        anchored = (
            features.entity_overlap > 0
            and features.action_overlap > 0
            or features.title_containment
        )
        if anchored and features.rerank_score >= config.rerank_auto_merge_min_score:
            score = max(score, features.rerank_score)
        elif features.rerank_score >= config.rerank_auto_merge_min_score:
            # Relevance without specific event anchors is a review candidate, not proof.
            score = max(score, config.candidate_review_confidence_threshold)
        elif features.rerank_score < 0.20 and not features.title_containment:
            score = min(score, config.candidate_review_confidence_threshold - 0.01)
    if signal.is_weak_signal:
        score -= 0.10
    if "embedding_only_without_hard_constraint" in features.guardrail_flags:
        score = min(score, config.candidate_review_confidence_threshold)
    return clip(score)


def build_match_reason(features: MatchFeatures) -> str:
    if features.id_match or features.url_match:
        return "hard ID or URL match"
    if features.guardrail_flags:
        return "blocked or downgraded by guardrail: " + ",".join(features.guardrail_flags)
    if features.matched_by:
        return "matched by " + ",".join(features.matched_by)
    return "low feature overlap"


def build_event_fingerprint(
    signal: EventSignal,
    *,
    source_refs: list[SourceSignalMatchRef] | None = None,
) -> str:
    date_bucket = date_bucket_for_signal(signal)
    source_parts = [
        normalize_term(part)
        for ref in source_refs or []
        for part in (ref.platform_id or "", normalize_url(ref.url))
        if normalize_term(part)
    ]
    parts = [
        *sorted(normalize_term(item) for item in signal.entities if normalize_term(item)),
        *sorted(normalize_term(item) for item in signal.action_terms if normalize_term(item)),
        *[normalize_term(item) for item in signal.keywords[:5] if normalize_term(item)],
        date_bucket,
        *sorted(source_parts),
    ]
    raw = "|".join(part for part in parts if part)
    if not raw:
        raw = normalize_text(signal.title) or signal.event_signal_id
    return hashlib.sha256(raw.encode()).hexdigest()


def build_event_id(signal: EventSignal, event_fingerprint: str) -> str:
    bucket = date_bucket_for_signal(signal).replace("-", "")
    if not bucket:
        bucket = datetime.now(UTC).date().strftime("%Y%m%d")
    return f"evt_{bucket}_{event_fingerprint[:12]}"


def date_bucket_for_signal(signal: EventSignal) -> str:
    value = parse_datetime(signal.event_time_hint)
    if value is None:
        return ""
    return value.date().isoformat()


def candidate_match_text(candidate: RetrievedEventMatchCandidate) -> str:
    return candidate.event_text_for_match or " ".join(
        [
            candidate.title,
            *candidate.keywords,
            *candidate.entities,
            *candidate.action_terms,
            candidate.event_type or "",
        ]
    )


def candidate_time(candidate: RetrievedEventMatchCandidate) -> datetime | str | None:
    return candidate.event_time_hint or candidate.first_seen_at or candidate.last_seen_at


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", value.lower())


def normalize_term(value: str) -> str:
    return re.sub(r"\s+", "", value.lower().strip())


def normalize_url(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().lower().rstrip("/")


def tokenize(value: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(value or "")]


def overlap_ratio(left: list[str], right: list[str]) -> float:
    left_set = {normalize_term(item) for item in left if normalize_term(item)}
    right_set = {normalize_term(item) for item in right if normalize_term(item)}
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def char_ngram_overlap(left: str, right: str, *, min_n: int = 2, max_n: int = 4) -> float:
    left_set = char_ngrams(left, min_n=min_n, max_n=max_n)
    right_set = char_ngrams(right, min_n=min_n, max_n=max_n)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def char_ngrams(value: str, *, min_n: int, max_n: int) -> set[str]:
    text = normalize_text(value)
    result: set[str] = set()
    for size in range(min_n, max_n + 1):
        result.update(text[index : index + size] for index in range(len(text) - size + 1))
    return result


def bm25_like_score(query: str, document: str) -> float:
    """Compatibility helper; retrieval uses the full candidate corpus instead."""
    score = bm25_scores(query, [document])[0]
    return score / (score + 1)


def cosine_text_similarity(left: str, right: str) -> float:
    """Real neural similarity; unavailable weights are never replaced with word counts."""
    result = compare_many(
        MatchDocument(title=left), [MatchDocument(title=right)], use_embedding=True
    )[0]
    if result.embedding_similarity is None:
        raise RuntimeError(";".join(result.quality_flags))
    return result.embedding_similarity


def time_distance_hours(left: datetime | str | None, right: datetime | str | None) -> float | None:
    left_dt = parse_datetime(left)
    right_dt = parse_datetime(right)
    if left_dt is None or right_dt is None:
        return None
    return abs((left_dt - right_dt).total_seconds()) / 3600


def parse_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_aware(value)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=UTC)
    try:
        return ensure_aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=UTC)
    return value


def _time_within_window(distance_hours: float | None, config: EventMatchConfig) -> bool:
    return distance_hours is not None and distance_hours <= config.time_window_same_event_hours


def _has_no_time_conflict(distance_hours: float | None, config: EventMatchConfig) -> bool:
    return distance_hours is None or distance_hours <= config.time_window_same_event_hours


def _has_any_hard_constraint(
    entity_overlap: float,
    action_overlap: float,
    time_distance: float | None,
    config: EventMatchConfig,
) -> bool:
    return entity_overlap > 0 or action_overlap > 0 or _time_within_window(time_distance, config)


def _has_hard_support(features: MatchFeatures) -> bool:
    return (
        features.id_match
        or features.url_match
        or features.entity_overlap > 0
        or features.action_overlap > 0
        or features.time_distance_hours is not None
    )


def _has_entity_or_time_support(features: MatchFeatures) -> bool:
    return features.entity_overlap > 0 or features.time_distance_hours is not None


def source_refs_for_signal(
    signal: EventSignal,
    source_refs_by_signal_id: dict[str, SourceSignalMatchRef],
) -> list[SourceSignalMatchRef]:
    return [
        source_refs_by_signal_id[source_signal_id]
        for source_signal_id in signal.source_signal_ids
        if source_signal_id in source_refs_by_signal_id
    ]


def hard_url_match(source_refs: list[SourceSignalMatchRef], candidate_urls: list[str]) -> bool:
    source_urls = {normalize_url(ref.url) for ref in source_refs if normalize_url(ref.url)}
    candidate_url_set = {normalize_url(url) for url in candidate_urls if normalize_url(url)}
    return bool(source_urls and source_urls.intersection(candidate_url_set))


def hard_platform_id_match(
    source_refs: list[SourceSignalMatchRef],
    candidate_platform_ids: list[str],
) -> bool:
    source_ids = {normalize_term(ref.platform_id or "") for ref in source_refs if ref.platform_id}
    candidate_ids = {normalize_term(platform_id) for platform_id in candidate_platform_ids}
    return bool(source_ids and source_ids.intersection(candidate_ids))


def clip(value: float) -> float:
    return min(1.0, max(0.0, round(value, 4)))


def dedupe(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[Any] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
