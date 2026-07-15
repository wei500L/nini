from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid4

from sqlmodel import Session

from app.llm.gateway import chat
from app.models import Scenario
from app.schemas import Dossier, FactEvidence, SourceSnapshot, WriterCritique
from app.tools.search import SearchResult, web_search


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
MAX_REGENERATION_ROUNDS = 2
MIN_INFERRED_SOURCE_SIMILARITY = 0.72


async def generate_dossier(
    topic: str,
    *,
    db: Session,
    trace_id: str,
    search_fixture: Path | None = None,
) -> Dossier:
    # Step 1: receive the topic entered by the host or selected from an RSS feed.
    normalized_topic = topic.strip()
    if not normalized_topic:
        raise ValueError("topic cannot be empty")

    # Step 2: gather 3-5 source summaries through the search tool.
    search_results = _deduplicate_search_results(
        await web_search(
            normalized_topic,
            max_results=5,
            fixture_path=search_fixture,
        )
    )
    if len(search_results) < 3:
        raise ValueError("at least 3 distinct public sources are required")

    # Step 3/4: generate, verify source grounding in code, then run the independent
    # Reflection prompt. A rejected or ungrounded dossier is never persisted.
    critique_issues: list[str] = []
    dossier: Dossier | None = None
    approved = False
    for reflection_round in range(MAX_REGENERATION_ROUNDS + 1):
        dossier = await _generate_draft(
            topic=normalized_topic,
            search_results=search_results,
            critique_issues=critique_issues,
            trace_id=f"{trace_id}-draft-{reflection_round}",
        )
        dossier = _attach_sources(dossier, search_results)
        dossier, repair_errors = _canonicalize_grounding(dossier, search_results)
        grounding_errors = [
            *repair_errors,
            *_grounding_errors(dossier, search_results),
        ]
        if grounding_errors:
            critique_issues = grounding_errors
            continue

        critique = await _criticize_dossier(
            dossier,
            trace_id=f"{trace_id}-reflection-{reflection_round}",
        )
        if _critique_approved(dossier, critique):
            approved = True
            break
        critique_issues = critique.issues

    if dossier is None or not approved:
        details = "; ".join(critique_issues[:5]) or "independent review rejected dossier"
        raise ValueError(f"dossier failed truth/playability gate: {details}")

    # IDs are server-owned so a model cannot overwrite an existing scenario.
    dossier = dossier.model_copy(update={"scenario_id": f"scenario-{uuid4().hex}"})

    # Step 5: persist the final dossier after Reflection finishes.
    _save_dossier(db, dossier)
    return dossier


async def _generate_draft(
    *,
    topic: str,
    search_results: Sequence[SearchResult],
    critique_issues: Sequence[str],
    trace_id: str,
) -> Dossier:
    prompt = _render_prompt(
        "writer.md",
        {
            "topic": topic,
            "sources_json": json.dumps(
                [result.model_dump(mode="json") for result in search_results],
                ensure_ascii=False,
                indent=2,
            ),
            "critique_json": json.dumps(
                list(critique_issues),
                ensure_ascii=False,
                indent=2,
            ),
        },
    )
    result = await chat(
        [{"role": "system", "content": prompt}],
        model_tier="smart",
        schema=Dossier,
        trace_id=trace_id,
    )
    return _require_schema(result, Dossier)


async def _criticize_dossier(dossier: Dossier, *, trace_id: str) -> WriterCritique:
    prompt = _render_prompt(
        "writer_critic.md",
        {
            "dossier_json": dossier.model_dump_json(indent=2),
        },
    )
    result = await chat(
        [{"role": "system", "content": prompt}],
        model_tier="smart",
        schema=WriterCritique,
        trace_id=trace_id,
    )
    return _require_schema(result, WriterCritique)


def _critique_approved(dossier: Dossier, critique: WriterCritique) -> bool:
    guards = {fact.guard for fact in dossier.facts}
    structural_minimums_met = len(dossier.facts) >= 4 and len(guards) > 1
    return (
        structural_minimums_met
        and critique.approved
        and critique.guard_gradient_ok
        and critique.unlock_hints_actionable
        and critique.surface_bio_consistent
    )


