"""
Тесты для проверки работы BaseManager и QuerySet
"""

import pytest
from sqlalchemy import CheckConstraint, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.models.base import Base
from app.models.managers.base_manager import prefetch
from app.models.platform import Platform
from app.models.role import Role
from app.models.source import Source
from app.models.user import User
from app.types import PlatformType, SourceType, UserRoleType


# Фикстура для создания тестовой БД в памяти
@pytest.fixture
async def async_session():
    """Создание асинхронной тестовой сессии.

    Метаданные проекта спроектированы под PostgreSQL: MetaData закреплена за
    схемой из настроек, а в моделях есть pg-оператор ``~`` в CHECK и
    касты ``::json`` в server_default. Для SQLite поэтому: (1) приаттачиваем
    схему отдельной in-memory БД — под тем именем, которое задаёт
    ``settings.DB_SCHEMA`` (conftest уже перенаправил его на схему тестов),
    (2) на время ``create_all`` снимаем несовместимые конструкции и ставим их
    обратно в ``finally`` (общий metadata процесса не должен меняться).
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _attach_schema(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute(f"ATTACH DATABASE ':memory:' AS {settings.DB_SCHEMA}")
        cursor.close()

    stripped = []
    patched_defaults = []
    for table in Base.metadata.tables.values():
        for constraint in list(table.constraints):
            sqltext = getattr(constraint, "sqltext", None)
            if isinstance(constraint, CheckConstraint) and sqltext is not None and "~" in sqltext.text:
                table.constraints.discard(constraint)
                stripped.append((table, constraint))
        for column in table.columns:
            default = column.server_default
            rendered = getattr(default, "arg", None)
            rendered = getattr(rendered, "text", rendered)
            if isinstance(rendered, str) and "::" in rendered:
                patched_defaults.append((column, default))
                column.server_default = text(rendered.split("::", 1)[0])

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        for table, constraint in stripped:
            table.constraints.add(constraint)
        for column, default in patched_defaults:
            column.server_default = default

    async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with async_session_maker() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def sample_data(async_session: AsyncSession):
    """Создание тестовых данных"""
    # Создаем роли
    admin_role = Role(id=1, name="Admin", codename=UserRoleType.ADMIN.name)
    user_role = Role(id=2, name="User", codename=UserRoleType.VIEWER.name)
    async_session.add_all([admin_role, user_role])

    # Создаем пользователей
    users = [
        User(
            id=1,
            username="admin",
            email="admin@test.com",
            hashed_password="hash",
            role_id=1,
            is_active=True,
            is_superuser=True,
        ),
        User(id=2, username="user1", email="user1@test.com", hashed_password="hash", role_id=2, is_active=True),
        User(id=3, username="user2", email="user2@test.com", hashed_password="hash", role_id=2, is_active=False),
    ]
    async_session.add_all(users)

    # Создаем платформы (с учетом актуальной схемы модели)
    platforms = [
        Platform(
            id=1,
            name="VK",
            platform_type=PlatformType.VK.db_value,  # хранится как db value через Enum столбец
            base_url="https://vk.com",
            params={},
            is_active=True,
        ),
        Platform(
            id=2,
            name="Telegram",
            platform_type=PlatformType.TELEGRAM.db_value,
            base_url="https://t.me",
            params={},
            is_active=True,
        ),
    ]
    async_session.add_all(platforms)

    # Создаем источники (tenant_id обязателен с внедрением workspace'ов;
    # в in-memory SQLite внешние ключи по умолчанию не проверяются)
    sources = [
        Source(
            id=1,
            platform_id=1,
            tenant_id=1,
            name="VK Group 1",
            source_type=SourceType.GROUP,
            external_id="group1",
            is_active=True,
        ),
        Source(
            id=2,
            platform_id=1,
            tenant_id=1,
            name="VK User 1",
            source_type=SourceType.USER,
            external_id="user1",
            is_active=True,
        ),
        Source(
            id=3,
            platform_id=2,
            tenant_id=1,
            name="TG Channel",
            source_type=SourceType.CHANNEL,
            external_id="channel1",
            is_active=False,
        ),
    ]
    async_session.add_all(sources)

    await async_session.commit()

    return {"users": users, "roles": [admin_role, user_role], "platforms": platforms, "sources": sources}


class TestBaseManagerBasicQueries:
    """Тесты базовых методов запросов"""

    @pytest.mark.asyncio
    async def test_all(self, async_session, sample_data):
        """Тест метода all()"""
        users = await User.objects.all(session=async_session)
        assert len(users) == 3
        assert all(isinstance(u, User) for u in users)

    @pytest.mark.asyncio
    async def test_get_by_id(self, async_session, sample_data):
        """Тест получения объекта по ID"""
        user = await User.objects.get(id=1, session=async_session)
        assert user is not None
        assert user.username == "admin"

    @pytest.mark.asyncio
    async def test_get_not_found(self, async_session, sample_data):
        """Тест получения несуществующего объекта"""
        user = await User.objects.get(id=999, session=async_session)
        assert user is None

    @pytest.mark.asyncio
    async def test_filter_simple(self, async_session, sample_data):
        """Тест простой фильтрации"""
        active_users = await User.objects.filter(is_active=True, session=async_session)
        assert len(active_users) == 2
        assert all(u.is_active for u in active_users)

    @pytest.mark.asyncio
    async def test_filter_with_lookups(self, async_session, sample_data):
        """Тест фильтрации с лукапами"""
        # Test __in lookup
        users = await User.objects.filter(id__in=[1, 2], session=async_session)
        assert len(users) == 2

        # Test __gt lookup
        users = await User.objects.filter(id__gt=1, session=async_session)
        assert len(users) == 2
        assert all(u.id > 1 for u in users)

        # Test __contains lookup
        users = await User.objects.filter(email__contains="test.com", session=async_session)
        assert len(users) == 3

    @pytest.mark.asyncio
    async def test_count(self, async_session, sample_data):
        """Тест подсчета записей"""
        total = await User.objects.count(session=async_session)
        assert total == 3

        active_count = await User.objects.count(is_active=True, session=async_session)
        assert active_count == 2

    @pytest.mark.asyncio
    async def test_exists(self, async_session, sample_data):
        """Тест проверки существования"""
        exists = await User.objects.exists(username="admin", session=async_session)
        assert exists is True

        not_exists = await User.objects.exists(username="nonexistent", session=async_session)
        assert not_exists is False


class TestBaseManagerChaining:
    """Тесты цепочек запросов"""

    @pytest.mark.asyncio
    async def test_queryset_filter_chaining(self, async_session, sample_data):
        """Тест цепочки filter() через QuerySet"""
        # ПРОБЛЕМА: метод filter() возвращает Sequence[M], а не QuerySet
        # Нужно исправить, чтобы можно было строить цепочки
        pass

    @pytest.mark.asyncio
    async def test_order_by(self, async_session, sample_data):
        """Тест сортировки"""
        users = await User.objects.order_by(User.id.desc(), session=async_session)
        assert len(users) == 3
        assert users[0].id == 3
        assert users[1].id == 2
        assert users[2].id == 1

    @pytest.mark.asyncio
    async def test_limit_offset(self, async_session, sample_data):
        """Тест лимита и оффсета"""
        users = await User.objects.all(session=async_session).order_by(User.id).limit(2)
        assert len(users) == 2
        assert [u.id for u in users] == [1, 2]

        users = await User.objects.all(session=async_session).order_by(User.id).offset(1)
        assert len(users) == 2
        assert users[0].id == 2


class TestBaseManagerEagerLoading:
    """Тесты жадной загрузки связей"""

    @pytest.mark.asyncio
    async def test_select_related(self, async_session, sample_data):
        """Тест select_related для ForeignKey"""
        # select_related работает для связи User.role: QuerySet выполняется
        # через await, роль загружается джойном без дополнительного запроса
        users = await User.objects.select_related("role", session=async_session)

        assert len(users) == 3
        assert all(user.role is not None for user in users)

    @pytest.mark.asyncio
    async def test_prefetch_related(self, async_session, sample_data):
        """Тест prefetch_related для обратных связей"""
        # prefetch_related для Platform.sources
        platforms = await Platform.objects.prefetch_related("sources", session=async_session)

        assert len(platforms) == 2

    @pytest.mark.asyncio
    async def test_prefetch_with_filters(self, async_session, sample_data):
        """Тест Prefetch с фильтрами"""
        # Загружаем платформы только с активными источниками
        platforms = await Platform.objects.prefetch_related(
            prefetch("sources", filters={"is_active": True}),
            session=async_session,
        )

        # Проверяем, что фильтр применен
        # (В реальности нужно проверить SQL запрос)
        assert len(platforms) >= 1


class TestBaseManagerCRUD:
    """Тесты CRUD операций"""

    @pytest.mark.asyncio
    async def test_create(self, async_session, sample_data):
        """Тест создания объекта"""
        new_user = await User.objects.create(
            username="newuser", email="new@test.com", hashed_password="hash", role_id=2, session=async_session
        )

        assert new_user.id is not None
        assert new_user.username == "newuser"

        # Проверяем, что объект сохранен
        found = await User.objects.get(id=new_user.id, session=async_session)
        assert found is not None

    @pytest.mark.asyncio
    async def test_update_by_id(self, async_session, sample_data):
        """Тест обновления объекта"""
        updated = await User.objects.update_by_id(instance_id=1, email="updated@test.com", session=async_session)

        assert updated is not None
        assert updated.email == "updated@test.com"

    @pytest.mark.asyncio
    async def test_delete_by_id(self, async_session, sample_data):
        """Тест удаления объекта"""
        success = await User.objects.delete_by_id(instance_id=3, session=async_session)
        assert success is True

        # Проверяем, что объект удален
        deleted = await User.objects.get(id=3, session=async_session)
        assert deleted is None

    @pytest.mark.asyncio
    async def test_bulk_create(self, async_session, sample_data):
        """Тест массового создания объектов"""
        users_data = [
            {"username": f"bulk{i}", "email": f"bulk{i}@test.com", "hashed_password": "hash", "role_id": 2}
            for i in range(5)
        ]

        created = await User.objects.bulk_create(users_data, return_instances=True, session=async_session)

        assert len(created) == 5
        assert all(u.id is not None for u in created)


class TestBaseManagerAdvanced:
    """Тесты продвинутых возможностей"""

    @pytest.mark.asyncio
    async def test_paginate(self, async_session, sample_data):
        """Тест пагинации"""
        result = await User.objects.paginate(page=1, per_page=2, session=async_session)

        assert result.total == 3
        assert result.page == 1
        assert result.per_page == 2
        assert len(result.items) == 2
        assert result.pages == 2

    @pytest.mark.asyncio
    async def test_has_related(self, async_session, sample_data):
        """Тест фильтрации по существованию связанных объектов"""
        # Получаем платформы, у которых есть активные источники
        # (метода has() в менеджере нет — используем relationship-подзапрос)
        platforms = await Platform.objects.filter(
            Platform.sources.any(is_active=True),
            session=async_session,
        )

        # Только платформа VK имеет активные источники
        assert len(platforms) == 1
        assert platforms[0].id == 1


def _loader_options(queryset):
    """The loader options of the statement a queryset builds (no DB needed)."""
    return queryset._build_statement_sync()._with_options


class TestRelationPaths:
    """`select_related` and `prefetch_related` must agree, and must not lie.

    The two neighbours nested differently — Django's `"source__platform"` for
    eager loading, SQLAlchemy's `"role.permissions"` for prefetching — and each
    silently ignored the other's spelling. A prefetch that matched nothing was
    accepted happily, so the caller only met the problem later, as a
    `DetachedInstanceError` from a lazy load, nowhere near the call that lied.
    """

    def test_prefetch_accepts_both_spellings(self):
        assert len(_loader_options(User.objects.prefetch_related("role.permissions"))) == 1
        assert len(_loader_options(User.objects.prefetch_related("role__permissions"))) == 1

    def test_eager_load_accepts_both_spellings(self):
        for path in ("role.permissions", "role__permissions"):
            assert len(_loader_options(User.objects.select_related(path))) == 1, path

    def test_both_spellings_produce_the_same_option(self):
        dotted = _loader_options(User.objects.prefetch_related("role.permissions"))[0]
        django = _loader_options(User.objects.prefetch_related("role__permissions"))[0]
        assert str(dotted) == str(django)

    def test_an_unknown_relation_raises_instead_of_loading_nothing(self):
        for queryset in (
            User.objects.prefetch_related("nope"),
            User.objects.prefetch_related("role.permisions"),
            User.objects.select_related("role__permisions"),
        ):
            with pytest.raises(ValueError, match="no such relation"):
                _loader_options(queryset)

    def test_a_column_is_not_a_relation(self):
        """`prefetch_related("username")` is a mistake, not a no-op."""
        with pytest.raises(ValueError, match="no such relation"):
            _loader_options(User.objects.prefetch_related("username"))


# Запуск тестов
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
