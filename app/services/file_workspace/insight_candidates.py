"""Mock-only, bounded, reauthorized compact candidate orchestration.

No AIAnalyzer/provider/model/task registration, persistence, cache or UI. Inject
trusted synthetic code only. Never promote these candidates into verified facts.
"""

from __future__ import annotations

import asyncio
import unicodedata
from uuid import uuid4

from app.integrations.file_workspace.content_read import ContentReadAuthority, ReadPlainText
from app.integrations.file_workspace.insight_candidates import (
    CANDIDATE_TIMEOUT,
    MAX_CANDIDATE_CHARACTERS,
    MAX_CANDIDATE_REFERENCES,
    MAX_INSIGHT_CANDIDATES,
    MAX_TOTAL_CANDIDATE_CHARACTERS,
    CandidateExcerpt,
    CandidateGenerationInput,
    GeneratedInsightCandidates,
    InsightCandidate,
    InsightCandidateBatch,
    InsightCandidateGenerator,
    InsightCandidatesUnavailable,
)
from app.integrations.file_workspace.line_evidence import LineEvidence, verify_line_evidence


def _live(deadline: float) -> None:
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError()
    if asyncio.get_running_loop().time() >= deadline:
        raise InsightCandidatesUnavailable()


async def _reauthorize(source, evidence, authority, deadline) -> None:
    fresh = await authority.authorize(source.context.request)
    _live(deadline)
    # Public evidence verification enforces strict context primitive types as
    # well as full equality, avoiding bool/int equality and context substitutions.
    current = ReadPlainText(fresh, source.extraction, source.provider_version)
    if not verify_line_evidence(evidence, current):
        raise InsightCandidatesUnavailable()
    _live(deadline)


def _validated_candidates(response, data) -> tuple[InsightCandidate, ...]:
    if (
        type(response) is not GeneratedInsightCandidates
        or type(response.generation_id) is not str
        or response.generation_id != data.generation_id
        or type(response.candidates) is not tuple
        or len(response.candidates) > MAX_INSIGHT_CANDIDATES
    ):
        raise InsightCandidatesUnavailable()
    total = 0
    validated = []
    for candidate in response.candidates:
        if (
            type(candidate) is not InsightCandidate
            or type(candidate.text) is not str
            or not 0 < len(candidate.text) <= MAX_CANDIDATE_CHARACTERS
            or not candidate.text.strip()
            or any(unicodedata.category(char).startswith("C") and char not in "\t\n" for char in candidate.text)
            or type(candidate.evidence_indices) is not tuple
            or not 1 <= len(candidate.evidence_indices) <= MAX_CANDIDATE_REFERENCES
        ):
            raise InsightCandidatesUnavailable()
        previous = 0
        for index in candidate.evidence_indices:
            if type(index) is not int or not previous < index <= len(data.excerpts):
                raise InsightCandidatesUnavailable()
            previous = index
        total += len(candidate.text)
        if total > MAX_TOTAL_CANDIDATE_CHARACTERS:
            raise InsightCandidatesUnavailable()
        validated.append(InsightCandidate(candidate.text, candidate.evidence_indices))
    return tuple(validated)


async def _generate(source, evidence, authority, generator, deadline) -> InsightCandidateBatch:
    if not verify_line_evidence(evidence, source):
        raise InsightCandidatesUnavailable()
    _live(deadline)
    await _reauthorize(source, evidence, authority, deadline)
    data = CandidateGenerationInput(
        uuid4().hex,
        tuple(
            CandidateExcerpt(index, item.start, item.end, item.text) for index, item in enumerate(evidence.excerpts, 1)
        ),
    )
    _live(deadline)
    response = await generator.generate(data)
    _live(deadline)
    candidates = _validated_candidates(response, data)
    _live(deadline)
    await _reauthorize(source, evidence, authority, deadline)
    _live(deadline)
    return InsightCandidateBatch(data.generation_id, evidence, candidates)


async def generate_insight_candidates(
    source: ReadPlainText,
    evidence: LineEvidence,
    *,
    authority: ContentReadAuthority | None = None,
    generator: InsightCandidateGenerator | None = None,
) -> InsightCandidateBatch:
    """No default generator/authority, retries, provider or external model grant.

    One 5s cooperative budget covers verification, both fresh authority assertions,
    synthetic generation and validation. Cancellation propagates; no partial
    candidates on ordinary errors. Context checks do not certify real ACL SQL,
    and referenced text does not semantically verify generated claims. No paid
    model/token budgets are implemented or implied by these character limits.
    """
    try:
        if authority is None or generator is None:
            raise InsightCandidatesUnavailable()
        deadline = asyncio.get_running_loop().time() + CANDIDATE_TIMEOUT
        async with asyncio.timeout(CANDIDATE_TIMEOUT):
            return await _generate(source, evidence, authority, generator, deadline)
    except Exception:
        pass
    # No chained provider/authority/generator details in ordinary tracebacks.
    raise InsightCandidatesUnavailable()
