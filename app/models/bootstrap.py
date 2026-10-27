"""One explicit manager-binding stage after ORM class registration."""

from importlib import import_module


def bind_managers(models):
    from .managers.base_manager import BaseManager
    from .managers.role_manager import RoleManager

    models.PlatformRole.objects = RoleManager(models.PlatformRole)
    models.WorkspaceRole.objects = BaseManager(models.WorkspaceRole)
    models.ModelType.objects = BaseManager(models.ModelType)
    models.LoginAttempt.objects = BaseManager(models.LoginAttempt)
    models.LLMOutcomeEvent.objects = import_module(
        "app.models.managers.llm_outcome_event_manager"
    ).LLMOutcomeEventManager()
    models.User.objects = import_module("app.models.managers.user_manager").UserManager()
    models.AgentFeedback.objects = import_module("app.models.managers.agent_feedback_manager").AgentFeedbackManager()
    models.AgentSession.objects = import_module("app.models.managers.agent_session_manager").AgentSessionManager()
    models.AgentMessage.objects = import_module("app.models.managers.agent_message_manager").AgentMessageManager()
    models.EntitlementUsage.objects = import_module(
        "app.models.managers.entitlement_manager"
    ).EntitlementManager()
    models.Tenant.objects = import_module("app.models.managers.tenant_manager").TenantManager()
    models.TenantUser.objects = import_module("app.models.managers.tenant_manager").TenantUserManager()
    models.TenantInvite.objects = import_module("app.models.managers.tenant_manager").TenantInviteManager()
    models.TenantChannel.objects = import_module("app.models.managers.tenant_manager").TenantChannelManager()
    models.Permission.objects = import_module("app.models.managers.permission_manager").PermissionManager()
    models.LLMDispatchReceipt.objects = import_module(
        "app.models.managers.llm_dispatch_receipt_manager"
    ).LLMDispatchReceiptManager()
    models.DigestRun.objects = import_module("app.models.managers.digest_run_manager").DigestRunManager()
    models.NotificationReceipt.objects = import_module("app.models.managers.notification_receipt_manager").NotificationReceiptManager()
    models.Notification.objects = import_module("app.models.managers.notification_manager").NotificationManager()
    models.NotificationPreference.objects = import_module(
        "app.models.managers.notification_preference_manager"
    ).NotificationPreferenceManager()
    models.Job.objects = import_module("app.models.managers.job_manager").JobManager()
    models.CollectedItem.objects = import_module("app.models.managers.collected_item_manager").CollectedItemManager()
    models.CollectionSeen.objects = import_module(
        "app.models.managers.collection_seen_manager"
    ).CollectionSeenManager()
    models.CollectionCandidate.objects = import_module(
        "app.models.managers.collection_candidate_manager"
    ).CollectionCandidateManager()
    models.CollectionLaneRun.objects = import_module(
        "app.models.managers.collection_lane_run_manager"
    ).CollectionLaneRunManager()
    models.AgentMemory.objects = import_module("app.models.managers.agent_memory_manager").AgentMemoryManager()
    models.Platform.objects = import_module("app.models.managers.platform_manager").PlatformManager()
    models.LLMModel.objects = import_module("app.models.managers.llm_model_manager").LLMModelManager()
    models.UserCredential.objects = import_module("app.models.managers.user_credential_manager").UserCredentialManager()
    models.LLMAttempt.objects = import_module("app.models.managers.llm_attempt_manager").LLMAttemptManager()
    models.AIAnalytics.objects = import_module("app.models.managers.ai_analytics_manager").AIAnalyticsManager()
    models.AnalyzedContentReceipt.objects = import_module(
        "app.models.managers.analyzed_content_receipt_manager"
    ).AnalyzedContentReceiptManager()
    models.LLMBudgetReservation.objects = import_module("app.models.managers.llm_budget_manager").LLMBudgetManager()
    personal_billing = import_module("app.models.managers.personal_billing_manager")
    models.PersonalBillingAccount.objects = personal_billing.PersonalBillingManager()
    models.TaskBillingPayer.objects = BaseManager(models.TaskBillingPayer)
    models.PersonalLLMDispatch.objects = BaseManager(models.PersonalLLMDispatch)
    models.PersonalBillingCutover.objects = BaseManager(models.PersonalBillingCutover)
    models.PersonalLLMBoundPolicy.objects = BaseManager(models.PersonalLLMBoundPolicy)
    models.PersonalLLMBudgetDay.objects = BaseManager(models.PersonalLLMBudgetDay)
    models.PersonalLLMReservation.objects = BaseManager(models.PersonalLLMReservation)
    models.PersonalLLMReservationState.objects = BaseManager(models.PersonalLLMReservationState)
    models.PersonalLLMLedgerEntry.objects = BaseManager(models.PersonalLLMLedgerEntry)
    models.PersonalLegacyAttributionConsent.objects = BaseManager(models.PersonalLegacyAttributionConsent)
    models.PersonalLegacyAttribution.objects = BaseManager(models.PersonalLegacyAttribution)
    models.PersonalLLMBudgetDay.objects = import_module(
        "app.models.managers.personal_llm_billing_manager"
    ).PersonalLLMBillingManager()
    models.PersonalLLMBillingIncident.objects = BaseManager(models.PersonalLLMBillingIncident)
    models.LLMProvider.objects = import_module("app.models.managers.llm_provider_manager").LLMProviderManager()
    models.BotAction.objects = import_module("app.models.managers.bot_action_manager").BotActionManager()
    models.Source.objects = import_module("app.models.managers.source_manager").SourceManager()
    models.AgentTask.objects = import_module("app.models.managers.agent_task_manager").AgentTaskManager()
    models.AgentScenario.objects = import_module("app.models.managers.agent_scenario_manager").AgentScenarioManager()
    models.LLMBudgetDay.objects = BaseManager(models.LLMBudgetDay)
