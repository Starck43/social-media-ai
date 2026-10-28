"""Versioned resource identities, role captions and workspace presets.

Keys are independent of Python class names and delivery interfaces. Adding a
mapped model never creates permissions implicitly.
"""

from dataclasses import dataclass

from app.types import ActionType, PlatformRoleType, WorkspaceRoleType


@dataclass(frozen=True)
class Resource:
    key: str
    model_name: str
    table_name: str
    actions: frozenset[ActionType]
    workspace: bool = False
    personal: bool = False


def _resource(key, model, table, actions, workspace=False, personal=False):
    return Resource(key, model.lower(), table, frozenset(ActionType[a] for a in actions.split()), workspace, personal)


RESOURCES = (
    _resource("identity.user", "User", "users", "VIEW CREATE UPDATE DELETE"),
    _resource("identity.platform_role", "PlatformRole", "roles", "VIEW CONFIGURE"),
    _resource("identity.permission", "Permission", "permissions", "VIEW"),
    _resource("identity.model_type", "ModelType", "model_types", "VIEW"),
    _resource(
        "identity.credential",
        "UserCredential",
        "user_credentials",
        "VIEW CREATE UPDATE DELETE CONFIGURE",
        personal=True,
    ),
    _resource("tenancy.workspace", "Tenant", "tenants", "VIEW UPDATE CONFIGURE", True),
    _resource("tenancy.membership", "TenantUser", "tenant_users", "VIEW CREATE UPDATE DELETE", True),
    _resource("tenancy.workspace_role", "WorkspaceRole", "workspace_roles", "VIEW"),
    _resource("tenancy.invite", "TenantInvite", "tenant_invites", "VIEW CREATE UPDATE DELETE", True),
    _resource("tenancy.channel", "TenantChannel", "tenant_channels", "VIEW CREATE UPDATE DELETE", True),
    _resource("collection.source", "Source", "sources", "VIEW CREATE UPDATE DELETE ANALYZE EXPORT", True),
    _resource("collection.platform", "Platform", "platforms", "VIEW CREATE UPDATE DELETE"),
    _resource("collection.collected_item", "CollectedItem", "collected_items", "VIEW CREATE UPDATE DELETE", True),
    _resource("analysis.scenario", "AgentScenario", "agent_scenarios", "VIEW CREATE UPDATE DELETE CONFIGURE", True),
    _resource("analysis.result", "AIAnalytics", "ai_analytics", "VIEW CREATE UPDATE DELETE ANALYZE EXPORT", True),
    _resource("llm.provider", "LLMProvider", "llm_providers", "VIEW CREATE UPDATE DELETE CONFIGURE"),
    _resource("llm.model", "LLMModel", "llm_models", "VIEW CREATE UPDATE DELETE CONFIGURE"),
    _resource("llm.attempt", "LLMAttempt", "llm_attempts", "VIEW CREATE", True),
    _resource("llm.budget_day", "LLMBudgetDay", "llm_budget_days", "VIEW", True),
    _resource("llm.budget_reservation", "LLMBudgetReservation", "llm_budget_reservations", "VIEW", True),
    _resource("llm.dispatch_receipt", "LLMDispatchReceipt", "llm_dispatch_receipts", "VIEW", True),
    _resource("llm.outcome_event", "LLMOutcomeEvent", "llm_outcome_events", "VIEW", True),
    _resource("scheduling.task", "AgentTask", "agent_tasks", "VIEW CREATE UPDATE DELETE CONFIGURE", True),
    _resource("scheduling.job", "Job", "jobs", "VIEW UPDATE DELETE", True),
    _resource("scheduling.action", "BotAction", "bot_actions", "VIEW CREATE UPDATE DELETE", True),
    _resource("agent.session", "AgentSession", "agent_sessions", "VIEW CREATE UPDATE DELETE", True, True),
    _resource("agent.message", "AgentMessage", "agent_messages", "VIEW CREATE UPDATE DELETE", True, True),
    _resource("agent.memory", "AgentMemory", "agent_memory", "VIEW CREATE UPDATE DELETE", True, True),
    _resource("agent.feedback", "AgentFeedback", "agent_feedback", "VIEW CREATE UPDATE DELETE", True, True),
    _resource("notifications.notification", "Notification", "notifications", "VIEW CREATE UPDATE DELETE", True),
    _resource("notifications.digest_run", "DigestRun", "digest_runs", "VIEW CREATE UPDATE DELETE EXPORT", True),
    _resource("audit.action_audit", "ActionAuditEvent", "action_audit_events", "VIEW EXPORT", True),
)
_BY_KEY = {r.key: r for r in RESOURCES}
_BY_MODEL = {r.model_name: r for r in RESOURCES}
_BY_MODEL["role"] = _BY_KEY["identity.platform_role"]
_BY_MODEL["credential"] = _BY_KEY["identity.credential"]
_BY_TABLE = {r.table_name: r for r in RESOURCES}


