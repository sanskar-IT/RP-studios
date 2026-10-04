"""The Performer: it decides how a scene plays, and nothing else.

The Director decides what the scene is trying to accomplish. The Performer
decides how it plays out. The State Engine decides what actually became true.
Those are three separate jobs and this module is only the second one.

**The Performer never commits state.** It returns proposals. Every event it
suggests travels through the existing claim extraction and validation pipeline,
and the State Engine disposes. That boundary is the reason a Performer bug is a
bad scene rather than corrupted history.

Three properties are load-bearing and each exists because its absence was a bug:

**Performer scope is the scene, never the project.** Only characters who are
participants in this scene, and only for this scene, enter the request. A
character who exists in the project but is not in the room is not context — and
passing them anyway would mean the model can be asked about someone who cannot
act or be observed.

**Knowledge is per-viewer, per-actor.** Each actor's brief carries what *that
actor* may know. A non-viewer actor gets a redacted, observably-derived brief
instead of their interiority. This is the same protection the Director's briefs
carry, applied per actor rather than per turn, because a multi-actor turn has as
many viewers as it has speakers.

**A user-controlled actor's decisions belong to the user.** The Performer may
describe the world reacting, other characters reacting, and consequences, but it
may not invent a major decision for a possessed character. User agency is
enforced here, in the request and the post-check, rather than downstream in event
rewriting — which is where the previous implementation put it, and where it
silently mis-attributed other characters' dialogue to the possessed character.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from services.core.enums import BeatStatus, ControlMode
from services.core.events import MODEL_WRITABLE_EVENT_TYPES, canonical_event_type
from services.core.state import StateSnapshot
from services.director.plan import Beat, DirectorPlanDraft

# How the scene is presented. Deliberately small: this is a knob on tone, not a
# style engine, and anything more elaborate would be a second Director.
PRESENTATION_STYLES = ("hybrid", "literary", "dialogue_focused")

# Presentation guidance appended to the prompt. Each is a short bias, not a
# template; the Performer still decides whether to use it.
_STYLE_GUIDANCE: dict[str, tuple[str, ...]] = {
    "hybrid": (
        "Weave environment, action, dialogue, and reaction into one continuous passage.",
    ),
    "literary": (
        "Favour sensory detail and interiority over plot mechanics.",
        "Let silence and observation carry weight; do not summarise what can be shown.",
    ),
    "dialogue_focused": (
        "Keep dialogue in the foreground and cut description to what a listener would notice.",
        "Let subtext carry the tension rather than narration of feelings.",
    ),
}


def normalize_style(value: str | None) -> str:
    """Coerce a presentation request to a supported style, defaulting to hybrid.

    Unknown input falls back rather than raising: presentation is a preference,
    and a typo in a UI field should not fail a turn.
    """
    candidate = (value or "").strip().casefold().replace("-", "_")
    return candidate if candidate in PRESENTATION_STYLES else "hybrid"


# --- The request ------------------------------------------------------------


@dataclass
class ActorBrief:
    """What one actor may be given for this turn.

    ``knows`` and ``suspects`` are populated **only** when the request's viewer is
    this actor. Every other actor gets an observably-derived brief instead, which
    is a weaker but honest read: what they did, what they carry, what hurts.
    """

    character_id: str
    name: str
    is_viewer: bool = False
    user_controlled: bool = False
    alive: bool = True
    present: bool = True
    location_id: str = ""
    goal: str = ""
    knows: list[str] = field(default_factory=list)
    suspects: list[str] = field(default_factory=list)
    injuries: list[str] = field(default_factory=list)
    carries: list[str] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    last_action: str = ""
    definition: dict[str, Any] = field(default_factory=dict)
    # Why the Performer should not decide this actor's major choices this turn.
    agency_withheld: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "character_id": self.character_id,
            "name": self.name,
            "is_viewer": self.is_viewer,
            "user_controlled": self.user_controlled,
            "alive": self.alive,
            "present": self.present,
            "location_id": self.location_id,
            "goal": self.goal,
            "injuries": list(self.injuries),
            "carries": list(self.carries),
            "relationships": list(self.relationships),
            "last_action": self.last_action,
            "agency_withheld": self.agency_withheld,
        }
        if self.is_viewer:
            payload["knows"] = list(self.knows)
            payload["suspects"] = list(self.suspects)
        else:
            payload["knows"] = "private to them"
            payload["suspects"] = "private to them"
        if self.definition:
            payload["definition"] = dict(self.definition)
        return payload


@dataclass
class PerformerRequest:
    """Everything the Performer needs to realise the current beat, and no more.

    The shape is the enforcement mechanism, not documentation. A field that does
    not exist here cannot leak into a prompt, which is how the Performer stays
    honest about scope without relying on instructions it might ignore.
    """

    scene_id: str
    scene_title: str = ""
    scene_objective: str = ""
    location: str = ""
    time_of_day: str = ""
    # The single beat to realise. A plan spans turns; one turn realises one beat.
    beat: Beat | None = None
    beat_index: int = 0
    beat_total: int = 0
    plan_objective: str = ""
    desired_direction: str = ""
    required_consequences: list[str] = field(default_factory=list)
    # The plan's own demands, kept separate because they are enforced across the
    # plan's whole lifetime rather than by any single turn.
    plan_required_consequences: list[str] = field(default_factory=list)
    likely_consequences: list[str] = field(default_factory=list)
    optional_consequences: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    actors: list[ActorBrief] = field(default_factory=list)
    # The acting character for this turn, chosen by the shared selector.
    viewer_character_id: str | None = None
    actor_selection_reason: str = ""
    user_input: str = ""
    style: str = "hybrid"
    environment: dict[str, Any] = field(default_factory=dict)
    initial_conditions: list[str] = field(default_factory=list)
    recent_events: list[str] = field(default_factory=list)
    active_commitments: list[dict[str, Any]] = field(default_factory=list)
    # Narration is legitimate when nobody is acting — a passing of time, weather,
    # a room changing while the cast is off doing something.
    narration_allowed: bool = True

    def actor(self, character_id: str) -> ActorBrief | None:
        return next((a for a in self.actors if a.character_id == character_id), None)

    @property
    def user_controlled_ids(self) -> list[str]:
        return [a.character_id for a in self.actors if a.user_controlled]

    @property
    def acting_ids(self) -> list[str]:
        """Actors the Performer may write as acting freely this turn.

        A user-controlled actor is *absent* here, because the Performer does not
        decide their intentions. It is not absent from the scene: the Performer may
        still describe the hand, the silence, and what the character observes.
        Those are connective, and a check that forbade them would make possession
        produce a scene where the user's character does nothing at all.
        """
        return [
            a.character_id
            for a in self.actors
            if a.alive and a.present and not a.agency_withheld
        ]

    @property
    def present_ids(self) -> list[str]:
        """Actors who are in the room and able to be written at all."""
        return [a.character_id for a in self.actors if a.alive and a.present]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "scene_title": self.scene_title,
            "scene_objective": self.scene_objective,
            "location": self.location,
            "time_of_day": self.time_of_day,
            "beat": self.beat.to_dict() if self.beat else None,
            "beat_index": self.beat_index,
            "beat_total": self.beat_total,
            "plan_objective": self.plan_objective,
            "desired_direction": self.desired_direction,
            "required_consequences": list(self.required_consequences),
            "plan_required_consequences": list(self.plan_required_consequences),
            "likely_consequences": list(self.likely_consequences),
            "optional_consequences": list(self.optional_consequences),
            "constraints": list(self.constraints),
            "exclusions": list(self.exclusions),
            "actors": [actor.to_dict() for actor in self.actors],
            "viewer_character_id": self.viewer_character_id,
            "actor_selection_reason": self.actor_selection_reason,
            "user_input": self.user_input,
            "style": self.style,
            "environment": dict(self.environment),
            "initial_conditions": list(self.initial_conditions),
            "recent_events": list(self.recent_events),
            "active_commitments": list(self.active_commitments),
            "narration_allowed": self.narration_allowed,
        }


# --- The result -------------------------------------------------------------


@dataclass
class ActorTurn:
    """One actor's contribution to the turn."""

    character_id: str
    name: str = ""
    action: str = ""
    dialogue: str = ""
    # ``reaction``, ``decision``, ``observation``. Recorded so the trace can
    # distinguish a character reacting from a character deciding something.
    kind: str = "reaction"

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "name": self.name,
            "action": self.action,
            "dialogue": self.dialogue,
            "kind": self.kind,
        }


