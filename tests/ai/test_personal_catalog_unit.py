"""Offline source-loaded regressions. Fake storage is not PostgreSQL acceptance.

No application bootstrap, engine, DB configuration, network, provider or real
credentials. Uses real SQLAlchemy mappings and actual service source with
explicit local dependency doubles. Runnable via unittest in an isolated venv.
"""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, configure_mappers
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("_personal_test_loader", ROOT / "tests/source_import_isolation.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)
load = loader.load_isolated_source


class Base(DeclarativeBase):
    metadata = sa.MetaData(schema="offline_catalog")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    is_active: Mapped[bool] = mapped_column(default=True)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(primary_key=True)


config = NS(settings=NS(DB_SCHEMA="offline_catalog"))
imports = {
    "isolated.core.config": config,
    "isolated.core.decorators": NS(app_label=lambda _: lambda cls: cls),
    "isolated.models.base": NS(Base=Base, TimestampMixin=object),
}
provider_module = load("isolated.models.llm.llm_provider", ROOT / "app/models/llm/llm_provider.py", imports)
model_module = load("isolated.models.llm.llm_model", ROOT / "app/models/llm/llm_model.py", imports)
Provider, Model = provider_module.LLMProvider, model_module.LLMModel


class AgentScenario(Base):
    __tablename__ = "agent_scenarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    text_llm_model_id: Mapped[int | None] = mapped_column(sa.ForeignKey("offline_catalog.llm_models.id"))
    image_llm_model_id: Mapped[int | None] = mapped_column(sa.ForeignKey("offline_catalog.llm_models.id"))
    video_llm_model_id: Mapped[int | None] = mapped_column(sa.ForeignKey("offline_catalog.llm_models.id"))
    text_llm_model = relationship(Model, foreign_keys=[text_llm_model_id], back_populates="text_scenarios")
    image_llm_model = relationship(Model, foreign_keys=[image_llm_model_id], back_populates="image_scenarios")
    video_llm_model = relationship(Model, foreign_keys=[video_llm_model_id], back_populates="video_scenarios")


configure_mappers()


class Result:
    def __init__(self, rows): self.rows = rows
    def scalar_one_or_none(self):
        if len(self.rows) > 1: raise AssertionError("Ambiguous fake scalar")
        return self.rows[0] if self.rows else None
    def scalars(self): return self
    def all(self): return list(self.rows)


def matches(row, condition):
    if isinstance(condition, BooleanClauseList):
        return all(matches(row, child) for child in condition.clauses)
    if not isinstance(condition, BinaryExpression):
        raise AssertionError("Unsupported fake predicate")
    value = getattr(row, condition.left.name)
    right = getattr(condition.right, "value", None)
    if condition.operator is operators.is_:
        return value is (True if isinstance(condition.right, sa.sql.elements.True_) else False)
    if condition.operator is operators.eq: return value == right
    if condition.operator is operators.in_op: return value in right
    raise AssertionError("Unsupported fake operator")


class FakeStorage:
    def __init__(self):
        self.rows = {User: [User(id=2, is_active=True), User(id=3, is_active=True)], Provider: [], Model: []}
        self.commits = self.rollbacks = 0
        self.locked = []

    @asynccontextmanager
    async def _get_owned_session(self):
        rows_before = {cls: list(rows) for cls, rows in self.rows.items()}
        values_before = [(row, {key: value for key, value in vars(row).items() if key != "_sa_instance_state"})
                         for rows in self.rows.values() for row in rows]
        try:
            yield self
            self.commits += 1
        except Exception:
            self.rows = rows_before
            for row, values in values_before:
                for key, value in values.items(): setattr(row, key, value)
            self.rollbacks += 1
            raise

    async def execute(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        rows = [row for row in self.rows[entity] if all(matches(row, c) for c in statement._where_criteria)]
        if statement._for_update_arg is not None: self.locked.append(entity)
        return Result(sorted(rows, key=lambda row: row.id))

    def add(self, row): self.rows[type(row)].append(row)

    async def flush(self):
        for cls, rows in self.rows.items():
            for index, row in enumerate(rows, 1):
                if row.id is None: row.id = index
                if cls is Provider:
                    for key, value in {"api_format":"openai", "is_active":True}.items():
                        if getattr(row, key) is None: setattr(row, key, value)
                if cls is Model:
                    for key, value in {"model_type":"text", "is_active":True, "is_default":False,
                                       "max_tokens":4096, "default_temperature":0.3, "pricing_token_unit":1000000,
                                       "input_cost_per_1k":0.0, "output_cost_per_1k":0.0}.items():
                        if getattr(row, key) is None: setattr(row, key, value)


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.actor = NS(id=2, is_active=True)
        self.store = FakeStorage()
        Provider.objects = Model.objects = self.store
        self.catalog = load("isolated.personal_catalog", ROOT / "app/services/ai/personal_catalog.py", {
            "app.core.permissions": NS(get_current_user=lambda: self.actor),
            "app.models": NS(LLMModel=Model, LLMProvider=Provider, Tenant=Tenant, User=User),
            "app.utils.crypto": NS(encrypt_secret=lambda key: "fake-encrypted:" + key),
        })

    async def create(self, *, models=None, uid="test"):
        return await self.catalog.save_provider_bundle(user_id=2,
            provider={"uid":uid, "name":"Display name", "base_url":"https://example.invalid/v1"},
            models=[] if models is None else models)

    async def test_provider_without_models_is_committed(self):
        result = await self.create()
        self.assertEqual(result["owner_user_id"], 2)
        self.assertEqual(result["models"], [])
        self.assertEqual(self.store.commits, 1)
        self.assertIs(self.store.locked[0], User)

    async def test_sparse_edit_keeps_provider_key_and_model_tariffs(self):
        result = await self.create(models=[{"name":"One", "model_id":"api-one", "input_cost_per_1k":3.5}])
        provider = self.store.rows[Provider][0]
        provider.encrypted_api_key = "fake-encrypted:sensitive-marker"
        provider.api_format = "anthropic"
        model = self.store.rows[Model][0]
        updated = await self.catalog.save_provider_bundle(user_id=2,
            provider={"id":result["id"], "name":"New display", "uid":"new-uid"},
            models=[{"id":model.id, "name":"Renamed", "model_id":"new-api-id"}])
        self.assertEqual(provider.uid, "new-uid")
        self.assertEqual(provider.api_format, "anthropic")
        self.assertEqual(model.input_cost_per_1k, 3.5)
        self.assertTrue(updated["has_api_key"])
        self.assertNotIn("sensitive-marker", repr(updated))
        self.assertNotIn("encrypted_api_key", updated)
        self.assertIsNone(updated["auth_header"])

    async def test_empty_key_does_not_erase_existing_key(self):
        await self.create()
        provider = self.store.rows[Provider][0]
        provider.encrypted_api_key = "fake-encrypted:keep"
        await self.catalog.save_provider_bundle(user_id=2, provider={"id":provider.id, "api_key":""}, models=[])
        self.assertEqual(provider.encrypted_api_key, "fake-encrypted:keep")

    async def test_foreign_provider_is_refused(self):
        await self.create()
        self.actor.id = 3
        with self.assertRaises(PermissionError):
            await self.catalog.save_provider_bundle(user_id=3, provider={"id":1, "name":"attack"}, models=[])
        self.assertEqual(self.store.rows[Provider][0].name, "Display name")

    async def test_foreign_model_rolls_back_prior_provider_edit(self):
        await self.create()
        with self.assertRaises(PermissionError):
            await self.catalog.save_provider_bundle(user_id=2, provider={"id":1, "name":"must rollback"},
                                                    models=[{"id":999, "name":"missing"}])
        self.assertEqual(self.store.rows[Provider][0].name, "Display name")
        self.assertEqual(self.store.rollbacks, 1)

    async def test_provider_cannot_take_model_from_another_provider(self):
        await self.create(models=[{"name":"One", "model_id":"one"}])
        second = await self.create(uid="second")
        with self.assertRaises(PermissionError):
            await self.catalog.save_provider_bundle(user_id=2, provider={"id":second["id"]}, models=[{"id":1,"name":"move"}])

    async def test_duplicate_model_ids_refused_before_writes(self):
        with self.assertRaises(ValueError):
            await self.catalog.save_provider_bundle(user_id=2, provider={"id":1}, models=[{"id":1},{"id":1}])
        self.assertEqual(self.store.commits, 0)

    async def test_conflicting_defaults_roll_back_new_provider_and_models(self):
        with self.assertRaises(ValueError):
            await self.create(models=[{"name":"One","model_id":"one","is_default":True},
                                      {"name":"Two","model_id":"two","is_default":True}])
        self.assertEqual(self.store.rows[Provider], [])
        self.assertEqual(self.store.rows[Model], [])

    async def test_defaults_change_only_overlapping_models_of_same_owner(self):
        await self.create(models=[{"name":"One","model_id":"one","is_default":True},
                                  {"name":"Two","model_id":"two"}])
        first, second = self.store.rows[Model]
        foreign_provider = Provider(id=20, owner_user_id=3, uid="foreign", name="Foreign", is_active=True)
        foreign = Model(id=20, owner_user_id=3, provider=foreign_provider, provider_id=20,
                        name="Other", model_id="other", model_type="text", is_default=True, is_active=True)
        self.store.rows[Provider].append(foreign_provider)
        self.store.rows[Model].append(foreign)
        await self.catalog.set_default_model(user_id=2, model_id=second.id)
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)
        self.assertTrue(foreign.is_default)

    async def test_actor_id_cannot_be_supplied_for_another_user(self):
        with self.assertRaises(PermissionError): await self.catalog.catalog_metadata(user_id=3)
        self.actor.is_active = False
        with self.assertRaises(PermissionError): await self.create()

    def test_nonfinite_negative_temperature_and_tariffs_rejected(self):
        for key in ("default_temperature", "input_cost_per_1k", "cache_read_cost_per_1m"):
            for value in ("NaN", "Infinity", "-1"):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.catalog.validate_model_values({key:value})

    def test_bad_provider_uid_url_header_and_owner_input_rejected(self):
        for patch in ({"uid":"Upper Case"}, {"base_url":"http://example.invalid"},
                      {"base_url":"https://user:pass@example.invalid"}, {"auth_header":"bad\nheader"},
                      {"owner_user_id":3}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.catalog.validate_provider_values(patch)

    async def test_eligibility_includes_owner_provider_activity_and_tier(self):
        await self.create(models=[{"name":"One","model_id":"one"}])
        model = self.store.rows[Model][0]
        predicate = self.catalog.eligible_chat_model
        self.assertTrue(predicate(model,user_id=2,allowed_types={"text"}))
        self.assertFalse(predicate(model,user_id=3))
        self.assertFalse(predicate(model,user_id=2,allowed_types=set()))
        model.provider.is_active=False
        self.assertFalse(predicate(model,user_id=2))

    async def test_media_eligibility_uses_the_same_owner_gate(self):
        """Image, video and audio resolve through the chat ownership predicate.

        A media stage decrypts the same provider key, so an owner-agnostic
        image/video/audio candidate is a cross-user key use, not a fallback.
        """
        await self.create(models=[{"name":"Vision","model_id":"vision","model_type":"image"},
                                  {"name":"Clip","model_id":"clip","model_type":"video"}])
        predicate = self.catalog.eligible_owned_model
        vision, clip = self.store.rows[Model]
        self.assertTrue(predicate(vision, user_id=2, capabilities={"image"}))
        self.assertTrue(predicate(clip, user_id=2, capabilities={"video"}))
        self.assertFalse(predicate(vision, user_id=2, capabilities={"audio"}))
        self.assertFalse(predicate(vision, user_id=2, capabilities={"text"}))
        self.assertFalse(predicate(vision, user_id=3, capabilities={"image"}))
        self.assertFalse(predicate(clip, user_id=2, capabilities={"video"}, allowed_types={"text","image"}))
        self.assertTrue(predicate(clip, user_id=2, capabilities={"video"}, allowed_types={"text","video"}))
        vision.provider.is_active = False
        self.assertFalse(predicate(vision, user_id=2, capabilities={"image"}))

    def test_real_model_mapping_enforces_owner_provider_composite(self):
        fks = [fk for fk in Model.__table__.foreign_key_constraints if fk.name=="fk_llm_model_provider_owner"]
        self.assertEqual(len(fks), 1)
        self.assertEqual(list(fks[0].column_keys), ["provider_id","owner_user_id"])
        self.assertEqual(len(Model.__mapper__.relationships["provider"].local_columns), 2)
        self.assertFalse(Model.__table__.c.owner_user_id.nullable)
        self.assertFalse(Provider.__table__.c.owner_user_id.nullable)


if __name__ == "__main__": unittest.main()
