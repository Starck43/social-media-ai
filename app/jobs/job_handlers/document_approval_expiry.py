"""Bounded expiration of document approvals in the current workspace."""

from app.services.knowledge.approvals import approval_service


async def handle_document_approval_expiry(payload):
    return await approval_service.expire_due(limit=payload.get("limit", 20))