@dataclass
class PerformerResult:
    """The Performer's proposal. Presentation plus claims; never state.

    ``prose`` is what the user reads. Everything else is a *claim*: a statement
    about what became true, which the existing validation pipeline judges and the
    State Engine accepts or refuses.
    """

    prose: str = ""
    dialogue: list[ActorTurn] = field(default_factory=list)
    actor_actions: list[ActorTurn] = field(default_factory=list)
    proposed_events: list[dict[str, Any]] = field(default_factory=list)
    state_claims: list[dict[str, Any]] = field(default_factory=list)
    knowledge_changes: list[dict[str, Any]] = field(default_factory=list)
    completion_signals: dict[str, Any] = field(default_factory=dict)
    narration: str = ""
    # Which required consequences the Performer believes it realised, and which it
    # did not. A required consequence is a demand, so failing to meet one has to
    # be visible rather than silently dropped.
    required_satisfied: list[str] = field(default_factory=list)
    required_unmet: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prose": self.prose,
            "dialogue": [turn.to_dict() for turn in self.dialogue],
            "actor_actions": [turn.to_dict() for turn in self.actor_actions],
            "proposed_events": list(self.proposed_events),
            "state_claims": list(self.state_claims),
            "knowledge_changes": list(self.knowledge_changes),
            "completion_signals": dict(self.completion_signals),
            "narration": self.narration,
            "required_satisfied": list(self.required_satisfied),
            "required_unmet": list(self.required_unmet),
            "notes": list(self.notes),
        }

    def all_claims(self) -> list[dict[str, Any]]:
        """Every claim, in the order the validation pipeline should see them."""
        return [
            *self.proposed_events,
            *self.state_claims,
            *self.knowledge_changes,
        ]

    def to_generation_shape(
        self, *, actor_id: str = "", participant_ids: set[str] | None = None
    ) -> dict[str, Any]:
        """Fold this result into the canonical generation shape.

        The pipeline's extraction and validation are the authority on what becomes
        state, and they read a fixed shape. Rather than fork that path for the
        Performer's richer output, the result is rendered into the same shape and
        then validated by exactly the same code that has always validated it.

        ``proposed_events`` become ``new_events``; dialogue and actor actions
        become speech and action claims attributed to the character who owns them,
        so a witness's line is a ``character_spoke`` claim for the witness rather
        than an untyped blob.

        Nothing is filtered here. A claim naming someone who is not in the scene
        is passed to validation to be *rejected loudly*, because a claim silently
        deleted by the Performer looks identical to a claim the model never made
        — and the user would never learn the model tried to write a stranger.
        ``participant_ids`` is accepted so the signature matches the extraction
        call site and so the refusal is a deliberate, documented decision.
        """
        del participant_ids
        new_events: list[dict[str, Any]] = [dict(event) for event in self.proposed_events]

        for turn in self.dialogue:
            if not turn.dialogue:
                continue
            new_events.append(
                {
                    "event_type": "character_spoke",
                    "character_id": turn.character_id,
                    "text": turn.dialogue,
                }
            )

        for turn in self.actor_actions:
            if not turn.action:
                continue
            new_events.append(
                {
                    "event_type": "character_performed_action",
                    "character_id": turn.character_id,
                    "action": turn.action,
                    "kind": turn.kind,
                }
            )

        return {
            "prose": self.prose,
            "selected_actor": actor_id,
            "reason": "",
            "new_events": new_events,
            "state_changes": [dict(item) for item in self.state_claims],
            "knowledge_changes": [dict(item) for item in self.knowledge_changes],
            "relationship_changes": [],
        }


