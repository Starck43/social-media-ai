"""Models that must not be offered for, or auto-resolved into, content analysis.

Two guarded cases hurt operators in practice:

- **classifiers/guard models** (e.g. "…content-safety", "llama-guard") accept a
  chat-style request but drive analysis to `failed_output`: they answer with a
  safety verdict, never the scenario's schema;
- **models that were dispatched several times and never once succeeded** are
  dead weight in a picker — every accidental run burns attempts and ends in
  "модель не ответила".

Both are screened out of the scenario model pickers and the analyzer's
auto-fallbacks. An *explicitly configured* model still runs — an operator who
insists gets their run, the failure then surfaces in diagnostics («Ошибка» +
code), and the `llm_attempts` journal keeps the evidence.
"""

MIN_ATTEMPTS_BEFORE_HIDING = 3

_CLASSIFIER_MARKERS = (
    "content-safety",
    "aegis",
    "guard",
    "moderation",
    "shield",
    "refusal",
    "classify",
)


def is_classifier_like(*identifiers) -> bool:
    """True when any identifier carries a safety-classifier marker."""

    haystack = " ".join(str(value or "") for value in identifiers).casefold()
    return any(marker in haystack for marker in _CLASSIFIER_MARKERS)


def never_succeeded(use_count, fail_count, *, min_attempts: int = MIN_ATTEMPTS_BEFORE_HIDING) -> bool:
    """Tried `min_attempts`+ times, not a single success (use == fail + successes)."""

    if type(use_count) is not int or type(fail_count) is not int:
        return False
    return use_count >= min_attempts and fail_count >= use_count


def analysis_capable(model) -> bool:
    """Tolerant predicate over a model object; unknown counters never hide."""

    if is_classifier_like(getattr(model, "model_id", ""), getattr(model, "name", "")):
        return False
    return not never_succeeded(getattr(model, "use_count", None), getattr(model, "fail_count", None))


# Models occasionally answer with a refusal instead of the scenario schema; the
# observed wording (DeepSeek, DeepSeek-V4 family) begins exactly like this.
REFUSAL_MARKERS = (
    "недостаточно данных",
    "insufficient data",
)


def is_refusal_summary(summary) -> bool:
    """True when a parsed analysis summary is a model's no-data refusal.

    Such an answer must never be saved as an analysis: it carries no topic, no
    title and no usable coverage, so it lands in «(без темы)» and blocks its
    records from being retried via dedup coverage.
    """

    if not isinstance(summary, str):
        return False
    normalized = summary.casefold().strip()
    return any(normalized.startswith(marker) for marker in REFUSAL_MARKERS)
