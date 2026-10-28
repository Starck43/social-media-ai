"""Synthetic VK journey inputs and explicit source-only dependency bindings.

No application package import, settings initialization, database or provider.
These IDs are fictional; integration runners must allocate their own records.
"""

from pathlib import Path
from types import ModuleType, SimpleNamespace

from tests.source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]


def module(name, **attributes):
    result = ModuleType(name)
    result.__dict__.update(attributes)
    return result


def journey_plan():
    return {
        "version": 2,
        "goal": "Find repair requests on the permitted community wall with evidence links",
        "platforms": ["vk"],
        "source_scope": "selected",
        "source_ids": [11],
        "scenario_id": 42,
        "vk_access": {"mode": "user", "credential_id": 7},
        "collection": {"content_types": ["posts"], "limit": 3},
        "window": {"type": "fixed", "start": "2026-10-20", "end": "2026-10-21"},
        "trigger": {"type": "once"},
        "pipeline": {"stages": ["collect", "analyze", "digest"]},
        "digest": {
            "window": {"type": "fixed", "start": "2026-10-19", "end": "2026-10-21"},
            "group_by": "sources",
            "time_breakdown": True,
        },
    }


def load_plan_modules():
    """Execute real parsers/preparation, replacing only collection settings."""
    identifiers = load_isolated_source("_p8_t41_identifiers", ROOT / "app/utils/identifiers.py")
    policy = load_isolated_source(
        "_p8_t41_policy",
        ROOT / "app/services/social/vk_access.py",
        {"app.utils.identifiers": identifiers},
    )
    intent = load_isolated_source(
        "_p8_t41_intent",
        ROOT / "app/services/collection/intent.py",
        {
            "app.core.config": module(
                "app.core.config",
                settings=SimpleNamespace(
                    SOCIAL_COLLECT_HARD_LIMIT=1000,
                    SOCIAL_COLLECT_LIMIT=1000,
                ),
            )
        },
    )
    plans = load_isolated_source(
        "_p8_t41_plans",
        ROOT / "app/services/task_plans.py",
        {"app.services.collection.intent": intent, "app.services.social.vk_access": policy},
    )
    preparation = load_isolated_source(
        "_p8_t41_preparation",
        ROOT / "app/services/task_preparation.py",
        {"app.services.task_plans": plans},
    )
    return policy, plans, preparation