# --- Building the request ---------------------------------------------------


def build_actor_briefs(
    characters: Sequence[Any],
    state: StateSnapshot,
    *,
    participant_ids: set[str],
    viewer_character_id: str | None,
    control_modes: dict[str, str],
    scene_location: str,
    definition_limit: int = 1200,
) -> list[ActorBrief]:
    """Build one brief per *scene participant*, and for nobody else.

    The filter is the point. A project may hold forty characters; a scene holds
    the three who are in the room, and only those three can act, be observed, or
    hold a relevant goal. Including the rest would invite the model to write
    someone who is not there.

    ``definition_limit`` is applied here rather than at render time so a truncated
    character card is visibly marked instead of silently cut.
    """
    briefs: list[ActorBrief] = []
    for character in characters:
        if character.id not in participant_ids:
            continue
        character_id = character.id
        record = state.characters.get(character_id, {})
        is_viewer = character_id == viewer_character_id
        control = control_modes.get(character_id, ControlMode.AI.value)
        user_controlled = control == ControlMode.USER.value
        certain = sorted(state.knowledge.get(character_id, set())) if is_viewer else []
        suspected = sorted(state.suspicions.get(character_id, set())) if is_viewer else []
        dead = character_id in state.dead
        location = str(record.get("location_id", ""))
        briefs.append(
            ActorBrief(
                character_id=character_id,
                name=character.name,
                is_viewer=is_viewer,
                user_controlled=user_controlled,
                alive=not dead,
                # A participant who is somewhere else can be written about as
                # absent, but cannot act in this beat.
                present=not dead,
                location_id=location,
                goal=(
                    _goal_for(record, certain, suspected, dead)
                    if is_viewer
                    else _observable_goal_for(record, dead)
                ),
                knows=certain,
                suspects=suspected,
                injuries=sorted(state.injuries.get(character_id, set())),
                carries=sorted(state.items.get(character_id, set())),
                relationships=[
                    relationship
                    for relationship in state.relationships.values()
                    if relationship.get("source_character_id") == character_id
                    or relationship.get("target_character_id") == character_id
                ],
                last_action=str(record.get("last_action", ""))[:160],
                definition=_bounded_definition(character.definition, definition_limit),
                # A user-controlled character's major decisions are the user's.
                # The Performer may still describe the world and others reacting.
                agency_withheld=user_controlled,
            )
        )
    return briefs


def _bounded_definition(definition: Any, limit: int) -> dict[str, Any]:
    """Truncate a character card, and say so rather than cutting mid-sentence."""
    if not isinstance(definition, dict) or not definition:
        return {}
    rendered = json.dumps(definition, sort_keys=True, default=str)
    if len(rendered) <= limit:
        return dict(definition)
    kept: dict[str, Any] = {}
    for key, value in definition.items():
        candidate = {**kept, key: value}
        if len(json.dumps(candidate, sort_keys=True, default=str)) > limit:
            break
        kept = candidate
    kept["_truncated"] = True
    return kept


