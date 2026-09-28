"""The deterministic long-running scenario harness.

The harness drives the real pipeline with a scripted provider, so everything it
measures is the engine's behaviour rather than a model's. It runs the same
scenario at 10, 25, 50, and 100 turns, checks the scenario's invariants after
every turn, and writes a machine-readable report.

Determinism is a hard requirement, not a nicety. There is no randomness anywhere
in this module, token estimation is pinned to the heuristic estimator, and the
scripted provider replays a scenario's turn plan in index order. Counters,
averages, and invariant verdicts are identical between runs; only generated
identifiers and wall-clock timings differ.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.app import repository
from services.context.knowledge import (
    build_knowledge_view,
    claim_states_fact,
    knowledge_leaks,
)
from services.core.enums import CommitmentStatus, EventSource, MemoryClass, MemoryScope
from services.core.events import CHARACTER_DIED, WORLD_FACT_CREATED
from services.core.models import (
    Base,
    Generation,
    Lorebook,
    LorebookEntry,
    Source,
    StoryCommitment,
)
from services.evaluation.metrics import EvaluationReport, ScenarioMetrics, TurnSample
from services.evaluation.scenarios import (
    ALL_SCENARIOS,
    BRANCH_CORRECTNESS,
    COMMITMENT_PERSISTENCE,
    CONTEXT_BOUNDED,
    LORE_ACTIVATION,
    MEMORY_RETRIEVAL,
    NO_KNOWLEDGE_LEAK,
    NO_TRANSCRIPT_AS_MEMORY,
    STATE_CONSISTENT,
    SUSPICION_NOT_CERTAINTY,
    Scenario,
    TurnPlan,
    scenario_by_key,
)
from services.narrative.pipeline import NarrativePipeline
from services.providers.base import ProviderMessage

# The harness pins the fallback estimator so a report does not change shape
# because a tokenizer library happens to be installed on the machine.
HARNESS_CAPABILITIES: dict[str, Any] = {
    "model": "harness-scripted",
    "adapter": "scripted",
    "context_window": 8_192,
    "max_output_tokens": 1_024,
    "features": ["structured_output", "json_mode"],
}

# Turn indices at which a scenario is probed for long-distance recall.
LONG_DISTANCE_PROBES: tuple[int, ...] = (57,)


class ScriptedTurnProvider:
    """Replays a scenario's turn plan.

    The provider is the *only* nondeterministic part of a real system, so the
    harness removes it from the measurement entirely. It still receives the
    prompt, because the harness records what the prompt contained and the
    knowledge-isolation check reads it.
    """

    def __init__(self, plans: Sequence[TurnPlan], character_ids: dict[str, str]) -> None:
        self.plans = list(plans)
        self.character_ids = character_ids
        self.prompts: list[str] = []
        self.index = 0

    def _next(self) -> TurnPlan:
        if self.index >= len(self.plans):
            raise IndexError("Scripted provider exhausted")
        plan = self.plans[self.index]
        self.index += 1
        return plan

    async def structured(
        self,
        messages: list[ProviderMessage],
        schema: dict[str, Any],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        self.prompts.append(messages[-1].content)
        plan = self._next()
        return {
            "selected_actor": self.character_ids.get(plan.actor, ""),
            "reason": f"scripted turn {self.index}",
            "prose": plan.prose,
            "actions": [],
            "new_events": [dict(event) for event in plan.events],
            "state_changes": [],
            "knowledge_changes": [dict(change) for change in plan.knowledge_changes],
            "relationship_changes": [dict(change) for change in plan.relationship_changes],
            "open_commitments": [],
        }

    async def generate(
        self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int | None = None
    ) -> str:
        return json.dumps(await self.structured(messages, {}))

    def stream(  # pragma: no cover - unused
        self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int | None = None
    ):
        raise NotImplementedError

    async def close(self) -> None:
        return None


class StagingProvider:
    """A fixed staging response, so staging never consumes a turn plan."""

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario

    async def structured(
        self,
        messages: list[ProviderMessage],
        schema: dict[str, Any],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        return {
            "location": self.scenario.location,
            "time": "night" if self.scenario.key != "canon" else "morning",
            "characters_present": [spec.name for spec in self.scenario.characters],
            "objective": self.scenario.objective,
            "initial_conditions": [self.scenario.premise],
            "environmental_assumptions": ["The scene is staged from the declared scenario."],
            "potential_consequences": ["A character may learn something irreversible."],
            "canon_conflicts": [],
        }

    async def generate(  # pragma: no cover
        self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int | None = None
    ) -> str:
        return ""

    def stream(  # pragma: no cover
        self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int | None = None
    ):
        raise NotImplementedError


def build_engine() -> Engine:
    database = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(database, "connect")
    def _foreign_keys(dbapi_connection, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(database)
    return database


def _seed_world(db: Session, scenario: Scenario) -> dict[str, str]:
    project = repository.create_project(db, f"Evaluation: {scenario.title}")
    for source in scenario.sources:
        db.add(
            Source(
                project_id=project.id,
                kind="text",
                title=source.get("title", ""),
                content=source.get("content", ""),
            )
        )
    db.flush()
    character_ids: dict[str, str] = {}
    for spec in scenario.characters:
        character = repository.add_character(
            db, project_id=project.id, name=spec.name, definition=dict(spec.definition)
        )
        character_ids[spec.key] = character.id
    if scenario.lore:
        book = Lorebook(
            project_id=project.id,
            name=f"{scenario.title} world book",
            extra_data={"token_budget": 2048},
        )
        db.add(book)
        db.flush()
        for order, entry in enumerate(scenario.lore):
            db.add(
                LorebookEntry(
                    lorebook_id=book.id,
                    name=entry["name"],
                    content=entry["content"],
                    primary_keys=list(entry.get("keys", [])),
                    insertion_order=order,
                )
            )
    if project.active_timeline_id is None:
        raise RuntimeError("A new project must have an active timeline")
    scene = repository.create_scene(
        db,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title=scenario.title,
        participant_ids=list(character_ids.values()),
    )
    db.commit()
    character_ids["__project__"] = project.id
    character_ids["__scene__"] = scene.id
    character_ids["__timeline__"] = project.active_timeline_id or ""
    return character_ids


async def _run_scenario(
    db: Session, scenario: Scenario, turns: int
) -> ScenarioMetrics:
    metrics = ScenarioMetrics(scenario=scenario.key, title=scenario.title, turns=turns)
    for invariant in scenario.invariants:
        metrics.invariants[invariant.key] = True

    character_ids = _seed_world(db, scenario)
    project_id = character_ids["__project__"]
    scene_id = character_ids["__scene__"]
    timeline_id = character_ids["__timeline__"]
    stager = NarrativePipeline(db, StagingProvider(scenario), capabilities=HARNESS_CAPABILITIES)
    staged = await stager.stage(
        project_id=project_id,
        premise=scenario.premise,
        scene_id=scene_id,
        character_ids=[character_ids[spec.key] for spec in scenario.characters],
    )
    stager.approve_scene(
        project_id=project_id,
        scene_id=scene_id,
        staging_revision=(staged.structured_output or {}).get("staging_revision") or 0,
    )
    for text in scenario.commitments:
        await stager.direct(project_id=project_id, scene_id=scene_id, text=text)
    commitment_ids = [row["id"] for row in stager.all_commitments(project_id, timeline_id)]

    plans = [scenario.plan(index, character_ids) for index in range(turns)]
    provider = ScriptedTurnProvider(plans, character_ids)
    pipeline = NarrativePipeline(db, provider, capabilities=HARNESS_CAPABILITIES)
    memory_rows_at_start = _memory_count(db, project_id, timeline_id)

    for index in range(turns):
        plan = plans[index]
        prompt_before = len(provider.prompts)
        result = None
        failure = ""
        try:
            result = await pipeline.continue_scene(
                project_id=project_id,
                scene_id=scene_id,
                # The plan already carries a resolved character id; the override
                # is the scripted direction, not a lookup.
                actor_character_id=plan.actor or None,
                user_input=plan.user_input,
            )
        except Exception as exc:  # a rejected or failed turn is data, not a crash
            failure = str(exc)
        _score_turn(
            db,
            metrics,
            scenario,
            turn=index,
            result=result,
            failure=failure,
            prompt=provider.prompts[prompt_before] if len(provider.prompts) > prompt_before else "",
            user_input=plan.user_input,
        )

    metrics.memory_rows_written = _memory_count(db, project_id, timeline_id) - memory_rows_at_start
    metrics.transcript_turns = turns

    _check_commitment_persistence(db, metrics, project_id, commitment_ids, turns)
    _check_long_distance_memory(db, metrics, scenario, project_id, timeline_id, character_ids, turns)
    _check_suspicions(db, metrics, scenario, timeline_id, character_ids, turns)
    _check_memory_growth(metrics)
    _check_lore(metrics, scenario)

    if scenario.key == "canon":
        _check_branch_isolation(db, metrics, project_id, timeline_id)

    for name, value in metrics.invariants.items():
        if not value:
            metrics.invariant_failures.append(name)
    return metrics


def _memory_count(db: Session, project_id: str, timeline_id: str) -> int:
    rows = repository.list_memories(db, project_id=project_id, timeline_id=timeline_id, limit=5000)
    return len(rows)


def _score_turn(
    db: Session,
    metrics: ScenarioMetrics,
    scenario: Scenario,
    *,
    turn: int,
    result: Any,
    failure: str,
    prompt: str,
    user_input: str,
) -> None:
    context = (result.context_debug or {}) if result is not None else {}
    trace = pipeline_trace(db, result.generation_id) if (result and result.generation_id) else {}
    validation = (result.validation or {}) if result is not None else {}
    contradictions = (result.contradictions or []) if result is not None else []
    committed = len((result.structured_output or {}).get("committed_event_ids", [])) if result is not None else 0

    total_tokens = int(context.get("total_tokens", 0))
    max_input = int(context.get("max_input_tokens", 0))
    metrics.context_tokens.append(total_tokens)
    if max_input and total_tokens > max_input:
        metrics.context_overflows += 1
        metrics.invariants[CONTEXT_BOUNDED] = False
        metrics.findings.append(f"turn {turn}: prompt exceeded the input budget")

    metrics.invalid_events += int(validation.get("rejected_event_count", 0)) + len(validation.get("errors", []))
    if failure:
        metrics.rejected_generations += 1
    metrics.contradiction_warnings += len(contradictions)
    for warning in contradictions:
        if warning.get("severity") == "error":
            metrics.state_contradictions += 1
            metrics.invariants[STATE_CONSISTENT] = False
            metrics.findings.append(
                f"turn {turn}: {warning.get('code')}: {warning.get('summary')}"
            )

    retrieval = trace.get("retrieval", {})
    lore = trace.get("activated_lore", [])
    missed = (trace.get("lore_debug") or {}).get("missed", [])
    metrics.lore_activated_total += len(lore)
    metrics.lore_missed_activations += len(missed)

    if prompt:
        _score_knowledge_isolation(db, metrics, turn, prompt, result, user_input)

    performance = trace.get("performance", {})
    for name, value in (performance.get("by_stage") or {}).items():
        metrics.stage_timings_ms[name] = metrics.stage_timings_ms.get(name, 0.0) + float(value)

    metrics.samples.append(
        TurnSample(
            turn=turn,
            actor_character_id=(result.structured_output or {}).get("selected_actor") if result else None,
            total_tokens=total_tokens,
            max_input_tokens=max_input,
            memories_retrieved=len(trace.get("retrieved_memories", [])),
            memories_considered=int(retrieval.get("considered", 0)),
            lore_activated=len(lore),
            lore_candidates=int((trace.get("lore_debug") or {}).get("considered_count", 0)),
            lore_missed=len(missed),
            events_committed=committed,
            validation_errors=len(validation.get("errors", [])),
            contradictions=len(contradictions),
            status="committed" if committed else ("rejected" if failure else "empty"),
            duration_ms=float(performance.get("total_ms", 0.0)),
        )
    )


def _score_knowledge_isolation(
    db: Session,
    metrics: ScenarioMetrics,
    turn: int,
    prompt: str,
    result: Any,
    user_input: str,
) -> None:
    """Does the prompt contain a fact the acting character has not learned?

    Reads the state *after* the turn, which is the strictest possible reading: a
    leak counts even if the character learned the fact during the turn and the
    prompt was built before they did.

    A fact that the turn's own instruction states is not counted. The operator
    is a legitimate channel: when the user tells the engine that Alice has a
    brother, the prompt must contain those words, and their presence is user
    intervention rather than an engine leak.
    """
    if result is None:
        return
    state = repository.current_state(db, result.timeline_id or "")
    actor_id = (result.structured_output or {}).get("selected_actor")
    view = build_knowledge_view(state, viewer_character_id=actor_id)
    leaks = [
        fact
        for fact in knowledge_leaks(view, [prompt])
        if not claim_states_fact(user_input, fact.fact)
    ]
    if leaks:
        metrics.knowledge_leaks += len(leaks)
        metrics.invariants[NO_KNOWLEDGE_LEAK] = False
        metrics.findings.append(
            f"turn {turn}: {len(leaks)} withheld fact(s) reached the prompt for actor {actor_id}"
        )
    if actor_id in state.dead and not _died_this_turn(db, result):
        metrics.invariants[STATE_CONSISTENT] = False
        metrics.findings.append(f"turn {turn}: dead character {actor_id} was selected to act")


def _died_this_turn(db: Session, result: Any) -> bool:
    """Whether this turn's own committed events killed the selected actor.

    A turn that strikes a character down is a death, not a violation. The actor
    was alive when they were selected.
    """
    if result is None:
        return False
    actor_id = (result.structured_output or {}).get("selected_actor")
    committed = (result.structured_output or {}).get("committed_event_ids", [])
    if not actor_id or not committed:
        return False
    rows = repository.list_events(db, result.timeline_id or "")
    by_id = {event.id: event for event in rows}
    return any(
        by_id[event_id].event_type == CHARACTER_DIED
        and (by_id[event_id].payload or {}).get("character_id") == actor_id
        for event_id in committed
        if event_id in by_id
    )


def _check_commitment_persistence(
    db: Session,
    metrics: ScenarioMetrics,
    project_id: str,
    commitment_ids: list[str],
    turns: int,
) -> None:
    """A commitment must still be open after every unrelated turn.

    The check reads the rows, not the planner context, because the planner
    context is what the commitment is supposed to influence: if it were only
    visible because the context included it, the check would be circular.
    """
    if not commitment_ids or COMMITMENT_PERSISTENCE not in metrics.invariants:
        return
    rows = db.scalars(select(StoryCommitment).where(StoryCommitment.id.in_(commitment_ids))).all()
    open_count = sum(1 for row in rows if CommitmentStatus.coerce(row.status).is_open)
    if open_count != len(commitment_ids):
        metrics.broken_commitments += len(commitment_ids) - open_count
        metrics.invariants[COMMITMENT_PERSISTENCE] = False
        metrics.findings.append(
            f"after {turns} turns, {len(commitment_ids) - open_count} of {len(commitment_ids)} "
            "commitments were no longer open"
        )


def _check_long_distance_memory(
    db: Session,
    metrics: ScenarioMetrics,
    scenario: Scenario,
    project_id: str,
    timeline_id: str,
    character_ids: dict[str, str],
    turns: int,
) -> None:
    """A fact learned many turns ago must still be retrievable.

    Only meaningful for a scenario long enough to contain a probe, so short runs
    report the invariant as satisfied rather than failing on an absent turn.
    """
    if MEMORY_RETRIEVAL not in metrics.invariants:
        return
    probes = [index for index in LONG_DISTANCE_PROBES if index < turns]
    if not probes:
        metrics.findings.append(
            f"{turns} turns is too short to probe long-distance recall; invariant treated as untested"
        )
        return
    actor_id = character_ids.get("detective") or character_ids.get("captain") or character_ids.get("general")
    for index in probes:
        plan = scenario.plan(index, character_ids)
        state = repository.current_state(db, timeline_id)
        view = build_knowledge_view(state, viewer_character_id=actor_id)
        inspection = NarrativePipeline(db, HeuristicHarnessProvider(), capabilities=HARNESS_CAPABILITIES).memory_inspection(
            project_id=project_id, timeline_id=timeline_id, query=plan.user_input, character_id=actor_id, limit=20
        )
        recalled = " ".join(str(item["content"]) for item in inspection["selected"]).casefold()
        known = [fact for fact in view.certain_texts()]
        missing = [fact for fact in known if fact.casefold() not in recalled]
        if missing:
            metrics.memory_retrieval_failures += len(missing)
            metrics.invariants[MEMORY_RETRIEVAL] = False
            metrics.findings.append(
                f"turn {index}: {len(missing)} known fact(s) were not surfaced by retrieval"
            )


def _check_suspicions(
    db: Session,
    metrics: ScenarioMetrics,
    scenario: Scenario,
    timeline_id: str,
    character_ids: dict[str, str],
    turns: int,
) -> None:
    """A suspicion must stay a suspicion: present in ``suspicions``, absent from ``knowledge``.

    Only runs when the scenario actually reaches the probe turn, so short runs
    report the invariant as untested rather than failing on an absent setup.
    """
    if SUSPICION_NOT_CERTAINTY not in metrics.invariants:
        return
    probes = [probe for probe in scenario.suspicion_probes if probe.from_turn < turns]
    if not probes:
        metrics.findings.append(
            f"{turns} turns is too short to reach a suspicion probe; invariant treated as untested"
        )
        return
    state = repository.current_state(db, timeline_id)
    for probe in probes:
        character_id = character_ids.get(probe.character_key)
        if probe.fact not in state.suspicions.get(character_id or "", set()):
            metrics.invariants[SUSPICION_NOT_CERTAINTY] = False
            metrics.findings.append(
                f"suspicion probe: {probe.character_key} does not suspect '{probe.fact}'"
            )
        if probe.fact in state.knowledge.get(character_id or "", set()):
            metrics.invariants[SUSPICION_NOT_CERTAINTY] = False
            metrics.findings.append(
                f"suspicion probe: {probe.character_key} is certain about '{probe.fact}', "
                "which was only ever a suspicion"
            )
        overlap = state.suspicions.get(character_id or "", set()) & state.knowledge.get(
            character_id or "", set()
        )
        if overlap:
            metrics.invariants[SUSPICION_NOT_CERTAINTY] = False
            metrics.findings.append(
                f"suspicion probe: {probe.character_key} holds the same belief as both suspicion and certainty"
            )


def _check_memory_growth(metrics: ScenarioMetrics) -> None:
    if NO_TRANSCRIPT_AS_MEMORY not in metrics.invariants:
        return
    if metrics.memory_growth_ratio > 1.5:
        metrics.invariants[NO_TRANSCRIPT_AS_MEMORY] = False
        metrics.findings.append(
            f"memory grew to {metrics.memory_growth_ratio} rows per turn, which is transcript-shaped"
        )


def _check_lore(metrics: ScenarioMetrics, scenario: Scenario) -> None:
    if LORE_ACTIVATION not in metrics.invariants:
        return
    expected_names = {entry["name"] for entry in scenario.lore}
    for sample in metrics.samples:
        if sample.lore_activated > 12:
            metrics.lore_false_activations += sample.lore_activated - 12
            metrics.invariants[LORE_ACTIVATION] = False
            metrics.findings.append(
                f"turn {sample.turn}: {sample.lore_activated} lore entries activated at once"
            )
    if not expected_names:
        return


def _check_branch_isolation(
    db: Session,
    metrics: ScenarioMetrics,
    project_id: str,
    timeline_id: str,
) -> None:
    """Fork, diverge, and prove neither branch sees the other's material.

    Three separate leak routes are checked because they fail differently: world
    facts in the projection, memory rows, and commitment scope.
    """
    if BRANCH_CORRECTNESS not in metrics.invariants:
        return
    node = repository.latest_node(db, timeline_id)
    parent_before = set(repository.current_state(db, timeline_id).world_facts)
    branch = repository.fork_timeline(
        db, source_timeline_id=timeline_id, source_node_id=node.id, name="Alternate: harbour stays sealed"
    )
    db.commit()
    repository.append_event(
        db,
        project_id=project_id,
        timeline_id=branch.id,
        event_type=WORLD_FACT_CREATED,
        payload={"fact_id": "harbour-still-sealed", "text": "the harbour gate remains sealed in this branch"},
        source=EventSource.USER,
    )
    db.commit()
    parent_after = set(repository.current_state(db, timeline_id).world_facts)
    branch_facts = set(repository.current_state(db, branch.id).world_facts)
    if parent_after != parent_before:
        leaked = parent_after - parent_before
        metrics.branch_leaks += len(leaked) or 1
        metrics.invariants[BRANCH_CORRECTNESS] = False
        metrics.findings.append(
            f"branch work changed the parent timeline's world facts: added {sorted(leaked) or '?'}"
        )
    if "harbour-still-sealed" in parent_after:
        metrics.branch_leaks += 1
        metrics.invariants[BRANCH_CORRECTNESS] = False
        metrics.findings.append("a world fact created on the branch is visible on the parent timeline")
    missing = parent_before - branch_facts
    if missing:
        metrics.branch_leaks += len(missing)
        metrics.invariants[BRANCH_CORRECTNESS] = False
        metrics.findings.append(f"the branch lost {len(missing)} world fact(s) it inherited from its parent")
    if "harbour-still-sealed" not in branch_facts:
        metrics.branch_leaks += 1
        metrics.invariants[BRANCH_CORRECTNESS] = False
        metrics.findings.append("a world fact created on the branch is missing there")

    repository.add_memory(
        db,
        project_id=project_id,
        content="branch-only: the harbour gate was never opened here",
        memory_class=MemoryClass.PERMANENT,
        scope=MemoryScope.WORLD,
        timeline_id=branch.id,
        importance=0.95,
    )
    db.commit()
    inspector = NarrativePipeline(db, HeuristicHarnessProvider(), capabilities=HARNESS_CAPABILITIES)
    parent_view = inspector.memory_inspection(project_id=project_id, timeline_id=timeline_id, limit=200)
    branch_view = inspector.memory_inspection(project_id=project_id, timeline_id=branch.id, limit=200)
    parent_surfaced = " ".join(str(item["content"]) for item in parent_view["selected"]).casefold()
    branch_surfaced = " ".join(str(item["content"]) for item in branch_view["selected"]).casefold()
    if "branch-only" in parent_surfaced:
        metrics.branch_leaks += 1
        metrics.invariants[BRANCH_CORRECTNESS] = False
        metrics.findings.append("a branch-only memory surfaced on the parent timeline")
    if "branch-only" not in branch_surfaced:
        metrics.branch_leaks += 1
        metrics.invariants[BRANCH_CORRECTNESS] = False
        metrics.findings.append("a branch-only memory was not retrievable on its own branch")
    # An inherited memory and its branch copy must collapse to one entry.
    if branch_view["retrieval"]["dropped_deduplicated"] == 0 and any(
        str(item.get("inherited_from_id") or "") for item in branch_view["selected"]
    ):
        metrics.findings.append("inherited memories were not de-duplicated on the branch")


class HeuristicHarnessProvider:
    """A provider that is never called, only needed to construct the pipeline."""

    model = "harness-inspector"

    async def structured(
        self,
        messages: list[ProviderMessage],
        schema: dict[str, Any],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        raise RuntimeError("The inspection pipeline must never call a provider")

    async def generate(
        self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int | None = None
    ) -> str:
        raise RuntimeError("The inspection pipeline must never call a provider")

    def stream(  # pragma: no cover
        self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int | None = None
    ):
        raise RuntimeError("The inspection pipeline must never stream")


def pipeline_trace(db: Session, generation_id: str) -> dict[str, Any]:

    row = db.get(Generation, generation_id)
    if row is None:
        return {}
    return dict(row.trace or {})


def run(
    *,
    scenarios: Sequence[str] | None = None,
    turn_counts: Sequence[int] = (10, 25, 50, 100),
) -> EvaluationReport:
    selected = [scenario_by_key(key) for key in (scenarios or [])] or list(ALL_SCENARIOS)
    report = EvaluationReport(
        turns_requested=list(turn_counts),
        generated_at=datetime.now(UTC).isoformat(),
        estimator="heuristic-v1",
    )
    engine = build_engine()
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as db:
            for scenario in selected:
                for turns in turn_counts:
                    report.scenarios.append(asyncio.run(_run_scenario(db, scenario, turns)))
    finally:
        engine.dispose()
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic narrative evaluation.")
    parser.add_argument("--scenario", action="append", dest="scenarios", default=None)
    parser.add_argument("--turns", action="append", type=int, dest="turns", default=None)
    parser.add_argument("--out", default="reports/evaluation.json")
    parser.add_argument("--print", action="store_true", dest="show")
    args = parser.parse_args(argv)
    report = run(
        scenarios=args.scenarios,
        turn_counts=tuple(args.turns) if args.turns else (10, 25, 50, 100),
    )
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.show:
        print(report.render())
    print(f"wrote {target}")
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
