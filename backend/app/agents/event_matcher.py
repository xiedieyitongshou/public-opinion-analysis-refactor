"""Hybrid event matching and resolution for Day 39."""

from __future__ import annotations

import hashlib
import math
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
            status = "partial" if errors else "succeeded"
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
            quality_flags=quality_flags,
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
        ranked = [
            self.rerank_candidate(
                signal=signal,
                candidate=candidate,
                config=config,
                source_refs=source_refs,
            )
            for candidate in candidates
        ]
        ranked.sort(key=lambda item: item.confidence, reverse=True)

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
            match_features_json=MatchFeatures(),
            review_required=signal.is_weak_signal,
        )

    def rerank_candidate(
        self,
        *,
        signal: EventSignal,
        candidate: RetrievedEventMatchCandidate,
        config: EventMatchConfig,
        source_refs: list[SourceSignalMatchRef] | None = None,
    ) -> RerankEventMatchResult:
        features = build_match_features(
            signal=signal,
            candidate=candidate,
            config=config,
            source_refs=source_refs or [],
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
        if "entity_conflict" in features.guardrail_flags:
            return "reject", "entity conflict blocks merge", False
        if "time_conflict" in features.guardrail_flags:
            return "reject", "time window conflict blocks merge", False
        if features.id_match or features.url_match:
            return "merge", "hard ID or URL match reuses existing event_id", False
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
) -> MatchFeatures:
    signal_title = normalize_text(signal.title)
    candidate_title = normalize_text(candidate.title)
    keyword_overlap = overlap_ratio(signal.keywords, candidate.keywords)
    entity_overlap = overlap_ratio(signal.entities, candidate.entities)
    action_overlap = overlap_ratio(signal.action_terms, candidate.action_terms)
    ngram_overlap = char_ngram_overlap(signal.event_text_for_match, candidate_match_text(candidate))
    bm25_score = bm25_like_score(signal.event_text_for_match, candidate_match_text(candidate))
    embedding_similarity = None
    if config.use_embedding:
        embedding_similarity = cosine_text_similarity(
            signal.semantic_fingerprint.event_text_for_embedding or signal.event_text_for_match,
            candidate.event_text_for_embedding or candidate_match_text(candidate),
        )
    time_distance = time_distance_hours(signal.event_time_hint, candidate_time(candidate))
    id_match = bool(
        signal.event_signal_id in candidate.event_signal_ids
        or set(signal.source_signal_ids).intersection(candidate.source_signal_ids)
        or hard_platform_id_match(source_refs or [], candidate.platform_ids)
    )
    url_match = hard_url_match(source_refs or [], candidate.source_urls)
    id_match = bool(id_match)
    url_match = bool(url_match)
    title_containment = bool(
        signal_title
        and candidate_title
        and (signal_title in candidate_title or candidate_title in signal_title)
    )

    matched_by: list[MatchMethod] = []
    if id_match:
        matched_by.append("id_match")
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

    flags: list[str] = []
    if signal.entities and candidate.entities and entity_overlap == 0:
        flags.append("entity_conflict")
    if time_distance is not None and time_distance > config.time_window_same_event_hours:
        flags.append("time_conflict")
    if (
        embedding_similarity is not None
        and embedding_similarity >= config.embedding_auto_merge_min_similarity
        and ("entity_conflict" in flags or "time_conflict" in flags)
    ):
        flags.extend(["embedding_conflict", "semantic_false_positive_risk"])
    if (
        embedding_similarity is not None
        and embedding_similarity >= config.embedding_auto_merge_min_similarity
        and not _has_any_hard_constraint(entity_overlap, action_overlap, time_distance, config)
    ):
        flags.append("embedding_only_without_hard_constraint")

    hard_constraints_passed = _has_any_hard_constraint(
        entity_overlap,
        action_overlap,
        time_distance,
        config,
    )

    return MatchFeatures(
        id_match=id_match,
        url_match=url_match,
        title_containment=title_containment,
        keyword_overlap=keyword_overlap,
        ngram_overlap=ngram_overlap,
        bm25_score=bm25_score,
        embedding_similarity=embedding_similarity,
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
    if features.id_match or features.url_match:
        return 0.98
    if "entity_conflict" in features.guardrail_flags or "time_conflict" in features.guardrail_flags:
        return 0.0
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
    query_tokens = tokenize(query)
    doc_tokens = tokenize(document)
    if not query_tokens or not doc_tokens:
        return 0.0
    doc_counts = {token: doc_tokens.count(token) for token in set(doc_tokens)}
    score = 0.0
    for token in set(query_tokens):
        tf = doc_counts.get(token, 0)
        if tf:
            score += (tf * 2.2) / (tf + 1.2)
    return clip(score / max(1.0, len(set(query_tokens))))


def cosine_text_similarity(left: str, right: str) -> float:
    left_tokens = tokenize(left)
    right_tokens = tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    vocab = sorted(set(left_tokens) | set(right_tokens))
    left_vec = [left_tokens.count(token) for token in vocab]
    right_vec = [right_tokens.count(token) for token in vocab]
    dot = sum(left * right for left, right in zip(left_vec, right_vec, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left_vec))
    right_norm = math.sqrt(sum(value * value for value in right_vec))
    if not left_norm or not right_norm:
        return 0.0
    return clip(dot / (left_norm * right_norm))


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
