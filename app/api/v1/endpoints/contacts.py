"""Read-only, authenticated contact API; merge/import remain out of scope."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.api.deps import require_model_perm
from app.models.managers.contact_manager import ContactUnavailable
from app.services.contacts.workspace import ContactPrivacy, contact_detail, list_contacts
from app.types import ActionType

router = APIRouter(dependencies=[Depends(require_model_perm("contact", ActionType.VIEW))])


@router.get("")
async def contacts_list(limit: int = Query(50, ge=1, le=100), before_id: int | None = Query(None, gt=0)):
    return await list_contacts(limit=limit, before_id=before_id)


@router.get("/{contact_id}")
async def contacts_detail(contact_id: int = Path(gt=0)):
    try:
        return await contact_detail(contact_id)
    except ContactUnavailable:
        raise HTTPException(404, "Contact unavailable") from None


@router.get("/{contact_id}/export", dependencies=[Depends(require_model_perm("contact", ActionType.EXPORT))])
async def contacts_export(contact_id: int = Path(gt=0)):
    """Explicit privacy export includes identities, consent evidence and the merge journal."""
    try:
        return await ContactPrivacy.export_contact(contact_id)
    except ContactUnavailable:
        raise HTTPException(404, "Contact unavailable") from None
