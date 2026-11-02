"""Procurement cases, supplier comparison table, and approval router (P12-T05).

Provides the `/app/cases` route displaying deterministic supplier price comparisons
in plain user-friendly Russian, with version-bound approval status and transparent
4-layer connector provenance.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.services.cases.approvals import CANONICAL_CASE_RECORD, CONNECTOR_STORAGE_LAYERS
from app.services.cases.calculator import CANONICAL_COMPARISON_ROWS
from app.web.deps import render

router = APIRouter()


@router.get("/cases")
async def cases_page(request: Request):
    """Render procurement case comparison table and version-bound approval card."""
    return render(
        request,
        "web/cases.html",
        case_record=CANONICAL_CASE_RECORD,
        comparison_rows=CANONICAL_COMPARISON_ROWS,
        connector_storage_layers=CONNECTOR_STORAGE_LAYERS,
    )
