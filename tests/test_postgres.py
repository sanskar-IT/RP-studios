"""Opt-in PostgreSQL and pgvector coverage.

These tests never run against the default SQLite suite. They are collected
normally and skip unless `NARRATIVE_TEST_DATABASE_URL` points at a reachable
PostgreSQL database with pgvector available, so a normal `pytest` run stays
SQLite-only:

    pytest -m postgres

The URL must address a disposable test database. The fixture applies the
Alembic chain to it once per session, and every test runs inside a transaction
that is rolled back afterwards, so no test data survives the session.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from migration_support import (
    TEST_DATABASE_URL_ENV,
    format_diff,
    metadata_diff,
    upgrade,
)
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from apps.api.app import repository
from services.core.enums import CommitmentStatus, MemoryClass, MemoryScope
from services.core.events import CHARACTER_MOVED, KNOWLEDGE_ACQUIRED
from services.core.models import (
    Character,
    CharacterCard,
    DirectorIntent,
    Event,
    Generation,
    Memory,
    ModelConfig,
    ModelProvider,
    Project,
    Scene,
    SceneParticipant,
    StoryCommitment,
    Timeline,
    TimelineNode,
    World,
)

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="session")
def postgres_url() -> str:
    url = os.environ.get(TEST_DATABASE_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set; the opt-in PostgreSQL suite is disabled")
    if not url.startswith("postgresql"):
        pytest.skip(
            f"{TEST_DATABASE_URL_ENV} must use a postgresql+psycopg URL, got {url.split(':')[0]}"
        )
    probe = create_engine(url)
    try:
        with probe.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except SQLAlchemyError as exc:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not reachable: {type(exc).__name__}")
    finally:
        probe.dispose()
    return url


@pytest.fixture(scope="session")
def postgres_engine(postgres_url: str) -> Iterator[Engine]:
    upgrade(postgres_url)
    engine = create_engine(postgres_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session(postgres_engine: Engine) -> Iterator[Session]:
    """A session that commits for real but never outlives the test."""
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


def seed_world(session: Session) -> tuple[Project, Character, Scene]:
    project = repository.create_project(session, "PostgreSQL validation")
    character = repository.add_character(
        session,
        project_id=project.id,
        name="Detective",
        definition={"appearance": "grey coat", "aliases": ["the Inspector"]},
    )
    scene = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="The library",
        participant_ids=[character.id],
    )
    session.commit()
    return project, character, scene


def test_migration_upgrade_is_idempotent(postgres_url: str, postgres_engine: Engine):
    upgrade(postgres_url)
    assert metadata_diff(postgres_engine) == []


def test_migration_chain_matches_models_on_postgresql(postgres_engine: Engine):
    with postgres_engine.connect() as connection:
        extension = connection.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one()
    assert extension, "the pgvector extension must be installed by the baseline migration"
    diff = metadata_diff(postgres_engine)
    assert diff == [], f"migration chain and models differ on PostgreSQL:\n{format_diff(diff)}"
    embedding = {
        column["name"]: column for column in inspect(postgres_engine).get_columns("memories")
    }["embedding"]
    assert embedding["nullable"] is True
    assert str(embedding["type"].compile(dialect=postgres_engine.dialect)).upper() == "VECTOR"


def test_core_entities_persist_and_reload(session: Session):
    project, character, scene = seed_world(session)
    repository.append_event(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        event_type=CHARACTER_MOVED,
        payload={"character_id": character.id, "location_id": "library"},
    )
    repository.add_generation(
        session,
        project_id=project.id,
        scene_id=scene.id,
        timeline_id=project.active_timeline_id,
        status="completed",
        structured_output={"committed_event_ids": [], "beats": 2},
        provider_name="heuristic",
        model_name="local",
    )
    provider = ModelProvider(name=f"provider-{project.id}", base_url="http://localhost:1/v1")
    session.add(provider)
    session.flush()
    session.add(ModelConfig(provider_id=provider.id, name="local", model_name="local"))
    session.add(CharacterCard(character_id=character.id, payload={"spec": "v2"}, extensions={}))
    session.commit()
    session.expire_all()

    assert session.scalar(select(World).where(World.project_id == project.id)) is not None
    assert session.get(Project, project.id).active_timeline_id == project.active_timeline_id
    assert session.get(Character, character.id).name == "Detective"
    assert session.get(Scene, scene.id).title == "The library"
    assert session.get(Timeline, project.active_timeline_id).name == "Main"
    assert session.scalar(
        select(SceneParticipant).where(SceneParticipant.scene_id == scene.id)
    ).character_id == character.id
    assert session.scalar(select(TimelineNode).where(TimelineNode.timeline_id == project.active_timeline_id))
    assert session.scalar(select(Event).where(Event.timeline_id == project.active_timeline_id)).event_type == CHARACTER_MOVED
    assert session.scalar(select(Generation).where(Generation.project_id == project.id)).provider_name == "heuristic"
    assert session.get(CharacterCard, session.scalar(select(CharacterCard.id))).card_version == "2"


def test_json_columns_round_trip_without_loss(session: Session):
    project, character, scene = seed_world(session)
    definition = {
        "appearance": "grey coat",
        "greeting": "The eastern door is locked. — “quoted”, path C:\\keys",
        "traits": ["observant", "suspicious"],
        "counts": {"observations": 3, "confidence": 0.75},
        "nested": [{"scene": "library", "beat": 1}, {"scene": "hallway", "beat": 2}],
    }
    character.definition = definition
    character.extra_data = {"imported": True, "source": "card"}
    scene.staging = {"status": "proposed", "assumptions": ["the vault is empty"]}
    repository.add_generation(
        session,
        project_id=project.id,
        scene_id=scene.id,
        timeline_id=project.active_timeline_id,
        status="completed",
        structured_output={"committed_event_ids": ["e1", "e2"], "beats": 2},
        lore_debug={"activated": [{"key": "eastern door", "depth": 1}]},
    )
    repository.append_event(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        event_type=KNOWLEDGE_ACQUIRED,
        payload={"character_id": character.id, "fact": "the decree is forged"},
    )
    repository.add_memory(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        content="The eastern door is locked",
        memory_class=MemoryClass.PERMANENT,
        character_id=character.id,
        metadata={"source_event_id": "e1", "weights": [0.1, 0.2]},
    )
    session.commit()
    session.expire_all()

    reloaded = session.get(Character, character.id)
    assert reloaded.definition == definition
    assert reloaded.extra_data == {"imported": True, "source": "card"}
    assert session.get(Scene, scene.id).staging["assumptions"] == ["the vault is empty"]
    generation = session.scalar(select(Generation).where(Generation.project_id == project.id))
    assert generation.structured_output == {"committed_event_ids": ["e1", "e2"], "beats": 2}
    assert generation.lore_debug["activated"][0]["depth"] == 1
    event = session.scalar(select(Event).where(Event.timeline_id == project.active_timeline_id))
    assert event.payload == {"character_id": character.id, "fact": "the decree is forged"}
    memory = session.scalar(select(Memory).where(Memory.project_id == project.id))
    assert memory.metadata_json == {"source_event_id": "e1", "weights": [0.1, 0.2]}


def test_foreign_keys_are_enforced(session: Session):
    project, character, _ = seed_world(session)
    timeline_id = project.active_timeline_id
    character_id = character.id
    orphan_project_id = "00000000-0000-0000-0000-000000000000"
    session.add(
        Scene(
            project_id=orphan_project_id,
            timeline_id=timeline_id,
            title="Orphan",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    assert session.scalar(select(Scene).where(Scene.project_id == orphan_project_id)) is None

    session.execute(text("DELETE FROM characters WHERE id = :character_id"), {"character_id": character_id})
    session.expire_all()
    assert (
        session.scalar(select(SceneParticipant).where(SceneParticipant.character_id == character_id))
        is None
    )


def test_transaction_rollback_discards_committed_work(session: Session):
    project = repository.create_project(session, "Rollback")
    session.commit()
    project_id = project.id
    timeline_id = project.active_timeline_id
    character = repository.add_character(session, project_id=project_id, name="Witness")
    character_id = character.id
    session.flush()
    assert session.get(Character, character_id) is not None

    session.rollback()
    assert session.get(Character, character_id) is None
    assert session.get(Project, project_id) is not None
    assert session.get(Timeline, timeline_id) is not None


def test_timeline_fork_preserves_source_and_branch_state(session: Session):
    project, character, _ = seed_world(session)
    event, node, _state = repository.append_event(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        event_type=CHARACTER_MOVED,
        payload={"character_id": character.id, "location_id": "library"},
    )
    repository.add_memory(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        content="The eastern door is locked",
        memory_class=MemoryClass.PERMANENT,
        character_id=character.id,
    )
    session.commit()
    source_events = [item.id for item in repository.list_events(session, project.active_timeline_id)]

    branch = repository.fork_timeline(
        session,
        source_timeline_id=project.active_timeline_id,
        source_node_id=node.id,
        name="Alternate",
    )
    session.commit()
    branch_events = repository.list_events(session, branch.id)
    branch_root = session.scalar(
        select(TimelineNode)
        .where(TimelineNode.timeline_id == branch.id)
        .order_by(TimelineNode.sequence)
    )
    assert branch.parent_timeline_id == project.active_timeline_id
    assert branch.forked_from_node_id == node.id
    assert branch_root.sequence == 0
    assert branch_root.checkpoint == node.checkpoint
    assert branch_events[0].event_type == "timeline_forked"
    assert branch_events[0].payload["source_node_id"] == node.id
    assert [item.id for item in repository.list_events(session, project.active_timeline_id)] == source_events
    assert repository.current_state(session, branch.id).characters[character.id]["location_id"] == "library"
    copied = session.scalar(select(Memory).where(Memory.timeline_id == branch.id))
    assert copied.content == "The eastern door is locked"
    assert session.get(Event, event.id).node_id == node.id

    repository.append_event(
        session,
        project_id=project.id,
        timeline_id=branch.id,
        event_type=KNOWLEDGE_ACQUIRED,
        payload={"character_id": character.id, "fact": "the branch diverges"},
    )
    session.commit()
    assert len(repository.list_events(session, branch.id)) == 2
    assert [item.id for item in repository.list_events(session, project.active_timeline_id)] == source_events


def test_production_constraints_reject_duplicate_order_and_second_current_scene(session: Session):
    project, character, scene = seed_world(session)
    committed = repository.append_event(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        event_type=CHARACTER_MOVED,
        payload={"character_id": character.id, "location_id": "library"},
    )
    session.commit()
    session.add(
        Event(
            project_id=project.id,
            timeline_id=project.active_timeline_id,
            node_id=committed[1].id,
            sequence=committed[0].sequence,
            event_type=CHARACTER_MOVED,
            payload={"character_id": character.id, "location_id": "hallway"},
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    session.add(
        Scene(project_id=project.id, timeline_id=project.active_timeline_id, title="Second current", current=True)
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    replacement = repository.create_scene(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        title="Replacement",
    )
    session.commit()
    assert replacement.current is True
    assert session.get(Scene, scene.id).current is False
    assert session.scalar(
        select(Scene).where(Scene.timeline_id == project.active_timeline_id, Scene.current.is_(True))
    ).id == replacement.id


def test_memory_persistence_keeps_class_scope_and_null_embedding(session: Session):
    project, character, _ = seed_world(session)
    memories = [
        repository.add_memory(
            session,
            project_id=project.id,
            timeline_id=project.active_timeline_id,
            content=content,
            memory_class=memory_class,
            character_id=character.id,
            importance=importance,
            metadata={"order": index},
        )
        for index, (content, memory_class, importance) in enumerate(
            [
                ("The eastern door is locked", MemoryClass.PERMANENT, 0.9),
                ("The witness is waiting in the hallway", MemoryClass.SCENE, 0.6),
                ("An older market rumor", MemoryClass.ARCHIVE, 0.2),
            ]
        )
    ]
    session.commit()
    session.expire_all()

    stored = list(session.scalars(select(Memory).where(Memory.project_id == project.id)))
    assert {memory.content for memory in stored} == {memory.content for memory in memories}
    for memory in stored:
        assert memory.embedding is None
        assert memory.valid_from_sequence == repository.latest_node(session, project.active_timeline_id).sequence
    assert {
        memory.memory_class for memory in stored
    } == {MemoryClass.PERMANENT.value, MemoryClass.SCENE.value, MemoryClass.ARCHIVE.value}
    assert session.scalar(
        select(Memory).where(Memory.project_id == project.id, Memory.character_id == character.id)
    ) is not None
    assert (
        session.scalar(
            select(Memory).where(
                Memory.project_id == project.id,
                Memory.character_id != character.id,
            )
        )
        is None
    )

    session.execute(text("DELETE FROM characters WHERE id = :character_id"), {"character_id": character.id})
    session.expire_all()
    orphaned = list(session.scalars(select(Memory).where(Memory.project_id == project.id)))
    assert orphaned
    assert all(memory.character_id is None for memory in orphaned)


def test_reliability_columns_round_trip_on_postgresql(session: Session):
    """Opt-in PostgreSQL coverage for the reliability milestone schema.

    Requires ``NARRATIVE_TEST_DATABASE_URL``. Mirrors the SQLite suite's
    assertions for memory scope, commitment timeline scope, and the generation
    trace JSON columns against a real PostgreSQL database with pgvector.
    """
    project, character, _ = seed_world(session)
    scoped = repository.add_memory(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        content="The eastern door is locked",
        memory_class=MemoryClass.PERMANENT,
        scope=MemoryScope.CHARACTER,
        character_id=character.id,
        importance=0.9,
        source_event_id="event-1",
    )
    retired = repository.add_memory(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        content="An outdated rumor",
        memory_class=MemoryClass.SCENE,
        importance=0.2,
        is_active=False,
    )
    session.commit()
    session.expire_all()

    assert session.get(Memory, scoped.id).scope == MemoryScope.CHARACTER.value
    assert session.get(Memory, scoped.id).source_event_id == "event-1"
    assert session.get(Memory, scoped.id).is_active is True
    assert session.get(Memory, retired.id).is_active is False
    assert (
        session.scalar(
            select(Memory).where(
                Memory.project_id == project.id,
                Memory.timeline_id == project.active_timeline_id,
                Memory.is_active.is_(True),
            )
        )
        is not None
    )

    intent = DirectorIntent(
        project_id=project.id,
        intent_type="story_commitment",
        text="The General betrays the Emperor.",
        goal="The General betrays the Emperor.",
    )
    session.add(intent)
    session.flush()
    commitment = StoryCommitment(
        project_id=project.id,
        intent_id=intent.id,
        timeline_id=project.active_timeline_id,
        description="The General betrays the Emperor.",
        status=CommitmentStatus.PROGRESSING.value,
        progress=0.4,
        created_sequence=repository.latest_node(session, project.active_timeline_id).sequence,
    )
    session.add(commitment)
    session.flush()
    generation = repository.add_generation(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        status="completed",
        context_debug={"total_tokens": 512, "max_input_tokens": 7168},
        validation={"status": "valid"},
        trace={"status": "traced", "timings": [{"name": "llm_request", "duration_ms": 1.5}]},
    )
    session.commit()
    session.expire_all()

    assert session.get(StoryCommitment, commitment.id).status == CommitmentStatus.PROGRESSING.value
    assert session.get(StoryCommitment, commitment.id).progress == 0.4
    assert session.get(StoryCommitment, commitment.id).timeline_id == project.active_timeline_id
    stored_generation = session.get(Generation, generation.id)
    assert stored_generation.context_debug["total_tokens"] == 512
    assert stored_generation.validation["status"] == "valid"
    assert stored_generation.trace["timings"][0]["name"] == "llm_request"


def test_timeline_fork_copies_memory_scope_and_commitment_linkage(session: Session):
    """Opt-in PostgreSQL coverage for branch-scoped memory and commitments.

    Requires ``NARRATIVE_TEST_DATABASE_URL``.
    """
    project, character, _ = seed_world(session)
    memory = repository.add_memory(
        session,
        project_id=project.id,
        timeline_id=project.active_timeline_id,
        content="Alice survived the night.",
        memory_class=MemoryClass.PERMANENT,
        scope=MemoryScope.WORLD,
        importance=0.9,
    )
    intent = DirectorIntent(
        project_id=project.id,
        intent_type="story_commitment",
        text="The long plan.",
        goal="The long plan.",
    )
    session.add(intent)
    session.flush()
    commitment = StoryCommitment(
        project_id=project.id,
        intent_id=intent.id,
        timeline_id=project.active_timeline_id,
        description="The long plan.",
        status=CommitmentStatus.CREATED.value,
    )
    session.add(commitment)
    session.commit()

    node = repository.latest_node(session, project.active_timeline_id)
    branch = repository.fork_timeline(
        session,
        source_timeline_id=project.active_timeline_id,
        source_node_id=node.id,
        name="Alternate",
    )
    session.commit()
    session.expire_all()

    copies = list(session.scalars(select(Memory).where(Memory.timeline_id == branch.id)))
    assert len(copies) == 1
    assert copies[0].content == "Alice survived the night."
    assert copies[0].scope == MemoryScope.WORLD.value
    assert copies[0].metadata_json["inherited_from_memory_id"] == memory.id

    branch_commitments = list(
        session.scalars(select(StoryCommitment).where(StoryCommitment.timeline_id == branch.id))
    )
    assert len(branch_commitments) == 1
    assert branch_commitments[0].forked_from_commitment_id == commitment.id
    assert branch_commitments[0].status == CommitmentStatus.CREATED.value
