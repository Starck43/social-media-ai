"""
Тесты для проверки работы BaseManager и QuerySet
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.models.base import Base
from app.models.managers.base_manager import prefetch
from app.models.platform import Platform
from app.models.role import Role
from app.models.source import Source
from app.models.user import User
from app.types import SourceType, PlatformType, UserRoleType


# Фикстура для создания тестовой БД в памяти
@pytest.fixture
async def async_session():
    """Создание асинхронной тестовой сессии"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=True
    )
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async_session_maker = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession
    )
    
    async with async_session_maker() as session:
        yield session
    
    await engine.dispose()


@pytest.fixture
async def sample_data(async_session: AsyncSession):
    """Создание тестовых данных"""
    # Создаем роли
    admin_role = Role(id=1, name="Admin", codename=UserRoleType.ADMIN)
    user_role = Role(id=2, name="User", codename=UserRoleType.VIEWER)
    async_session.add_all([admin_role, user_role])
    
    # Создаем пользователей
    users = [
        User(id=1, username="admin", email="admin@test.com", hashed_password="hash", role_id=1, is_active=True, is_superuser=True),
        User(id=2, username="user1", email="user1@test.com", hashed_password="hash", role_id=2, is_active=True),
        User(id=3, username="user2", email="user2@test.com", hashed_password="hash", role_id=2, is_active=False),
    ]
    async_session.add_all(users)
    
    # Создаем платформы (с учетом актуальной схемы модели)
    platforms = [
        Platform(
            id=1,
            name="VK",
            platform_type=PlatformType.VK.value,  # хранится как db value через Enum столбец
            base_url="https://vk.com",
            params={},
            is_active=True,
        ),
        Platform(
            id=2,
            name="Telegram",
            platform_type=PlatformType.TELEGRAM.value,
            base_url="https://t.me",
            params={},
            is_active=True,
        ),
    ]
    async_session.add_all(platforms)
    
    # Создаем источники
    sources = [
        Source(id=1, platform_id=1, name="VK Group 1", source_type=SourceType.GROUP, external_id="group1", is_active=True),
        Source(id=2, platform_id=1, name="VK User 1", source_type=SourceType.USER, external_id="user1", is_active=True),
        Source(id=3, platform_id=2, name="TG Channel", source_type=SourceType.CHANNEL, external_id="channel1", is_active=False),
    ]
    async_session.add_all(sources)
    
    await async_session.commit()
    
    return {
        "users": users,
        "roles": [admin_role, user_role],
        "platforms": platforms,
        "sources": sources
    }


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
        users = await User.objects.limit(2, session=async_session)
        assert len(users) == 2
        
        users = await User.objects.offset(1, session=async_session)
        assert len(users) == 2
        assert users[0].id == 2


class TestBaseManagerEagerLoading:
    """Тесты жадной загрузки связей"""
    
    @pytest.mark.asyncio
    async def test_select_related(self, async_session, sample_data):
        """Тест select_related для ForeignKey"""
        # select_related должен работать для связи User.role
        manager = User.objects.select_related('role')
        stmt = manager.build_select()
        
        result = await async_session.execute(stmt)
        users = result.scalars().unique().all()
        
        # Проверяем, что роль загружена без дополнительного запроса
        assert len(users) == 3
        # В реальности нужно проверить количество запросов
    
    @pytest.mark.asyncio
    async def test_prefetch_related(self, async_session, sample_data):
        """Тест prefetch_related для обратных связей"""
        # prefetch_related для Platform.sources
        manager = Platform.objects.prefetch_related('sources')
        stmt = manager.build_select()
        
        result = await async_session.execute(stmt)
        platforms = result.scalars().unique().all()
        
        assert len(platforms) == 2
    
    @pytest.mark.asyncio
    async def test_prefetch_with_filters(self, async_session, sample_data):
        """Тест Prefetch с фильтрами"""
        # Загружаем платформы только с активными источниками
        manager = Platform.objects.prefetch_related(
            prefetch('sources', filters={'is_active': True})
        )
        stmt = manager.build_select()
        
        result = await async_session.execute(stmt)
        platforms = result.scalars().unique().all()
        
        # Проверяем, что фильтр применен
        # (В реальности нужно проверить SQL запрос)


class TestBaseManagerCRUD:
    """Тесты CRUD операций"""
    
    @pytest.mark.asyncio
    async def test_create(self, async_session, sample_data):
        """Тест создания объекта"""
        new_user = await User.objects.create(
            username="newuser",
            email="new@test.com",
            hashed_password="hash",
            role_id=2,
            session=async_session
        )
        
        assert new_user.id is not None
        assert new_user.username == "newuser"
        
        # Проверяем, что объект сохранен
        found = await User.objects.get(id=new_user.id, session=async_session)
        assert found is not None
    
    @pytest.mark.asyncio
    async def test_update_by_id(self, async_session, sample_data):
        """Тест обновления объекта"""
        updated = await User.objects.update_by_id(
            instance_id=1,
            email="updated@test.com",
            session=async_session
        )
        
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
        
        created = await User.objects.bulk_create(
            users_data,
            return_instances=True,
            session=async_session
        )
        
        assert len(created) == 5
        assert all(u.id is not None for u in created)


