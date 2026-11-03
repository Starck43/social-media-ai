"""Internal, bounded bridge from the existing owner-authorized reader to catalog.

No default transport, browser metadata input, recursive scan or runtime job is
registered. The caller owns the application-controlled client and scan scheduling.
Production UI refresh must wait for the P8-T34 transport/route integration review.
"""

from __future__ import annotations

import httpx

from app.models.managers.file_catalog_manager import CatalogInputError, FileCatalogManager
from app.services.file_workspace.yandex_reader import list_owned_folder


async def refresh_catalog_page(
    *,
    tenant_id: int,
    actor_id: int,
    connection_id: int,
    folder: str,
    client: httpx.AsyncClient,
    offset: int = 0,
    limit: int = 100,
) -> dict:
    """Read/admit exactly one page, rechecking policy before and after the read.

    The required client is supplied by trusted application code, never by a web
    form. No provider-wide traversal and no deletion inference from an empty or
    truncated page. A changed/revoked policy after the read denies persistence.
    Cursor values are continuation hints, not snapshot or sync-completion proof.
    """
    if client is None or type(offset) is not int or not 0 <= offset <= 1_000_000:
        raise CatalogInputError("invalid_page")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise CatalogInputError("invalid_page")
    catalog = FileCatalogManager()
    ticket = await catalog.begin_page(
        tenant_id=tenant_id, actor_id=actor_id, connection_id=connection_id, folder=folder
    )
    page = await list_owned_folder(
        tenant_id=tenant_id,
        actor_id=actor_id,
        connection_id=connection_id,
        folder=ticket.folder,
        client=client,
        offset=offset,
        limit=limit,
    )
    result = await catalog.admit_page(
        tenant_id=tenant_id, actor_id=actor_id, connection_id=connection_id, ticket=ticket, page=page
    )
    return {**result, "next_offset": page.next_offset, "truncated": page.truncated}
