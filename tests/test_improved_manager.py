"""
Демонстрация работы улучшенного BaseManager
Показывает оба варианта использования: ленивый и сразу
"""
import asyncio
from datetime import datetime, timedelta
from sqlalchemy import or_

from app.core.database import async_session_maker
from app.models.user import User
from app.models.source import Source
from app.models.platform import Platform
from app.models.managers.base_manager import prefetch


async def demo_immediate_execution():
    """
    Вариант 1: Сразу получить результат (как раньше)
    
    await напрямую - запрос выполняется немедленно
    """
    print("=" * 80)
    print("ВАРИАНТ 1: Немедленное выполнение (сразу список)")
    print("=" * 80)
    
    # Простой запрос - сразу результат
    print("\n1. Простой filter с await:")
    users = await User.objects.filter(is_active=True)
    print(f"   Результат: {type(users)} с {len(users)} элементами")
    
    # С несколькими условиями
    print("\n2. Filter с несколькими условиями:")
    users = await User.objects.filter(is_active=True, is_superuser=False)
    print(f"   Результат: {type(users)} с {len(users)} элементами")
    
    # С lookups
    print("\n3. Filter с lookups:")
    users = await User.objects.filter(
        email__endswith='@test.com',
        id__in=[1, 2, 3]
    )
    print(f"   Результат: {type(users)} с {len(users)} элементами")
    
    # exclude работает!
    print("\n4. Exclude (теперь работает!):")
    users = await User.objects.exclude(is_superuser=True)
    print(f"   Результат: {type(users)} с {len(users)} элементами")
    
    # count оптимизирован
    print("\n5. Count (оптимизированный):")
    count = await User.objects.filter(is_active=True).count()
    print(f"   Результат: {count}")
    
    # exists
    print("\n6. Exists:")
    exists = await User.objects.exists(username='admin')
    print(f"   Результат: {exists}")


async def demo_lazy_execution():
    """
    Вариант 2: Ленивое построение запроса (новая возможность)
    
    Без await - возвращает QuerySet для построения сложных запросов
    """
    print("\n" + "=" * 80)
    print("ВАРИАНТ 2: Ленивое выполнение (QuerySet с цепочками)")
    print("=" * 80)
    
    # Шаг 1: Создаем QuerySet (запрос НЕ выполняется)
    print("\n1. Создание базового QuerySet:")
    qs = User.objects.filter(is_active=True)
    print(f"   Тип: {type(qs)}")
    print(f"   SQL еще НЕ выполнен!")
    
    # Шаг 2: Добавляем условия (запрос НЕ выполняется)
    print("\n2. Добавление фильтров (цепочка):")
    qs = qs.filter(is_superuser=False)
    print(f"   Тип: {type(qs)}")
    print(f"   SQL еще НЕ выполнен!")
    
    # Шаг 3: Добавляем сортировку (запрос НЕ выполняется)
    print("\n3. Добавление сортировки:")
    qs = qs.order_by(User.created_at.desc())
    print(f"   Тип: {type(qs)}")
    print(f"   SQL еще НЕ выполнен!")
    
    # Шаг 4: Добавляем лимит (запрос НЕ выполняется)
    print("\n4. Добавление лимита:")
    qs = qs.limit(10)
    print(f"   Тип: {type(qs)}")
    print(f"   SQL еще НЕ выполнен!")
    
    # Шаг 5: ВЫПОЛНЯЕМ запрос
    print("\n5. Выполнение запроса (await):")
    users = await qs
    print(f"   Тип результата: {type(users)}")
    print(f"   Количество: {len(users)}")
    print(f"   SQL ВЫПОЛНЕН!")


async def demo_dynamic_queries():
    """
    Вариант 3: Динамическое построение запросов
    
    Главное преимущество ленивого подхода
    """
    print("\n" + "=" * 80)
    print("ВАРИАНТ 3: Динамическое построение (как в Django)")
    print("=" * 80)
    
    # Симуляция параметров из API
    search_query = "admin"
    is_active_filter = True
    role_id_filter = None
    
    print("\n1. Построение запроса на основе условий:")
    print(f"   search_query = {search_query}")
    print(f"   is_active_filter = {is_active_filter}")
    print(f"   role_id_filter = {role_id_filter}")
    
    # Начинаем с базового QuerySet
    qs = User.objects.all()
    
    # Добавляем фильтры динамически
    if search_query:
        print(f"\n   Добавляем фильтр по поиску...")
        qs = qs.filter(
            or_(
                User.username.contains(search_query),
                User.email.contains(search_query)
            )
        )
    
    if is_active_filter is not None:
        print(f"   Добавляем фильтр is_active...")
        qs = qs.filter(is_active=is_active_filter)
    
    if role_id_filter:
        print(f"   Добавляем фильтр role_id...")
        qs = qs.filter(role_id=role_id_filter)
    
    # Выполняем
    print(f"\n   Выполняем запрос...")
    users = await qs
    print(f"   Найдено: {len(users)} пользователей")


async def demo_queryset_reuse():
    """
    Вариант 4: Переиспользование базовых QuerySet
    
    Как в Django - создаем базовый запрос и используем его для разных целей
    """
    print("\n" + "=" * 80)
    print("ВАРИАНТ 4: Переиспользование QuerySet")
    print("=" * 80)
    
    # Базовый QuerySet для активных пользователей
    print("\n1. Создаем базовый QuerySet:")
    active_users = User.objects.filter(is_active=True)
    print(f"   active_users = User.objects.filter(is_active=True)")
    
    # Используем его для разных запросов
    print("\n2. Создаем разные запросы на его основе:")
    
    print("\n   a) Администраторы:")
    admins = await active_users.filter(is_superuser=True)
    print(f"      Найдено: {len(admins)}")
    
    print("\n   b) Обычные пользователи:")
    regular_users = await active_users.filter(is_superuser=False)
    print(f"      Найдено: {len(regular_users)}")
    
    print("\n   c) Недавно созданные:")
    recent_date = datetime.now() - timedelta(days=30)
    recent = await active_users.filter(created_at__gte=recent_date)
    print(f"      Найдено: {len(recent)}")
    
    print("\n   Все запросы независимы и имеют свои фильтры!")