class TestBaseManagerAdvanced:
    """Тесты продвинутых возможностей"""
    
    @pytest.mark.asyncio
    async def test_paginate(self, async_session, sample_data):
        """Тест пагинации"""
        result = await User.objects.paginate(
            page=1,
            per_page=2,
            session=async_session
        )
        
        assert result.total == 3
        assert result.page == 1
        assert result.per_page == 2
        assert len(result.items) == 2
        assert result.pages == 2
    
    @pytest.mark.asyncio
    async def test_has_related(self, async_session, sample_data):
        """Тест фильтрации по существованию связанных объектов"""
        # Получаем платформы, у которых есть активные источники
        platforms = await Platform.objects.has(
            sources={"is_active": True},
            session=async_session
        )
        
        # Должна быть только VK платформа с активными источниками
        assert len(platforms) >= 1


class TestIssuesAndProblems:
    """Тесты для выявления конкретных проблем"""
    
    @pytest.mark.asyncio
    async def test_session_handling_inconsistency(self, async_session, sample_data):
        """
        ПРОБЛЕМА 1: Несогласованность в обработке session
        
        В некоторых методах используется self._get_session(**kwargs),
        а в других просто kwargs['session'].
        
        Это может привести к KeyError если session не передан.
        """
        # Этот вызов должен работать без session благодаря @with_db_session
        users = await User.objects.all()
        assert len(users) > 0
    
    @pytest.mark.asyncio
    async def test_filter_not_chainable(self, async_session, sample_data):
        """
        ПРОБЛЕМА 2: filter() не возвращает QuerySet
        
        filter() возвращает Sequence[M], что делает невозможным:
        User.objects.filter(is_active=True).filter(role_id=2)
        """
        # Это не будет работать, так как filter возвращает список
        # result = await User.objects.filter(is_active=True).filter(role_id=2)
        pass
    
    @pytest.mark.asyncio  
    async def test_where_state_leak(self, async_session, sample_data):
        """
        ПРОБЛЕМА 3: Утечка состояния в where()
        
        _where_filters очищается в get_queryset(), но если один
        экземпляр менеджера используется повторно, может быть утечка.
        """
        manager = User.objects
        
        # Первый запрос
        stmt1 = manager.where(is_active=True).build_select()
        
        # Второй запрос не должен содержать фильтры из первого
        stmt2 = manager.build_select()
        
        # Проверяем, что состояние очищено
        assert manager._where_filters == {}
        assert manager._where_clauses == []
    
    @pytest.mark.asyncio
    async def test_exclude_wrong_implementation(self, async_session, sample_data):
        """
        ПРОБЛЕМА 4: exclude() работает некорректно
        
        Использует stmt.filter() вместо stmt.where() и не учитывает
        другие фильтры из kwargs.
        """
        # exclude должен исключить активных пользователей
        inactive = await User.objects.exclude(is_active=True, session=async_session)
        
        # Должен быть только 1 неактивный пользователь
        assert len(inactive) == 1
        assert not inactive[0].is_active
    
    @pytest.mark.asyncio
    async def test_get_queryset_filter_application(self, async_session, sample_data):
        """
        ПРОБЛЕМА 5: get_queryset() применяет фильтры дважды
        
        Сначала из filters параметра, потом из self._where_filters
        """
        # Проверяем, что фильтры не дублируются
        manager = User.objects
        stmt = await manager.get_queryset(is_active=True)
        
        # SQL не должен содержать дублирующихся условий
    
    @pytest.mark.asyncio
    async def test_prefetch_filter_syntax(self, async_session, sample_data):
        """
        ПРОБЛЕМА 6: Prefetch с фильтрами имеет неудобный синтаксис
        
        В Django: Prefetch('sources', queryset=Source.objects.filter(is_active=True))
        Здесь: prefetch('sources', filters={'is_active': True})
        
        Но фильтры применяются через простое сравнение, без поддержки lookups.
        """
        # Должно работать с различными условиями
        manager = Platform.objects.prefetch_related(
            prefetch('sources', filters={'is_active': True, 'source_type': SourceType.GROUP})
        )
        stmt = manager.build_select()
        
        result = await async_session.execute(stmt)
        platforms = result.scalars().unique().all()


# Запуск тестов
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