def _observable_goal_for(record: dict[str, Any], dead: bool) -> str:
    """A goal readable from what an observer can see.

    Deliberately weaker than :func:`_goal_for`. Without the character's own
    knowledge, the only defensible read of their intent is what they just did and
    what they visibly carry or suffer.
    """
    if dead:
        return "dead; take no action"
    if record.get("last_action"):
        return f"continue: {str(record['last_action'])[:80]}"
    return "respond to what happens next, from their own perspective"


def _goal_for(
    record: dict[str, Any], certain: list[str], suspected: list[str], dead: bool
) -> str:
    """A short, derived read of what this character is plausibly after.

    Weak by design: it reads the projection, not a stored motive. A character
    with a recent action is pursuing it; one holding something has a reason to
    keep holding it; one who suspects something is chasing it.
    """
    if dead:
        return "dead; take no action"
    if suspected:
        return f"confirm or drop the suspicion: {suspected[0]}"
    if certain:
        return f"act on what is known: {certain[-1]}"
    if record.get("last_action"):
        return f"continue: {str(record['last_action'])[:80]}"
    return "observe and respond to the current situation"


def build_performer_request(
    *,
    scene: Any,
    state: StateSnapshot,
    characters: Sequence[Any],
    participant_ids: set[str],
    control_modes: dict[str, str],
    viewer_character_id: str | None,
    actor_selection_reason: str,
    user_input: str,
    plan: DirectorPlanDraft | None = None,
    style: str = "hybrid",
    recent_events: Sequence[str] = (),
    active_commitments: Sequence[dict[str, Any]] = (),
) -> PerformerRequest:
    """Assemble the request for one beat.

    The plan is optional: a scene can be performed with no Director plan at all,
    which is what possession and direct-actor turns produce. A missing plan is
    not a degraded request, it is a different kind of request.
    """
    staging = dict(scene.staging or {})
    beat = plan.active_beat() if plan is not None else None
    briefs = build_actor_briefs(
        characters,
        state,
        participant_ids=participant_ids,
        viewer_character_id=viewer_character_id,
        control_modes=control_modes,
        scene_location=str(staging.get("location", "")),
    )
    index = 0
    total = 0
    if plan is not None:
        total = len(plan.beats)
        if beat is not None:
            index = plan.beats.index(beat) if beat in plan.beats else 0
    return PerformerRequest(
        scene_id=scene.id,
        scene_title=scene.title,
        scene_objective=str(staging.get("objective", "")),
        location=str(staging.get("location", "")),
        time_of_day=str(staging.get("time", "")),
        beat=beat,
        beat_index=index,
        beat_total=total,
        plan_objective=plan.objective if plan is not None else "",
        desired_direction=plan.desired_direction if plan is not None else "",
        # Beat-scoped only. A plan's own required consequences describe an arc
        # that may take many turns, and demanding the whole arc by the end of one
        # turn would reject correct scenes for not finishing the story at once.
        required_consequences=(
            list(beat.required_consequences) if beat is not None and beat.required_consequences else []
        ),
        plan_required_consequences=(
            list(plan.required_consequences) if plan is not None else []
        ),
        likely_consequences=list(plan.likely_consequences) if plan is not None else [],
        optional_consequences=list(plan.optional_consequences) if plan is not None else [],
        constraints=list(plan.constraints) if plan is not None else [],
        exclusions=list(plan.exclusions) if plan is not None else [],
        actors=briefs,
        viewer_character_id=viewer_character_id,
        actor_selection_reason=actor_selection_reason,
        user_input=user_input,
        style=normalize_style(style),
        environment={
            key: staging.get(key)
            for key in ("location", "time", "environmental_assumptions")
            if staging.get(key)
        },
        initial_conditions=[str(item) for item in staging.get("initial_conditions", []) or []],
        recent_events=[str(item) for item in recent_events],
        active_commitments=[dict(item) for item in active_commitments],
        narration_allowed=not briefs or all(
            brief.agency_withheld for brief in briefs
        ),
    )


# --- Rendering --------------------------------------------------------------


