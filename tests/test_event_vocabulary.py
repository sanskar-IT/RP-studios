"""The Performer's event vocabulary must match what the engine accepts.

The schema the model is handed is the only specification of what a valid claim
looks like. When it disagrees with the validator, a schema-compliant model
produces claims that are rejected — and because validation is all-or-nothing,
one wrong event name costs the whole turn, including the prose and the beat.
"""

from __future__ import annotations

import pytest

from services.context.claims import extract_claims, validate_claims
from services.core.events import (
    EVENT_TYPE_ALIASES,
    INJURY_ADDED,
    ITEM_ACQUIRED,
    ITEM_REMOVED,
    KNOWN_EVENT_TYPES,
    MODEL_WRITABLE_EVENT_TYPES,
    canonical_event_type,
)
from services.core.state import StateSnapshot
from services.narrative.pipeline import GENERATION_SCHEMA
from services.performer.performer import PERFORMER_SCHEMA


def _schema_enum(schema: dict) -> set[str]:
    return set(schema["properties"]["proposed_events"]["items"]["properties"]["event_type"]["enum"])


@pytest.mark.parametrize(
    "schema",
    [PERFORMER_SCHEMA, GENERATION_SCHEMA],
    ids=["performer", "pipeline"],
)
def test_every_advertised_event_type_is_accepted_by_the_engine(schema):
    """A name the model is told to use must be a name the engine applies.

    This is the regression test for three event types that shipped in the schema
    with no counterpart in ``KNOWN_EVENT_TYPES`` (``item_obtained``,
    ``item_destroyed``, ``character_injured``). Picking up a locket was a 422.
    """
    unknown = _schema_enum(schema) - KNOWN_EVENT_TYPES
    assert not unknown, f"schema advertises event types the engine rejects: {sorted(unknown)}"


@pytest.mark.parametrize(
    "schema",
    [PERFORMER_SCHEMA, GENERATION_SCHEMA],
    ids=["performer", "pipeline"],
)
def test_schema_does_not_advertise_engine_only_events(schema):
    """The Performer narrates the room; it does not author the world.

    Advertising ``world_fact_created`` invites the model to publish a private
    belief as canon, which is then rendered to every character as true.
    """
    overreach = _schema_enum(schema) - set(MODEL_WRITABLE_EVENT_TYPES)
    assert not overreach, f"schema advertises non-proposable events: {sorted(overreach)}"


def test_advertised_enum_covers_everything_a_performer_may_propose():
    """The schema must be a complete vocabulary, not a narrow subset.

    Otherwise a legitimate action — healing, unlocking — has no advertised name
    and the model has to guess.
    """
    assert _schema_enum(PERFORMER_SCHEMA) == set(MODEL_WRITABLE_EVENT_TYPES)


@pytest.mark.parametrize("alias,canonical", sorted(EVENT_TYPE_ALIASES.items()))
def test_an_alias_resolves_to_a_type_the_engine_accepts(alias, canonical):
    assert canonical in KNOWN_EVENT_TYPES
    assert canonical_event_type(alias) == canonical


def test_an_unknown_event_type_is_left_alone_for_the_validator_to_reject():
    """Normalising must not invent validity for arbitrary names."""
    assert canonical_event_type("not_a_real_event") == "not_a_real_event"


@pytest.mark.parametrize(
    "claimed,expected",
    [
        ("item_obtained", ITEM_ACQUIRED),
        ("item_destroyed", ITEM_REMOVED),
        ("character_injured", INJURY_ADDED),
    ],
)
def test_an_aliased_claim_is_normalised_before_validation(claimed, expected):
    """A naming mistake must cost the turn only the claim, never the scene."""
    structured = {
        "prose": "A picks up the locket.",
        "new_events": [
            {
                "event_type": claimed,
                "character_id": "c1",
                "item": "locket",
                "injury": "graze",
                "severity": "minor",
            }
        ],
        "state_changes": [],
        "actions": [],
        "open_commitments": [],
    }
    claims = extract_claims(structured, actor_id="c1", participant_ids={"c1"})
    normalised = [
        {**claim.payload, "event_type": canonical_event_type(claim.event_type)}
        for claim in claims.events
    ]
    from services.context.claims import ClaimedEvent, GenerationClaims

    rebuilt = GenerationClaims(prose=claims.prose, events=[ClaimedEvent(event_type=e["event_type"], payload={k: v for k, v in e.items() if k != "event_type"}) for e in normalised])
    validation = validate_claims(rebuilt, StateSnapshot())
    assert validation.valid, validation.errors
    assert validation.accepted[0].event_type == expected
