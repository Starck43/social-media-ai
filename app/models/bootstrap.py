"""One explicit manager-binding stage after ORM class registration."""

from importlib import import_module


def bind_managers(models):
    from .managers.base_manager import BaseManager
    from .managers.role_manager import RoleManager

    models.PlatformRole.objects = RoleManager(models.PlatformRole)
    models.WorkspaceRole.objects = BaseManager(models.WorkspaceRole)
    models.ModelType.objects = BaseManager(models.ModelType)
    models.LLMOutcomeEvent.objects = import_module(
        "app.models.managers.llm_outcome_event_manager"
    ).LLMOutcomeEventManager()
    models.User.objects = import_module("app.models.managers.user_manager").UserManager()
    models.AgentFeedback.objects = import_module("app.models.managers.agent_feedback_manager").AgentFeedbackManager()
    models.AgentSession.objects = import_module("app.models.managers.agent_session_manager").AgentSessionManager()
    models.AgentMessage.objects = import_module("app.models.managers.agent_message_manager").AgentMessageManager()
    models.Tenant.objects = import_module("app.models.managers.tenant_manager").TenantManager()
    models.TenantUser.objects = import_module("app.models.managers.tenant_manager").TenantUserManager()
    models.TenantInvite.objects = import_module("app.models.managers.tenant_manager").TenantInviteManager()
    models.TenantChannel.objects = import_module("app.models.managers.tenant_manager").TenantChannelManager()
    models.Permission.objects = import_module("app.models.managers.permission_manager").PermissionManager()
    models.LLMDispatchReceipt.objects = import_module(
        "app.models.managers.llm_dispatch_receipt_manager"
    ).LLMDispatchReceiptManager()
    models.DigestRun.objects = import_module("app.models.managers.digest_run_manager").DigestRunManager()
    models.Notification.objects = import_module("app.models.managers.notification_manager").NotificationManager()
    models.Job.objects = import_module("app.models.managers.job_manager").JobManager()
    models.CollectedItem.objects = import_module("app.models.managers.collected_item_manager").CollectedItemManager()
    models.AgentMemory.objects = import_module("app.models.managers.agent_memory_manager").AgentMemoryManager()
    models.Platform.objects = import_module("app.models.managers.platform_manager").PlatformManager()
    models.LLMModel.objects = import_module("app.models.managers.llm_model_manager").LLMModelManager()
    models.UserCredential.objects = import_module("app.models.managers.user_credential_manager").UserCredentialManager()
    models.LLMAttempt.objects = import_module("app.models.managers.llm_attempt_manager").LLMAttemptManager()
    models.AIAnalytics.objects = import_module("app.models.managers.ai_analytics_manager").AIAnalyticsManager()
    models.LLMBudgetReservation.objects = import_module("app.models.managers.llm_budget_manager").LLMBudgetManager()
    models.LLMProvider.objects = import_module("app.models.managers.llm_provider_manager").LLMProviderManager()
    models.BotAction.objects = import_module("app.models.managers.bot_action_manager").BotActionManager()
    models.Source.objects = import_module("app.models.managers.source_manager").SourceManager()
    models.AgentTask.objects = import_module("app.models.managers.agent_task_manager").AgentTaskManager()
    models.AgentScenario.objects = import_module("app.models.managers.agent_scenario_manager").AgentScenarioManager()
    models.LLMBudgetDay.objects = BaseManager(models.LLMBudgetDay)
