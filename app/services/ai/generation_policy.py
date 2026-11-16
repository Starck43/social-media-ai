"""Model output ceilings, distinct from generation defaults and total allowance.

Callers pass already-approved purpose/plan output bounds. This helper does not
derive an output budget from a token ledger or use temperature as a cost limit.
"""

from __future__ import annotations


def requested_output_limit(model, *, requested=None, purpose_limit=None, approved_plan_limit=None):
    """Apply restrictive output bounds without changing a model's stored default.

    An absent model ceiling means unknown, not a ceiling inferred from context
    size. An absent approved bound means this helper received no such bound; it
    never proves allowance/admission. Zero explicitly denies output generation.
    """
    value = getattr(model, "max_tokens", None) if requested is None else requested
    if type(value) is not int or value <= 0:
        raise ValueError("A positive requested output limit is required")
    bounds = (getattr(model, "max_output_tokens", None), purpose_limit, approved_plan_limit)
    for bound in bounds:
        if bound is None:
            continue
        if type(bound) is not int or bound < 0:
            raise ValueError("Invalid approved output bound")
        value = min(value, bound)
    if value == 0:
        raise PermissionError("Output generation is not allowed")
    return value
