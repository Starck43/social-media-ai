"""One explicit manager-binding stage after ORM class registration."""

from importlib import import_module


def bind_managers(models):
    from .managers.base_manager import BaseManager
    from .managers.role_manager import RoleManager

    models.PlatformRole.objects = RoleManager(models.PlatformRole)
    models.WorkspaceRole.objects = BaseManager(models.WorkspaceRole)
    models.ModelType.objects = BaseManager(models.ModelType)
    models.LLMOutcomeEvent.objects = import_module("app.models.llm_outcome_event").LLMOutcomeEventManager()
    models.User.objects = import_module("app.models.user").UserManager()
    models.AgentFeedback.objects = import_module("app.models.agent_feedback").AgentFeedbackManager()
    models.AgentSession.objects = import_module("app.models.agent_session").AgentSessionManager()
    models.AgentMessage.objects = import_module("app.models.agent_message").AgentMessageManager()
    models.Tenant.objects = import_module("app.models.tenant").TenantManager()
    models.TenantUser.objects = import_module("app.models.tenant").TenantUserManager()
    models.TenantInvite.objects = import_module("app.models.tenant").TenantInviteManager()
    models.TenantChannel.objects = import_module("app.models.tenant").TenantChannelManager()
    models.Permission.objects = import_module("app.models.permission").PermissionManager()
    models.LLMDispatchReceipt.objects = import_module("app.models.llm_dispatch_receipt").LLMDispatchReceiptManager()
    models.DigestRun.objects = import_module("app.models.digest_run").DigestRunManager()
    models.Notification.objects = import_module("app.models.notification").NotificationManager()
    models.Job.objects = import_module("app.models.job").JobManager()
    models.CollectedItem.objects = import_module("app.models.collected_item").CollectedItemManager()
    models.AgentMemory.objects = import_module("app.models.agent_memory").AgentMemoryManager()
    models.Platform.objects = import_module("app.models.platform").PlatformManager()
    models.LLMModel.objects = import_module("app.models.llm_model").LLMModelManager()
    models.UserCredential.objects = import_module("app.models.user_credential").UserCredentialManager()
    models.LLMAttempt.objects = import_module("app.models.llm_attempt").LLMAttemptManager()
    models.AIAnalytics.objects = import_module("app.models.ai_analytics").AIAnalyticsManager()
    models.LLMBudgetReservation.objects = import_module("app.models.llm_budget").LLMBudgetManager()
    models.LLMProvider.objects = import_module("app.models.llm_provider").LLMProviderManager()
    models.BotAction.objects = import_module("app.models.bot_action").BotActionManager()
    models.Source.objects = import_module("app.models.source").SourceManager()
    models.AgentTask.objects = import_module("app.models.agent_task").AgentTaskManager()
    models.AgentScenario.objects = import_module("app.models.agent_scenario").AgentScenarioManager()
    models.LLMBudgetDay.objects = BaseManager(models.LLMBudgetDay)
