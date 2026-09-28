"""The Performer: it decides how a scene plays, and nothing else.

The Director decides what the scene is trying to accomplish. The Performer
decides how it plays out. The State Engine decides what actually became true.

Public surface:

- :class:`PerformerRequest` / :class:`PerformerResult` — the request and the
  proposal, both structured so a claim cannot hide in prose.
- :func:`build_performer_request` — assembles a request from scene, state, and
  plan, scoped to this scene's participants only.
- :func:`render_performer_prompt` — the prompt.
- :func:`read_performer_result` — reads a provider response, dropping anything
  that names someone who is not in the scene.
- :func:`check_user_agency`, :func:`check_offstage_actors`,
  :func:`check_required_consequences` — the post-checks the pipeline runs before
  anything reaches validation.
- :func:`realize_beat` — beat progression.
"""

from services.performer.performer import (
    PERFORMER_SCHEMA,
    PRESENTATION_STYLES,
    ActorBrief,
    ActorTurn,
    PerformerRequest,
    PerformerResult,
    PerformerViolation,
    build_actor_briefs,
    build_performer_request,
    check_offstage_actors,
    check_required_consequences,
    check_user_agency,
    normalize_style,
    read_performer_result,
    realize_beat,
    render_performer_prompt,
)

__all__ = [
    "PERFORMER_SCHEMA",
    "PRESENTATION_STYLES",
    "ActorBrief",
    "ActorTurn",
    "PerformerRequest",
    "PerformerResult",
    "PerformerViolation",
    "build_actor_briefs",
    "build_performer_request",
    "check_offstage_actors",
    "check_required_consequences",
    "check_user_agency",
    "normalize_style",
    "read_performer_result",
    "realize_beat",
    "render_performer_prompt",
]
