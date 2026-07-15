from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
import re
from uuid import uuid4

from sqlmodel import Session

from app.llm.gateway import chat
from app.models import Scenario
from app.schemas import Dossier, SourceSnapshot, WriterCritique
from app.tools.search import SearchResult, web_search


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
MAX_REGENERATION_ROUNDS = 2


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
    search_results = await web_search(
        normalized_topic,
        max_results=5,
        fixture_path=search_fixture,
    )

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
        grounding_errors = _grounding_errors(dossier, search_results)
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


def _grounding_errors(
    dossier: Dossier,
    search_results: Sequence[SearchResult],
) -> list[str]:
    """Fail closed unless every training fact is a verbatim sourced excerpt."""

    sources = {result.url: result for result in search_results}
    errors: list[str] = []
    used_source_urls: set[str] = set()
    surface = _normalize_evidence(dossier.surface_bio)
    if not any(
        surface == _normalize_evidence(result.summary)
        for result in search_results
    ):
        errors.append("surface_bio 必须逐字复制一条来源摘要")
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
            if _normalize_evidence(fact.content) == quote:
                matching_quote = True
                if evidence.source_url in used_source_urls:
                    errors.append(f"{fact.id} 与其他 fact 重复使用同一来源")
                used_source_urls.add(evidence.source_url)
        if not matching_quote:
            errors.append(f"{fact.id}.content 必须逐字复制一条 evidence.quote")
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