def render_performer_prompt(request: PerformerRequest) -> str:
    """The Performer's prompt.

    Kept deliberately short and imperative. The request already carries the
    structure; this renders it as instructions the model can act on without
    reinterpreting prose written for a human reader.
    """
    lines: list[str] = [
        "PERFORM THIS SCENE SEGMENT.",
        "Everything below is the situation. Realise it; do not report on it.",
    ]

    if request.beat is not None:
        lines.append(
            f"This turn realises beat {request.beat_index + 1} of {request.beat_total}: "
            f"{request.beat.description}"
        )
        lines.append(
            "The Performer realises one beat per turn. Do not rush through the "
            "remaining beats; later turns handle them."
        )
    elif request.plan_objective:
        lines.append(f"Narrative pressure: {request.plan_objective}")

    lines.extend(_STYLE_GUIDANCE.get(request.style, _STYLE_GUIDANCE["hybrid"]))

    lines.append("")
    lines.append("SCENE")
    lines.append(f"  Title: {request.scene_title or 'untitled'}")
    lines.append(f"  Location: {request.location or 'unspecified'}")
    lines.append(f"  Time: {request.time_of_day or 'unspecified'}")
    if request.scene_objective:
        lines.append(f"  Objective: {request.scene_objective}")
    if request.initial_conditions:
        lines.append(f"  Initial conditions: {'; '.join(request.initial_conditions)}")

    if request.actors:
        lines.append("")
        lines.append("CAST — only these characters are present. Do not write anyone else.")
        for brief in request.actors:
            lines.append(f"  {_render_actor(brief)}")

    withheld = [brief for brief in request.actors if brief.agency_withheld]
    if withheld:
        names = ", ".join(brief.name for brief in withheld)
        lines.append("")
        lines.append(
            f"USER-CONTROLLED: {names}. The user owns {names} intentional actions. "
            "You may write the environment, other characters' reactions, and the "
            "consequences. You may supply low-level connective description — a "
            "hand steadies, a breath catches. You may NOT invent a major decision, "
            "a new intention, an accusation, or a conflict of will for them."
        )

    if request.required_consequences:
        lines.append("")
        lines.append("REQUIRED THIS TURN — these must be realised:")
        lines.extend(f"  - {item}" for item in request.required_consequences)
    elif request.plan_required_consequences:
        lines.append("")
        lines.append(
            "This turn serves a longer arc rather than finishing it. Keep it in mind; "
            "you are not required to complete it now:"
        )
        lines.extend(f"  - {item}" for item in request.plan_required_consequences)
    if request.likely_consequences:
        lines.append("LIKELY — realise if they fit the moment:")
        lines.extend(f"  - {item}" for item in request.likely_consequences)
    if request.optional_consequences:
        lines.append(
            "OPTIONAL — these are possibilities, not obligations. Never force one, "
            "and never treat one as a commitment:"
        )
        lines.extend(f"  - {item}" for item in request.optional_consequences)

    if request.constraints:
        lines.append("")
        lines.append("MUST HOLD:")
        lines.extend(f"  - {item}" for item in request.constraints)
    if request.exclusions:
        lines.append("MUST NOT HAPPEN:")
        lines.extend(f"  - {item}" for item in request.exclusions)

    if request.user_input:
        lines.append("")
        lines.append("THE USER JUST SAID:")
        lines.append(f"  {request.user_input}")

    if request.recent_events:
        lines.append("")
        lines.append("RECENTLY:")
        lines.extend(f"  - {item}" for item in request.recent_events)

    lines.append("")
    lines.append("AGENCY")
    lines.append(
        "Each character decides their own reaction from their own knowledge, goals, "
        "and relationships. Do not make them respond identically. A character may "
        "refuse, deflect, or do nothing."
    )
    if request.narration_allowed and not request.actors:
        lines.append("Nobody is acting. Narrate the environment and what changes.")
    lines.append(
        "Return prose and proposed state separately. Assert only what the material "
        "above supports. If it is insufficient, write the uncertainty instead of "
        "resolving it."
    )
    return "\n".join(lines)


def _render_actor(brief: ActorBrief) -> str:
    parts = [f"{brief.name} (id: {brief.character_id})"]
    if not brief.alive:
        parts.append("DEAD on this timeline — take no action, speak no line")
    if brief.goal:
        parts.append(f"goal: {brief.goal}")
    if brief.knows:
        parts.append(f"knows: {', '.join(brief.knows) or 'nothing established'}")
    if brief.suspects:
        parts.append(f"suspects: {', '.join(brief.suspects) or 'nothing'}")
    if brief.injuries:
        parts.append(f"injuries: {', '.join(brief.injuries)}")
    if brief.carries:
        parts.append(f"carries: {', '.join(brief.carries)}")
    if brief.last_action:
        parts.append(f"last action: {brief.last_action}")
    if brief.definition:
        marker = " (truncated)" if brief.definition.get("_truncated") else ""
        parts.append(f"who they are{marker}: {json.dumps(brief.definition, sort_keys=True)[:600]}")
    return "; ".join(parts)


# --- Performer schema -------------------------------------------------------