async def demo_complex_chains():
    """
    Вариант 5: Сложные цепочки
    
    Демонстрация всех возможностей вместе
    """
    print("\n" + "=" * 80)
    print("ВАРИАНТ 5: Сложные цепочки (всё вместе)")
    print("=" * 80)
    
    print("\n1. Цепочка с filter, exclude, order_by, limit:")
    users = await (User.objects
        .select_related('role')  # Eager loading
        .filter(is_active=True)
        .exclude(email__endswith='@spam.com')
        .filter(created_at__gte=datetime.now() - timedelta(days=90))
        .order_by(User.created_at.desc())
        .limit(5)
    )
    print(f"   Найдено: {len(users)} пользователей")
    
    print("\n2. Пагинация в цепочке:")
    result = await (User.objects
        .filter(is_active=True)
        .order_by(User.username)
        .paginate(page=1, per_page=10)
    )
    print(f"   Страница {result.page} из {result.pages}")
    print(f"   Всего записей: {result.total}")
    print(f"   На странице: {len(result.items)}")
    
    print("\n3. Count и exists без загрузки объектов:")
    count = await User.objects.filter(is_active=True).count()
    exists = await User.objects.filter(username='admin').exists()
    print(f"   Count: {count}")
    print(f"   Exists: {exists}")


async def demo_prefetch_with_lookups():
    """
    Вариант 6: Prefetch с lookups (новая возможность!)
    
    Теперь можно фильтровать связанные объекты при загрузке
    """
    print("\n" + "=" * 80)
    print("ВАРИАНТ 6: Prefetch с lookups")
    print("=" * 80)
    
    print("\n1. Загрузка платформ с активными источниками:")
    platforms = await Platform.objects.prefetch_related(
        prefetch('sources', filters={'is_active': True})
    ).all()
    print(f"   Загружено платформ: {len(platforms)}")
    
    print("\n2. Prefetch с несколькими lookups:")
    from app.types.models import SourceType
    platforms = await Platform.objects.prefetch_related(
        prefetch('sources', filters={
            'is_active': True,
            'source_type__in': [SourceType.GROUP, SourceType.CHANNEL],
            'name__contains': 'official'
        })
    ).all()
    print(f"   Загружено платформ: {len(platforms)}")
    print(f"   Источники отфильтрованы при загрузке!")


async def demo_comparison():
    """
    Сравнение: что изменилось
    """
    print("\n" + "=" * 80)
    print("ЧТО ИЗМЕНИЛОСЬ: Сравнение старого и нового")
    print("=" * 80)
    
    print("\n❌ БЫЛО (не работало):")
    print("   users = await User.objects.filter(is_active=True).filter(role_id=2)")
    print("   # AttributeError: 'list' object has no attribute 'filter'")
    
    print("\n✅ СТАЛО (работает):")
    users = await User.objects.filter(is_active=True).filter(role_id=2)
    print(f"   users = await User.objects.filter(is_active=True).filter(role_id=2)")
    print(f"   Результат: {type(users)} с {len(users)} элементами")
    
    print("\n" + "-" * 80)
    
    print("\n❌ БЫЛО (не работало):")
    print("   users = await User.objects.exclude(is_superuser=True)")
    print("   # AttributeError: 'Select' object has no attribute 'filter'")
    
    print("\n✅ СТАЛО (работает):")
    users = await User.objects.exclude(is_superuser=True)
    print(f"   users = await User.objects.exclude(is_superuser=True)")
    print(f"   Результат: {type(users)} с {len(users)} элементами")
    
    print("\n" + "-" * 80)
    
    print("\n❌ БЫЛО (не работало):")
    print("   prefetch('sources', filters={'id__in': [1, 2, 3]})")
    print("   # ValueError: Unsupported lookup")
    
    print("\n✅ СТАЛО (работает):")
    print("   prefetch('sources', filters={'id__in': [1, 2, 3], 'is_active': True})")
    print("   Все lookups поддерживаются!")


async def main():
    """Запуск всех демонстраций"""
    print("\n")
    print("🚀" * 40)
    print("ДЕМОНСТРАЦИЯ УЛУЧШЕННОГО BaseManager")
    print("🚀" * 40)
    
    try:
        await demo_immediate_execution()
        await demo_lazy_execution()
        await demo_dynamic_queries()
        await demo_queryset_reuse()
        await demo_complex_chains()
        await demo_prefetch_with_lookups()
        await demo_comparison()
        
        print("\n" + "=" * 80)
        print("✅ ВСЕ ДЕМОНСТРАЦИИ ЗАВЕРШЕНЫ УСПЕШНО!")
        print("=" * 80)
        print("\nТеперь вы можете использовать:")
        print("  • Цепочки фильтров: .filter().filter().exclude()")
        print("  • Ленивые запросы: qs = Model.objects.filter(...); await qs")
        print("  • Динамическое построение: if condition: qs = qs.filter(...)")
        print("  • Оптимизированные count/exists")
        print("  • Prefetch с lookups")
        print("  • Все исправленные баги")
        print("\n")
        
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
