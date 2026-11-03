"""Minimal read-only contact directory, using the existing authenticated shell."""

from fastapi import APIRouter, HTTPException, Query, Request

from app.services.contacts.workspace import list_contacts
from app.web.deps import render

router = APIRouter()


@router.get("/contacts")
async def contacts_page(request: Request, before_id: int | None = Query(None, gt=0)):
    try:
        page = await list_contacts(limit=50, before_id=before_id)
    except PermissionError:
        raise HTTPException(403, "Contact access denied") from None
    return render(request, "web/contacts.html", section="contacts", **page)