# Declared with actual item shapes. The previous generation schema declared every
# array as ``{"type": "object"}`` with no properties, so a model was never told
# what a valid claim looks like and learned it only from prose.
PERFORMER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prose": {
            "type": "string",
            "description": "The scene segment as the reader experiences it.",
        },
        "narration": {
            "type": "string",
            "description": "Environment-only prose, empty when an actor is present.",
        },
        "dialogue": {
            "type": "array",
            "description": "One entry per speaking character, in the order they speak.",
            "items": {
                "type": "object",
                "properties": {
                    "character_id": {"type": "string"},
                    "name": {"type": "string"},
                    "line": {"type": "string"},
                },
                "required": ["character_id", "line"],
            },
        },
        "actor_actions": {
            "type": "array",
            "description": "One entry per acting character.",
            "items": {
                "type": "object",
                "properties": {
                    "character_id": {"type": "string"},
                    "name": {"type": "string"},
                    "action": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["reaction", "decision", "observation"],
                    },
                },
                "required": ["character_id", "action"],
            },
        },
        "proposed_events": {
            "type": "array",
            "description": "Proposed timeline events. Proposals only; the engine decides.",
            "items": {
                "type": "object",
                "properties": {
                    "event_type": {
                        "type": "string",
                        "enum": list(MODEL_WRITABLE_EVENT_TYPES),
                    },
                    "character_id": {"type": "string"},
                    "source_character_id": {"type": "string"},
                    "target_character_id": {"type": "string"},
                    "text": {"type": "string"},
                    "action": {"type": "string"},
                    "item": {"type": "string"},
                    "injury": {"type": "string"},
                    "severity": {"type": "string"},
                    "fact": {"type": "string"},
                    "relationship_type": {"type": "string"},
                    "strength": {"type": "number"},
                },
                "required": ["event_type"],
            },
        },
        "knowledge_changes": {
            "type": "array",
            "description": "Facts newly learned, by character.",
            "items": {
                "type": "object",
                "properties": {
                    "event_type": {"type": "string", "enum": ["knowledge_acquired", "knowledge_suspected"]},
                    "character_id": {"type": "string"},
                    "fact": {"type": "string"},
                },
                "required": ["character_id", "fact"],
            },
        },
        "completion_signals": {
            "type": "object",
            "description": "Whether the beat was realised, and what is left.",
            "properties": {
                "beat_realised": {"type": "boolean"},
                "beat_partial": {"type": "boolean"},
                "required_satisfied": {"type": "array", "items": {"type": "string"}},
                "required_unmet": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "required": ["prose", "proposed_events"],
}


# --- Reading the result -----------------------------------------------------


def read_performer_result(
    value: Any, *, request: PerformerRequest
) -> PerformerResult:
    """Turn a provider response into a result, or explain why it is unusable.

    Shape problems are reported as a single readable reason rather than a stack
    of individual complaints, because a model that returned the wrong shape needs
    one clear thing to fix.
    """
    if not isinstance(value, dict):
        raise ValueError("Performer response must be an object")

    prose = value.get("prose", "")
    if not isinstance(prose, str):
        raise ValueError("Performer response prose must be a string")
    if not prose.strip():
        # Not an error. An empty turn is a real outcome — the scene held, nobody
        # moved — and the pipeline has its own fallback prose for it. Raising here
        # would make "the model wrote nothing" a provider failure rather than a
        # quiet turn.
        notes = ["the performer returned no prose for this turn"]
    else:
        notes = []

    known_ids = {brief.character_id for brief in request.actors}
    names_by_id = {brief.character_id: brief.name for brief in request.actors}

    dialogue: list[ActorTurn] = []
    for entry in _as_list(value.get("dialogue")):
        if not isinstance(entry, dict):
            continue
        character_id = str(entry.get("character_id") or "")
        line = str(entry.get("line") or entry.get("dialogue") or entry.get("text") or "")
        if not character_id or not line:
            continue
        if character_id not in known_ids:
            # A speaker who is not in this scene cannot speak. Dropping the line
            # is better than letting it reach the timeline.
            continue
        dialogue.append(
            ActorTurn(
                character_id=character_id,
                name=names_by_id.get(character_id, str(entry.get("name") or "")),
                dialogue=line,
                kind="dialogue",
            )
        )

    actor_actions: list[ActorTurn] = []
    for entry in _as_list(value.get("actor_actions")):
        if not isinstance(entry, dict):
            continue
        character_id = str(entry.get("character_id") or "")
        action = str(entry.get("action") or entry.get("text") or "")
        if not character_id or not action:
            continue
        if character_id not in known_ids:
            continue
        kind = str(entry.get("kind") or "reaction")
        actor_actions.append(
            ActorTurn(
                character_id=character_id,
                name=names_by_id.get(character_id, str(entry.get("name") or "")),
                action=action,
                kind=kind if kind in {"reaction", "decision", "observation"} else "reaction",
            )
        )

    proposed_events = [
        dict(entry) for entry in _as_list(value.get("proposed_events")) if isinstance(entry, dict)
    ]
    if not proposed_events:
        # Legacy providers return ``new_events``. Accepted so a model that has not
        # moved to the Performer's shape still produces a working turn; preferred
        # over merging, because a response carrying both would otherwise commit
        # every event twice under different names.
        proposed_events = [
            dict(entry) for entry in _as_list(value.get("new_events")) if isinstance(entry, dict)
        ]
    for event in proposed_events:
        if event.get("event_type"):
            event["event_type"] = canonical_event_type(str(event["event_type"]))

    state_claims = [
        dict(entry) for entry in _as_list(value.get("state_changes")) if isinstance(entry, dict)
    ]
    if not state_claims:
        state_claims = [
            dict(entry)
            for entry in _as_list(value.get("relationship_changes"))
            if isinstance(entry, dict)
        ]

    knowledge_changes = [
        dict(entry)
        for entry in _as_list(value.get("knowledge_changes"))
        if isinstance(entry, dict)
    ]

    signals = value.get("completion_signals")
    signals = dict(signals) if isinstance(signals, dict) else {}
    notes.extend(str(item) for item in _as_list(signals.get("notes")))

    return PerformerResult(
        prose=prose,
        narration=str(value.get("narration") or ""),
        dialogue=dialogue,
        actor_actions=actor_actions,
        proposed_events=proposed_events,
        state_claims=state_claims,
        knowledge_changes=knowledge_changes,
        completion_signals=signals,
        required_satisfied=[str(item) for item in _as_list(signals.get("required_satisfied"))],
        required_unmet=[str(item) for item in _as_list(signals.get("required_unmet"))],
        notes=notes,
    )


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else []


# --- Post-checks ------------------------------------------------------------


@dataclass
class PerformerViolation:
    """Something the Performer proposed that it was not allowed to do."""

    code: str
    summary: str
    character_id: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "summary": self.summary,
            "character_id": self.character_id,
            "detail": self.detail,
        }