def _attach_sources(
    dossier: Dossier,
    search_results: Sequence[SearchResult],
) -> Dossier:
    retrieved_at = datetime.now(UTC)
    return dossier.model_copy(
        update={
            "sources": [
                SourceSnapshot(
                    title=result.title,
                    url=result.url,
                    summary=result.summary,
                    retrieved_at=retrieved_at,
                )
                for result in search_results
            ]
        }
    )


def _canonicalize_grounding(
    dossier: Dossier,
    search_results: Sequence[SearchResult],
) -> tuple[Dossier, list[str]]:
    """Repair model-authored citations while keeping stored claims source-verbatim.

    The model chooses which source best supports each training fact. The server owns
    the final wording: it stores the complete source summary as evidence and uses
    only a contiguous excerpt as fact text. Harmless formatting drift is repaired,
    while unsupported additions can never reach the database.
    """

    if not search_results:
        return dossier, ["没有可用于真实性归一化的搜索来源"]

    sources_by_url = {result.url: result for result in search_results}
    surface_source = _closest_source(dossier.surface_bio, search_results)
    surface_bio = _grounded_excerpt(
        surface_source.summary,
        query=f"{dossier.topic} {surface_source.title}",
        max_chars=520,
    )
    used_urls: set[str] = set()
    canonical_facts = []
    repair_errors: list[str] = []

    for fact in dossier.facts:
        evidence = fact.evidence[0] if fact.evidence else None
        requested_source = (
            sources_by_url.get(evidence.source_url) if evidence is not None else None
        )
        if requested_source is not None:
            source = requested_source
        else:
            # A missing or malformed citation may be repaired only when the authored
            # claim itself strongly matches a retrieved source. Never attach an
            # arbitrary real URL to unsupported model prose just to satisfy the gate.
            unused_sources = [
                source for source in search_results if source.url not in used_urls
            ]
            candidates = unused_sources or list(search_results)
            source, similarity = _closest_source_with_score(
                " ".join(
                    part
                    for part in (
                        fact.content,
                        evidence.quote if evidence is not None else "",
                    )
                    if part
                ),
                candidates,
            )
            if similarity < MIN_INFERRED_SOURCE_SIMILARITY:
                repair_errors.append(
                    f"{fact.id} 的来源 URL 无效，且内容无法高置信匹配本次搜索结果"
                )
                canonical_facts.append(fact)
                continue
        used_urls.add(source.url)
        fact_content = _grounded_excerpt(
            source.summary,
            query=f"{fact.unlock_hint} {source.title}",
            max_chars=520,
        )
        canonical_facts.append(
            fact.model_copy(
                update={
                    "content": fact_content,
                    "partial": _source_partial(fact_content),
                    "evidence": [
                        FactEvidence(
                            source_url=source.url,
                            quote=source.summary,
                        )
                    ],
                }
            )
        )

    return (
        dossier.model_copy(
            update={
                "surface_bio": surface_bio,
                "facts": canonical_facts,
            }
        ),
        repair_errors,
    )


def _grounding_errors(
    dossier: Dossier,
    search_results: Sequence[SearchResult],
) -> list[str]:
    """Fail closed unless every training fact is a verbatim sourced excerpt."""

    sources = {result.url: result for result in search_results}
    errors: list[str] = []
    surface = _normalize_evidence(dossier.surface_bio)
    if not surface or not any(
        surface in _normalize_evidence(result.summary)
        for result in search_results
    ):
        errors.append("surface_bio 必须是某条来源摘要中的连续原文片段")
    for fact in dossier.facts:
        if not fact.evidence:
            errors.append(f"{fact.id} 缺少 evidence")
            continue
        matching_quote = False
        for evidence in fact.evidence:
            source = sources.get(evidence.source_url)
            if source is None:
                errors.append(f"{fact.id} 引用了本次搜索结果之外的 URL")
                continue
            quote = _normalize_evidence(evidence.quote)
            if quote != _normalize_evidence(source.summary):
                errors.append(f"{fact.id} 的 evidence.quote 必须是完整来源摘要")
                continue
            normalized_content = _normalize_evidence(fact.content)
            if normalized_content and normalized_content in quote:
                matching_quote = True
        if not matching_quote:
            errors.append(f"{fact.id}.content 必须是 evidence.quote 中的连续原文片段")
    if len(dossier.facts) < 4:
        errors.append("facts 至少需要 4 条")
    return errors


