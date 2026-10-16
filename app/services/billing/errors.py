"""Classifier for pre-dispatch personal billing denials across broad callers."""


def is_personal_billing_block(error: BaseException) -> bool:
    """Identify errors that must never become a fallback or retry envelope."""
    from app.models.managers.personal_billing_manager import PersonalBillingError, PersonalBillingUnavailable
    from app.models.managers.personal_llm_billing_manager import PersonalDispatchConflict
    from app.services.ai.budget_admission import (
        BudgetAdmissionConflict,
        BudgetAdmissionDenied,
        BudgetAdmissionUnavailable,
    )
    return isinstance(
        error,
        (
            PersonalBillingError,
            PersonalBillingUnavailable,
            PersonalDispatchConflict,
            BudgetAdmissionConflict,
            BudgetAdmissionDenied,
            BudgetAdmissionUnavailable,
        ),
    )


def raise_personal_billing_block(error: BaseException) -> None:
    """Reraise a billing block before an outer broad failure handler can hide it."""
    if is_personal_billing_block(error):
        raise error