def check_user_agency(result: PerformerResult, *, request: PerformerRequest) -> list[PerformerViolation]:
    """A user-controlled character must not be given a decision or a line.

    The user owns their character's intentional actions. Writing "I open the
    drawer" as "I open the drawer, gasp, draw my weapon, and accuse the butler" is
    a takeover: four decisions the user did not make, two of them irreversible.

    The check is deliberately narrow about *what* is forbidden. Blocking any
    mention of a possessed character would make possession useless, because the
    Performer still has to describe the hand reaching the drawer. What is refused
    is a **decision** — something that commits the character to a course of
    action — and **dialogue**, which is the user speaking, not the engine.

    An ``observation`` or ``reaction`` attributed to a possessed character is
    connective tissue and is allowed: the drawer opens, the dust lifts, the
    witness inhales. That is the Performer supplying texture, not intent.

    ``kind`` is deliberately *not* consulted. It is a field the model fills in,
    so a check that reads it is a request rather than a guarantee — and the
    request is one token away from being ignored. An earlier version read
    ``kind == "decision"`` and was bypassed by sending the same action labelled
    ``reaction``, which then committed as an authoritative ``user_action`` for a
    character the user owns. What the user asked for is the ground truth here,
    and ``user_input`` is the one statement of intent that is not model-authored.

    The check also covers ``proposed_events`` and ``state_claims``. It used to
    read ``actor_actions`` only, so a turn that asserted state for the possessed
    character in a claim rather than in the action list passed unexamined.
    """
    violations: list[PerformerViolation] = []
    for brief in request.actors:
        if not brief.agency_withheld:
            continue
        for turn in result.actor_actions:
            if turn.character_id != brief.character_id:
                continue
            if not _grounded_in_user_input(turn.action, request.user_input):
                violations.append(
                    PerformerViolation(
                        code="user_agency_violation",
                        summary=(
                            f"The Performer invented a major decision for the "
                            f"user-controlled character {brief.name}"
                        ),
                        character_id=brief.character_id,
                        detail=turn.action[:200],
                    )
                )
        for claim, label in (
            *(
                (event, "proposed event")
                for event in result.proposed_events
            ),
            *(
                (claim, "state claim")
                for claim in result.state_claims
            ),
        ):
            claimed_id = claim.get("character_id") or claim.get("entity_id")
            if claimed_id != brief.character_id:
                continue
            violations.append(
                PerformerViolation(
                    code="user_agency_violation",
                    summary=(
                        f"A {label} asserts state for the user-controlled "
                        f"character {brief.name}"
                    ),
                    character_id=brief.character_id,
                    detail=json.dumps(claim, default=str)[:200],
                )
            )
        for turn in result.dialogue:
            if turn.character_id != brief.character_id:
                continue
            violations.append(
                PerformerViolation(
                    code="user_voice_violation",
                    summary=(
                        f"The Performer spoke for the user-controlled character {brief.name}"
                    ),
                    character_id=brief.character_id,
                    detail=turn.dialogue[:200],
                )
            )
    return violations


