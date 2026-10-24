"""Resolve personal credentials from durable job identity, not untrusted payload."""
from app.core.personal_catalog_context import catalog_execution_scope, without_catalog_access


async def run_with_job_catalog(job, handler, payload):
    if job.job_type not in {"collect", "analyze", "digest", "learn", "reflect"}:
        return await handler(payload)
    if job.job_type == "collect":
        # Pure collection has no model side effect. Existing explicit payer is
        # resolved when available; absent consent may not guess a catalog.
        from app.models import TaskBillingPayer
        consent = await TaskBillingPayer.objects.filter(
            agent_task_id=job.agent_task_id, tenant_id=job.tenant_id
        ).first() if job.agent_task_id is not None else None
        if consent is None and job.initiated_by_user_id is None:
            with without_catalog_access():
                return await handler(payload)
    if job.agent_task_id is not None:
        from app.services.billing.payer import resolve_scheduled_task_payer
        account = await resolve_scheduled_task_payer(task_id=job.agent_task_id, tenant_id=job.tenant_id)
        owner_id = account.user_id
    else:
        from app.models import User
        from app.models.managers.tenant_manager import tenant_users
        owner_id = job.initiated_by_user_id
        user = await User.objects.get(id=owner_id, is_active=True) if owner_id is not None else None
        members = await tenant_users.web_memberships(owner_id) if user else []
        if user is None or not any(m.tenant_id == job.tenant_id for m in members):
            raise PermissionError("Job has no active personal catalog owner")
    with catalog_execution_scope(owner_id):
        return await handler(payload)