def resource_for(value):
    if isinstance(value, Resource):
        return value
    if isinstance(value, type):
        value = value.__name__
    if not isinstance(value, str):
        return None
    key = value.lower()
    return _BY_KEY.get(key) or _BY_MODEL.get(key) or _BY_TABLE.get(key)


# Explicit one-release aliases. Never infer a new domain from caller input.
_OLD_DOMAINS = {
    "identity": ("account", "app"),
    "tenancy": ("account",),
    "collection": ("social",),
    "analysis": ("social", "dashboard"),
    "llm": ("ai", "account"),
    "scheduling": ("social",),
    "agent": ("social",),
    "notifications": ("social",),
    "audit": (),
}
_ALIASES = {
    f"{old}.{r.model_name}.{a.db_value}": (r, a)
    for r in RESOURCES
    for old in _OLD_DOMAINS[r.key.split(".")[0]]
    for a in r.actions
}
_ALIASES.update(
    {
        f"app.role.{a.db_value}": (_BY_KEY["identity.platform_role"], a)
        for a in _BY_KEY["identity.platform_role"].actions
    }
)


def resolve_permission_key(key):
    if not isinstance(key, str):
        return None
    key = key.strip().lower()
    prefix, _, token = key.rpartition(".")
    resource = _BY_KEY.get(prefix)
    action = ActionType.get_by_value(token)
    if resource is not None and action in resource.actions:
        return resource, action
    return _ALIASES.get(key)


PLATFORM_LABELS = {
    "user": "Пользователь",
    "operator": "Оператор",
    "admin": "Администратор платформы",
    "superuser": "Собственник платформы",
}
WORKSPACE_LABELS = {
    "owner": "Владелец",
    "admin": "Администратор",
    "manager": "Менеджер",
    "analyst": "Аналитик",
    "viewer": "Наблюдатель",
}


def role_label(codename, *, workspace=False):
    return (WORKSPACE_LABELS if workspace else PLATFORM_LABELS).get(str(codename), "Роль не назначена")


def workspace_grants(codename):
    """Fixed presets, never platform or personal-secret administration."""
    try:
        role = WorkspaceRoleType(codename)
    except (ValueError, TypeError):
        return frozenset()
    grants = set()
    for r in RESOURCES:
        if not r.workspace:
            continue
        actions = set()
        if role in (WorkspaceRoleType.OWNER, WorkspaceRoleType.ADMIN):
            actions = set(r.actions)
        elif role == WorkspaceRoleType.MANAGER:
            if r.key in ("collection.source", "analysis.scenario", "scheduling.task"):
                actions = set(r.actions)
            elif r.key in ("analysis.result", "notifications.digest_run", "notifications.notification"):
                actions = {ActionType.VIEW}
        elif role == WorkspaceRoleType.ANALYST:
            if r.key == "collection.source":
                actions = {ActionType.VIEW, ActionType.ANALYZE, ActionType.EXPORT}
            elif r.key == "analysis.result":
                actions = {ActionType.VIEW, ActionType.ANALYZE, ActionType.EXPORT}
            elif r.key == "notifications.digest_run":
                actions = {ActionType.VIEW, ActionType.CREATE, ActionType.EXPORT}
        elif role == WorkspaceRoleType.VIEWER:
            if r.key in ("collection.source", "analysis.result", "notifications.digest_run"):
                actions = {ActionType.VIEW}
        # Everyone with AI access can operate ONLY their own conversation records.
        if r.personal:
            actions = set(r.actions)
        grants.update((r.model_name, a) for a in actions & r.actions)
    return frozenset(grants)
