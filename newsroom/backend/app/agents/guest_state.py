from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from app.schemas import Fact, FactState, Persona


GuestAction = Literal["reveal", "partial", "tell", "deflect"]


def update_guard_state(
    fact_states: Sequence[FactState],
    *,
    targeted_fact: str | None,
    previous_targeted_fact: str | None,
    pressure: int,
    initial_guards: Mapping[str, int],
    pressure_response: Literal["standard", "inverse"],
) -> FactState | None:
    """Apply the deterministic guard transition for the current probe.

    The supplied FactState objects are mutated so an orchestrator can persist the
    same objects after the guest turn. A missing target is a general question and
    therefore does not change any fact's state.
    """

    if targeted_fact is None:
        return None

    state = _find_state(fact_states, targeted_fact)
    try:
        initial_guard = initial_guards[targeted_fact]
    except KeyError as error:
        raise KeyError(f"Unknown targeted fact: {targeted_fact}") from error

    if targeted_fact == previous_targeted_fact:
        state.consecutive_probes += 1
        state.guard_current = max(0, state.guard_current - 1)
    else:
        state.consecutive_probes = 0
        state.guard_current = min(initial_guard, state.guard_current + 1)

    if pressure_response == "inverse" and pressure >= 4:
        state.guard_current = min(5, state.guard_current + 1)

    return state


def decide_action(
    *,
    fact: Fact | None,
    state: FactState | None,
    persona: Persona,
    pressure: int,
) -> GuestAction:
    """Resolve the core PRESSURE-versus-GUARD table without model guesswork."""

    if fact is None or state is None:
        return "deflect"

    # Information that is already out may be repeated without guarding it again.
    if state.revealed == "full":
        return "reveal"

    # High pressure makes an inverse persona close down regardless of the table.
    if persona.pressure_response == "inverse" and pressure >= 4:
        return "deflect"

    if pressure > state.guard_current:
        return "reveal"
    if pressure == state.guard_current:
        return "partial" if fact.partial is not None else "reveal"
    if state.revealed == "partial":
        return "partial"
    if pressure == state.guard_current - 1:
        return "tell"
    return "deflect"


def record_revelation(state: FactState | None, action: GuestAction) -> None:
    if state is None:
        return
    if action == "reveal":
        state.revealed = "full"
    elif action == "partial" and state.revealed == "hidden":
        state.revealed = "partial"


def guard_comparison(pressure: int, state: FactState | None) -> str:
    if state is None:
        return "PRESSURE has no targeted GUARD"
    operator = (
        ">"
        if pressure > state.guard_current
        else "="
        if pressure == state.guard_current
        else "<"
    )
    return f"PRESSURE {pressure} {operator} GUARD {state.guard_current}"


def _find_state(fact_states: Sequence[FactState], fact_id: str) -> FactState:
    for state in fact_states:
        if state.fact_id == fact_id:
            return state
    raise KeyError(f"Missing FactState for targeted fact: {fact_id}")