def persisted_grounding_errors(dossier: Dossier) -> list[str]:
    if not dossier.sources:
        return ["dossier 缺少可审计来源快照"]
    return _grounding_errors(
        dossier,
        [
            SearchResult(
                title=source.title,
                url=source.url,
                summary=source.summary,
            )
            for source in dossier.sources
        ],
    )


def _normalize_evidence(value: str) -> str:
    return re.sub(r"\s+", "", value).strip()


def _closest_source(
    value: str,
    candidates: Sequence[SearchResult],
) -> SearchResult:
    return _closest_source_with_score(value, candidates)[0]


def _closest_source_with_score(
    value: str,
    candidates: Sequence[SearchResult],
) -> tuple[SearchResult, float]:
    if not candidates:
        raise ValueError("source candidates cannot be empty")
    normalized_value = _normalize_for_similarity(value)
    scored = [
        (
            source,
            SequenceMatcher(
                None,
                normalized_value,
                _normalize_for_similarity(source.summary),
            ).ratio(),
        )
        for source in candidates
    ]
    return max(
        scored,
        key=lambda item: item[1],
    )


def _normalize_for_similarity(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _source_partial(summary: str) -> str | None:
    sentences = [
        sentence.strip()
        for sentence in re.findall(r".*?(?:[。！？!?]|$)", summary)
        if sentence.strip()
    ]
    if len(sentences) <= 1:
        return None
    return sentences[0]


def _grounded_excerpt(summary: str, *, query: str, max_chars: int) -> str:
    """Select a compact, contiguous excerpt without rewriting retrieved text."""

    sentence_matches = list(
        re.finditer(
            r"[^\n。！？!?.]+(?:[。！？!?.]|$)",
            summary,
        )
    )
    candidates = [
        match
        for match in sentence_matches
        if len(_normalize_for_similarity(match.group(0))) >= 12
    ]
    if not candidates:
        return summary[:max_chars].strip()

    normalized_query = _normalize_for_similarity(query)
    query_chars = set(normalized_query)

    def relevance(match: re.Match[str]) -> float:
        value = _normalize_for_similarity(match.group(0))
        coverage = (
            len(query_chars.intersection(value)) / len(query_chars)
            if query_chars
            else 0.0
        )
        similarity = SequenceMatcher(None, normalized_query, value).ratio()
        information = min(len(value) / 120, 1.0)
        return coverage * 0.55 + similarity * 0.20 + information * 0.25

    best = max(candidates, key=relevance)
    start = best.start()
    end = min(best.end(), start + max_chars)
    candidate_index = candidates.index(best)
    for following in candidates[candidate_index + 1 :]:
        if following.end() - start > max_chars:
            break
        if following.start() - end > 12:
            break
        end = following.end()
    excerpt = summary[start:end].strip()
    return excerpt or summary[:max_chars].strip()


def _deduplicate_search_results(
    search_results: Sequence[SearchResult],
) -> list[SearchResult]:
    unique: list[SearchResult] = []
    seen_urls: set[str] = set()
    for result in search_results:
        normalized_url = result.url.rstrip("/").casefold()
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        unique.append(result)
    return unique


def _save_dossier(db: Session, dossier: Dossier) -> None:
    scenario = db.get(Scenario, dossier.scenario_id)
    if scenario is None:
        scenario = Scenario(
            id=dossier.scenario_id,
            topic=dossier.topic,
            dossier_json=dossier.model_dump(mode="json"),
        )
        db.add(scenario)
    else:
        scenario.topic = dossier.topic
        scenario.dossier_json = dossier.model_dump(mode="json")
        db.add(scenario)
    db.commit()


def _render_prompt(filename: str, values: dict[str, str]) -> str:
    prompt = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
    for placeholder, value in values.items():
        prompt = prompt.replace(f"{{{placeholder}}}", value)
    return prompt


def _require_schema(value: object, schema: type[Dossier] | type[WriterCritique]):
    if not isinstance(value, schema):
        raise TypeError(f"Expected {schema.__name__} from validated LLM gateway")
    return value
