from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from sqlmodel import Session

from app.llm.gateway import chat
from app.models import Scenario
from app.schemas import Dossier, WriterCritique
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

    # Step 3: ask the writer model for the first complete dossier.
    dossier = await _generate_draft(
        topic=normalized_topic,
        search_results=search_results,
        critique_issues=[],
        trace_id=f"{trace_id}-draft-0",
    )

    # Step 4 — Reflection（反思）能力：用独立 prompt 和第二次 LLM 调用审查
    # guard 梯度、unlock_hint 可操作性，以及 facts 与 surface_bio 的一致性。
    for reflection_round in range(MAX_REGENERATION_ROUNDS + 1):
        critique = await _criticize_dossier(
            dossier,
            trace_id=f"{trace_id}-reflection-{reflection_round}",
        )
        if _critique_approved(dossier, critique):
            break
        if reflection_round == MAX_REGENERATION_ROUNDS:
            break
        dossier = await _generate_draft(
            topic=normalized_topic,
            search_results=search_results,
            critique_issues=critique.issues,
            trace_id=f"{trace_id}-draft-{reflection_round + 1}",
        )

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
