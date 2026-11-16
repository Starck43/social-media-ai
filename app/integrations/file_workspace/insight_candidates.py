"""Inactive synthetic candidate contracts; no model/client/tool registration.

Every excerpt and candidate is untrusted DATA. Reference consistency does not
establish truth, relevance, entailment, authority or permission to call a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.integrations.file_workspace.line_evidence import LineEvidence

MAX_INSIGHT_CANDIDATES = 5
MAX_CANDIDATE_CHARACTERS = 600
MAX_TOTAL_CANDIDATE_CHARACTERS = 1800
MAX_CANDIDATE_REFERENCES = 4
CANDIDATE_TIMEOUT = 5.0


class InsightCandidatesUnavailable(Exception):
    def __init__(self):
        super().__init__("insight_candidates_unavailable")


@dataclass(frozen=True, slots=True, repr=False)
class CandidateExcerpt:
    index: int
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True, repr=False)
class CandidateGenerationInput:
    """Minimal selected excerpts; no source IDs/path/hash/version/credentials.

    generation_id is a fresh response-binding nonce, not a source/read identity
    or authorization. No user-controlled instruction, system role or tool grant.
    A future real-model prompt adapter must maintain the untrusted-data framing.
    """

    generation_id: str
    excerpts: tuple[CandidateExcerpt, ...]
    untrusted_data: bool = field(default=True, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class InsightCandidate:
    text: str
    evidence_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True, repr=False)
class GeneratedInsightCandidates:
    generation_id: str
    candidates: tuple[InsightCandidate, ...]


@dataclass(frozen=True, slots=True, repr=False)
class InsightCandidateBatch:
    """In-memory candidates requiring review; NOT retained/accepted knowledge.

    Empty candidates means synthetic abstention, not proof there are no insights.
    Presence of citations is a structural check, not semantic verification.
    """

    generation_id: str
    evidence: LineEvidence
    candidates: tuple[InsightCandidate, ...]
    review_status: str = field(default="unverified", init=False)


class InsightCandidateGenerator(Protocol):
    async def generate(self, data: CandidateGenerationInput) -> GeneratedInsightCandidates:
        """Synthetic/in-process implementation only in this checkpoint.

        No network, external model, paid call, tools, storage or autonomous action.
        A future real generator needs separately reviewed model consent, budget/
        usage accounting, provider routing and safe prompt/response integration.
        READ/cloud-processing authority alone does not grant those capabilities.
        """
        ...
