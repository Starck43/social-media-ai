"""Minimal /app/commands review list reusing action-review UI patterns (P13-T04)."""

from fastapi import APIRouter, HTTPException, Query, Request

from app.services.commands import CommandError, command_ledger
from app.web.deps import render

router = APIRouter()


@router.get("/commands")
async def commands_page(request: Request, before_id: int | None = Query(None, gt=0)):
    try:
        page = await command_ledger.list_commands(limit=50, before_id=before_id)
    except (PermissionError, CommandError):
        raise HTTPException(403, "Command ledger access denied") from None
    return render(request, "web/commands.html", section="actions", **page)
