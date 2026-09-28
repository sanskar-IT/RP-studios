"""Context engine: budget accounting, priority, knowledge, validation, and tracing."""

from __future__ import annotations

from services.context.assembly import (
    ContextAssembly,
    ContextComponent,
    ContextItem,
    ContextOverflowError,
    ContextPriority,
    GenerationContextRequest,
    build_generation_context,
)
from services.context.claims import ClaimedEvent, ClaimValidation, GenerationClaims, validate_claims
from services.context.consistency import ContradictionDetector, ContradictionWarning
from services.context.knowledge import KnowledgeFact, KnowledgeView, build_knowledge_view
from services.context.tokens import (
    TokenBudget,
    TokenEstimate,
    estimate_messages,
    estimate_tokens,
    estimator_for,
)
from services.context.trace import GenerationTrace

__all__ = [
    "ClaimValidation",
    "ClaimedEvent",
    "ContradictionDetector",
    "ContradictionWarning",
    "ContextAssembly",
    "ContextComponent",
    "ContextItem",
    "ContextOverflowError",
    "ContextPriority",
    "GenerationClaims",
    "GenerationContextRequest",
    "GenerationTrace",
    "KnowledgeFact",
    "KnowledgeView",
    "TokenBudget",
    "TokenEstimate",
    "build_generation_context",
    "build_knowledge_view",
    "estimate_messages",
    "estimate_tokens",
    "estimator_for",
    "validate_claims",
]