def check_offstage_actors(
    result: PerformerResult, *, request: PerformerRequest, state: StateSnapshot
) -> list[PerformerViolation]:
    """Nobody dead, off-stage, or non-participant may act or speak.

    A user-controlled actor is treated separately from an absent one. Describing
    the user's character is legitimate — a hand on the drawer, a held breath — so
    only a *decision* attributed to them is a violation. A dead or off-stage
    character has no such allowance: they are not in the room at all.
    """
    violations: list[PerformerViolation] = []
    acting_ids = set(request.acting_ids)
    present_ids = set(request.present_ids)
    for turn in (*result.dialogue, *result.actor_actions):
        brief = request.actor(turn.character_id)
        if turn.character_id in acting_ids:
            continue
        name = brief.name if brief else turn.character_id
        if brief is not None and not brief.alive:
            code, summary = (
                "dead_character_acts",
                f"{name} is dead on this timeline but the Performer wrote them acting",
            )
        elif brief is not None and brief.agency_withheld:
            # A user-controlled actor is present, so only a decision is a
            # violation; see check_user_agency, which is the precise check.
            continue
        elif turn.character_id in present_ids:
            continue
        elif brief is not None:
            code, summary = (
                "offstage_character_acts",
                f"{name} is not in this scene but the Performer wrote them acting",
            )
        else:
            code, summary = (
                "non_participant_acts",
                f"The Performer wrote {name}, who is not a participant in this scene",
            )
        violations.append(
            PerformerViolation(
                code=code,
                summary=summary,
                character_id=turn.character_id,
                detail=(turn.dialogue or turn.action)[:200],
            )
        )
    for event in result.proposed_events:
        character_id = str(event.get("character_id") or "")
        if not character_id or character_id in acting_ids:
            continue
        if character_id in state.dead:
            violations.append(
                PerformerViolation(
                    code="dead_character_event",
                    summary=f"A proposed event has the dead character {character_id} as its actor",
                    character_id=character_id,
                    detail=json.dumps(event, default=str)[:200],
                )
            )
    return violations


def check_required_consequences(
    result: PerformerResult, *, request: PerformerRequest
) -> tuple[list[str], list[str]]:
    """Split required consequences into satisfied and unmet.

    The check is intentionally forgiving about wording and strict about presence.
    A required consequence is a demand on the turn, and a demand that silently
    failed is worse than one reported as unmet — the user would be told the plan
    is progressing when it is not.
    """
    if not request.required_consequences:
        return [], []
    satisfied: list[str] = []
    unmet: list[str] = []
    reported_satisfied = {item.strip().casefold() for item in result.required_satisfied}
    haystack = " ".join(
        [
            result.prose,
            *[
                item
                for event in result.proposed_events
                for item in [json.dumps(event, default=str)]
            ],
            *[turn.action for turn in result.actor_actions],
            *[turn.dialogue for turn in result.dialogue],
        ]
    ).casefold()
    for consequence in request.required_consequences:
        wanted = consequence.strip().casefold()
        if wanted in reported_satisfied or _content_present(wanted, haystack):
            satisfied.append(consequence)
        else:
            unmet.append(consequence)
    return satisfied, unmet


def _content_present(wanted: str, haystack: str) -> bool:
    """Whether a consequence's distinctive words appear anywhere in the turn."""
    tokens = [token for token in wanted.replace(",", " ").split() if len(token) > 3]
    if not tokens:
        return wanted in haystack
    return all(token in haystack for token in tokens)


_STOPWORDS = frozenset(
    """a an and are as at be been by for from has have he her him his in into is it its
    of on or she that the their them then there they this to was were with you your""".split()
)


def _grounded_in_user_input(action: str, user_input: str) -> bool:
    """Whether ``action`` restates something the user actually asked for.

    This is the trust boundary for a user-controlled character. The Performer
    cannot be asked whether it took over — it fills in that field itself — so the
    only statement of user intent that is not model-authored is the user's own
    input. An action passes when its distinctive words come from there.

    Deliberately a *weak* test in the permissive direction: any substantial
    overlap is enough, and short connective actions are excused. The failure mode
    this accepts is a legitimate-sounding action the user did not quite ask for;
    the failure mode it prevents is a character signing away their own estate
    because a label said "reaction".
    """
    if not user_input.strip():
        return False
    action_words = [
        word
        for word in re.findall(r"[a-z']+", action.casefold())
        if word not in _STOPWORDS and len(word) > 3
    ]
    if not action_words:
        # Nothing substantive claimed: texture, not intent.
        return True
    input_words = set(re.findall(r"[a-z']+", user_input.casefold()))
    return any(word in input_words for word in action_words)


# --- Beat progression -------------------------------------------------------


def realize_beat(plan: DirectorPlanDraft, *, beat: Beat | None) -> BeatStatus:
    """Mark the performed beat and report the beat now active, if any.

    A beat the Performer did not manage is ``skipped``, not failed. The scene
    moved on; that is improvisation working, not the engine breaking. A plan
    whose beats are all skipped is complete.
    """
    if beat is None:
        return BeatStatus.SKIPPED
    if beat.status in {BeatStatus.PENDING, BeatStatus.ACTIVE}:
        beat.status = BeatStatus.COMPLETED
    following = plan.active_beat()
    return following.status if following is not None else BeatStatus.COMPLETED
