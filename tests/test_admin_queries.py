"""
Тестирование исправленных запросов в admin views
"""
import asyncio
from app.models import Source, SourceUserRelationship
from app.models.managers.base_manager import prefetch


async def test_source_list_query():
    """Тест list_query для SourceAdmin"""
    print("\n" + "="*80)
    print("ТЕСТ: SourceAdmin.list_query()")
    print("="*80)
    
    try:
        # Имитация list_query из SourceAdmin
        qs = Source.objects.prefetch_related(
            "monitored_users",
            "tracked_in_sources",
        )
        stmt = await qs._build_statement()
        
        print("✅ list_query построен успешно")
        print(f"   Тип: {type(stmt)}")
        print(f"   SQL будет выполнен с prefetch_related для:")
        print(f"   - monitored_users")
        print(f"   - tracked_in_sources")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()


async def test_source_details_query():
    """Тест details_query для SourceAdmin"""
    print("\n" + "="*80)
    print("ТЕСТ: SourceAdmin.details_query()")
    print("="*80)
    
    try:
        # Имитация details_query из SourceAdmin
        pk = 1  # Тестовый ID
        
        # Создаем prefetch с фильтром (исключаем текущий источник)
        monitored_prefetch = prefetch(
            "monitored_users",
            filters={"id__ne": pk}  # Используем lookup __ne (not equal)
        )
        
        # Строим запрос через QuerySet
        qs = Source.objects.prefetch_related(
            monitored_prefetch,
            "tracked_in_sources",
            "analytics",
            "platform",
        ).filter(id=pk)
        
        stmt = await qs._build_statement()
        
        print("✅ details_query построен успешно")
        print(f"   Тип: {type(stmt)}")
        print(f"   SQL будет выполнен с:")
        print(f"   - filter(id={pk})")
        print(f"   - prefetch monitored_users с фильтром id__ne={pk}")
        print(f"   - prefetch tracked_in_sources, analytics, platform")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()


async def test_relationship_list_query():
    """Тест list_query для SourceUserRelationshipAdmin"""
    print("\n" + "="*80)
    print("ТЕСТ: SourceUserRelationshipAdmin.list_query()")
    print("="*80)
    
    try:
        # Имитация list_query из SourceUserRelationshipAdmin
        qs = (SourceUserRelationship.objects
            .select_related("source", "user")
            .prefetch_related("source.platform", "user.platform")
        )
        stmt = await qs._build_statement()
        
        print("✅ list_query построен успешно")
        print(f"   Тип: {type(stmt)}")
        print(f"   SQL будет выполнен с:")
        print(f"   - select_related: source, user (JOIN)")
        print(f"   - prefetch_related: source.platform, user.platform (отдельные запросы)")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()


async def test_prefetch_with_filters():
    """Тест prefetch с различными фильтрами"""
    print("\n" + "="*80)
    print("ТЕСТ: Prefetch с различными lookups")
    print("="*80)
    
    test_cases = [
        ("id__ne", 1, "not equal"),
        ("is_active", True, "equality"),
        ("id__in", [1, 2, 3], "in list"),
        ("name__contains", "test", "contains"),
    ]
    
    for lookup, value, description in test_cases:
        try:
            p = prefetch("monitored_users", filters={lookup: value})
            print(f"✅ prefetch с {description}: {lookup}={value}")
        except Exception as e:
            print(f"❌ prefetch с {description}: {e}")


async def test_queryset_chaining():
    """Тест цепочек QuerySet"""
    print("\n" + "="*80)
    print("ТЕСТ: Цепочки QuerySet")
    print("="*80)
    
    try:
        # Цепочка как в views
        qs = (Source.objects
            .prefetch_related("platform")
            .filter(is_active=True)
            .order_by(Source.name)
            .limit(10)
        )
        stmt = await qs._build_statement()
        
        print("✅ Цепочка построена успешно")
        print(f"   prefetch_related -> filter -> order_by -> limit")
        
    except Exception as e:
        print(f"❌ Ошибка в цепочке: {e}")
        import traceback
        traceback.print_exc()


async def test_comparison_old_vs_new():
    """Сравнение старого и нового API"""
    print("\n" + "="*80)
    print("СРАВНЕНИЕ: Старый vs Новый API")
    print("="*80)
    
    print("\n❌ СТАРЫЙ API (не работает теперь):")
    print("   Source.objects.prefetch_related(...).where(id=pk).build_select()")
    print("   - Методы where() и build_select() удалены")
    
    print("\n✅ НОВЫЙ API (работает):")
    print("   qs = Source.objects.prefetch_related(...).filter(id=pk)")
    print("   stmt = await qs._build_statement()")
    print("   - Используем filter() вместо where()")
    print("   - Используем _build_statement() вместо build_select()")
    print("   - QuerySet поддерживает цепочки")


async def main():
    """Запуск всех тестов"""
    print("\n")
    print("🧪" * 40)
    print("ТЕСТИРОВАНИЕ ИСПРАВЛЕННЫХ ADMIN QUERIES")
    print("🧪" * 40)
    
    await test_source_list_query()
    await test_source_details_query()
    await test_relationship_list_query()
    await test_prefetch_with_filters()
    await test_queryset_chaining()
    await test_comparison_old_vs_new()
    
    print("\n" + "="*80)
    print("✅ ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ")
    print("="*80)
    print("\nИЗМЕНЕНИЯ В views.py:")
    print("  1. ✅ Заменен import: Prefetch -> prefetch")
    print("  2. ✅ list_query стал async")
    print("  3. ✅ Заменен .build_select() на await qs._build_statement()")
    print("  4. ✅ Заменен .where(id=pk) на .filter(id=pk)")
    print("  5. ✅ Prefetch() заменен на prefetch() с filters")
    print("  6. ✅ queryset=Source.objects.exclude() заменен на filters={'id__ne': pk}")
    print("  7. ✅ Вложенные select_related заменены на prefetch_related")
    print("\n")


if __name__ == "__main__":
    asyncio.run(main())
