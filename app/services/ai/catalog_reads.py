"""Explicit funding-aware catalog reads for the dormant dispatch consumers.

Only a trusted server integration may bind this scope, never posted identity.
There is no implicit owner-OR-platform query, credential load or HTTP here.
Unbound consumers retain the accepted personal path until an approved cutover.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from app.services.ai.dispatch_seam import CODE_CATALOG_ACCESS_MISSING, DispatchContext, DispatchDenial

_reads = ContextVar("funding_catalog_reads", default=None)


def current_catalog_reads():
    return _reads.get()


class FundingCatalogReads:
    def __init__(self, session, context, *, required_modules=(), cache=None):
        from app.services.ai.catalog_grants import CatalogGrantResolver

        if not isinstance(context, DispatchContext):
            raise DispatchDenial(CODE_CATALOG_ACCESS_MISSING, "step4_grants")
        self.session = session
        self.context = context
        self.resolver = CatalogGrantResolver(session, required_modules=required_modules, cache=cache)
        self._metadata = {}

    async def metadata(self, *, capabilities):
        try:
            rows = await self.resolver.eligible_metadata(self.context, frozenset(capabilities))
        except BaseException:
            self._metadata.clear()
            raise
        self._metadata = {row.id: row for row in rows}
        return rows

    async def models(self, *, capabilities, model_id=None, model_name=None):
        """Safe ORM projections of freshly eligible IDs; explicit misses deny.

        The projected provider cannot decrypt a credential. This read scope is
        not an execution token; the legacy client refuses it before key access.
        """
        from sqlalchemy import select
        from sqlalchemy.orm import load_only, selectinload

        from app.models import LLMCatalog, LLMModel, LLMProvider

        metadata = await self.metadata(capabilities=capabilities)
        if model_id is not None:
            if type(model_id) is not int or model_id <= 0:
                raise DispatchDenial(CODE_CATALOG_ACCESS_MISSING, "step4_grants")
            metadata = tuple(row for row in metadata if row.id == model_id)
        if model_name is not None:
            metadata = tuple(row for row in metadata if row.name == model_name)
        if (model_id is not None or model_name is not None) and len(metadata) != 1:
            raise DispatchDenial(CODE_CATALOG_ACCESS_MISSING, "step4_grants")
        if not metadata:
            return []
        stmt = (
            select(LLMModel)
            .join(
                LLMProvider, (LLMProvider.id == LLMModel.provider_id) & (LLMProvider.catalog_id == LLMModel.catalog_id)
            )
            .join(LLMCatalog, LLMCatalog.id == LLMModel.catalog_id)
            .where(
                LLMModel.id.in_([row.id for row in metadata]),
                LLMCatalog.kind == self.context.funding_class,
                LLMCatalog.is_active.is_(True),
                LLMProvider.is_active.is_(True),
                LLMModel.is_active.is_(True),
            )
            .options(
                load_only(
                    LLMModel.id,
                    LLMModel.catalog_id,
                    LLMModel.owner_user_id,
                    LLMModel.provider_id,
                    LLMModel.name,
                    LLMModel.model_id,
                    LLMModel.model_type,
                    LLMModel._capabilities_column,
                    LLMModel.max_tokens,
                    LLMModel.max_output_tokens,
                    LLMModel.default_temperature,
                    LLMModel.is_active,
                    LLMModel.is_default,
                    LLMModel.use_count,
                    LLMModel.fail_count,
                    raiseload=True,
                ),
                selectinload(LLMModel.provider).load_only(
                    LLMProvider.id,
                    LLMProvider.catalog_id,
                    LLMProvider.owner_user_id,
                    LLMProvider.name,
                    LLMProvider.uid,
                    LLMProvider.api_format,
                    LLMProvider.is_active,
                    raiseload=True,
                ),
            )
            .execution_options(populate_existing=True)
        )
        if self.context.funding_class == "personal":
            stmt = stmt.where(
                LLMCatalog.owner_user_id == self.context.plan_holder_user_id,
                LLMModel.owner_user_id == self.context.plan_holder_user_id,
                LLMProvider.owner_user_id == self.context.plan_holder_user_id,
            )
        else:
            stmt = stmt.where(
                LLMCatalog.owner_user_id.is_(None),
                LLMModel.owner_user_id.is_(None),
                LLMProvider.owner_user_id.is_(None),
            )
        rows = list((await self.session.scalars(stmt)).all())
        if (model_id is not None or model_name is not None) and len(rows) != 1:
            raise DispatchDenial(CODE_CATALOG_ACCESS_MISSING, "step4_grants")
        rows.sort(key=self.default_key)
        return rows

    def eligible_row(self, model, *, capabilities):
        metadata = self._metadata.get(getattr(model, "id", None))
        provider = getattr(model, "provider", None)
        return bool(
            metadata is not None
            and provider is not None
            and getattr(model, "catalog_id", None) == metadata.catalog_id
            and getattr(provider, "id", None) == metadata.provider_id
            and getattr(provider, "catalog_id", None) == metadata.catalog_id
            and set(capabilities) <= set(metadata.capabilities)
            and model.is_active
            and provider.is_active
        )

    def default_key(self, model):
        metadata = self._metadata.get(getattr(model, "id", None))
        if metadata is None:
            return (True, 2**31, getattr(model, "id", 0))
        return (not metadata.is_default, metadata.priority, metadata.id)


@contextmanager
def catalog_read_scope(session, context, *, required_modules=(), cache=None):
    """Bind a caller-owned, short read transaction; never bind from a payload."""
    scope = FundingCatalogReads(session, context, required_modules=required_modules, cache=cache)
    token = _reads.set(scope)
    try:
        yield scope
    finally:
        _reads.reset(token)
