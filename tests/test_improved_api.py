"""
Демонстрация улучшенного API:
1. Prefetch с queryset= (Django-style)
2. Публичный метод to_select() вместо _build_statement()
"""
import asyncio
from app.models import Source
from app.models.managers.base_manager import prefetch


async def test_prefetch_with_queryset():
    """Тест prefetch с queryset (Django-style)"""
    print("\n" + "="*80)
    print("ТЕСТ 1: Prefetch с queryset= (Django-style)")
    print("="*80)
    
    pk = 1
    
    # Вариант 1: С queryset (Django-style) ✅ РЕКОМЕНДУЕТСЯ
    print("\n✅ Вариант 1: prefetch с queryset=")
    monitored_prefetch = prefetch(
        "monitored_users",
        queryset=Source.objects.exclude(id=pk)  # Как в Django!
    )
    print(f"   prefetch('monitored_users', queryset=Source.objects.exclude(id={pk}))")
    print(f"   Гибко: можно использовать любые методы QuerySet!")
    
    # Вариант 2: С filters (простой способ)
    print("\n✅ Вариант 2: prefetch с filters=")
    monitored_prefetch_2 = prefetch(
        "monitored_users",
        filters={"id__ne": pk}  # Проще, но менее гибко
    )
    print(f"   prefetch('monitored_users', filters={{'id__ne': {pk}}})")
    print(f"   Проще для простых случаев")
    
    # Демонстрация гибкости queryset
    print("\n✅ Вариант 3: сложный queryset")
    complex_prefetch = prefetch(
        "monitored_users",
        queryset=(Source.objects
            .exclude(id=pk)
            .filter(is_active=True)
            .order_by(Source.name)
        )
    )
    print(f"   prefetch('monitored_users',")
    print(f"       queryset=Source.objects.exclude(id={pk})")
    print(f"           .filter(is_active=True)")
    print(f"           .order_by(Source.name)")
    print(f"   )")
    print(f"   Можно строить сложные цепочки!")


def test_to_select_public_api():
    """Тест публичного метода to_select()"""
    print("\n" + "="*80)
    print("ТЕСТ 2: Публичный метод .to_select()")
    print("="*80)
    
    # ❌ ПЛОХО: использование защищенного метода
    print("\n❌ БЫЛО (плохая практика):")
    print("   stmt = await qs._build_statement()  # Защищенный метод!")
    
    # ✅ ХОРОШО: публичный API
    print("\n✅ СТАЛО (правильно):")
    print("   stmt = qs.to_select()  # Публичный API!")
    
    # Пример использования
    print("\n📝 Пример в admin views:")
    print("""
    def list_query(self, request: Request) -> Select:
        return User.objects.filter(is_active=True).to_select()
    """)
    
    # Реальный пример
    print("\n🔍 Реальный запрос:")
    stmt = Source.objects.filter(is_active=True).to_select()
    print(f"   Тип результата: {type(stmt)}")
    print(f"   Это SQLAlchemy Select: {stmt.__class__.__name__}")


def test_admin_views_pattern():
    """Демонстрация правильного паттерна для admin views"""
    print("\n" + "="*80)
    print("ТЕСТ 3: Правильный паттерн для Admin Views")
    print("="*80)
    
    print("\n📋 SourceAdmin.list_query():")
    print("""
    def list_query(self, request: Request) -> Select:
        return Source.objects.prefetch_related(
            "monitored_users",
            "tracked_in_sources",
        ).to_select()
    """)
    
    print("\n📋 SourceAdmin.details_query() с Django-style prefetch:")
    print("""
    def details_query(self, request: Request) -> Select:
        pk = int(request.path_params["pk"])
        
        # Django-style!
        monitored_prefetch = prefetch(
            "monitored_users",
            queryset=Source.objects.exclude(id=pk)
        )
        
        return (Source.objects
            .prefetch_related(
                monitored_prefetch,
                "tracked_in_sources",
                "analytics",
                "platform",
            )
            .filter(id=pk)
            .to_select()
        )
    """)
    
    print("\n✅ Преимущества:")
    print("   1. Методы НЕ async (SQLAdmin требует sync)")
    print("   2. Используем публичный API to_select()")
    print("   3. Prefetch с queryset= как в Django")
    print("   4. Читаемые цепочки методов")


def test_comparison():
    """Сравнение старого и нового подхода"""
    print("\n" + "="*80)
    print("СРАВНЕНИЕ: Старый vs Новый подход")
    print("="*80)
    
    print("\n❌ СТАРЫЙ ПОДХОД:")
    print("""
    # 1. Защищенный метод
    async def details_query(self, request):
        qs = Source.objects.filter(...)
        return await qs._build_statement()  # ❌ Защищенный!
    
    # 2. Prefetch только с filters
    prefetch = Prefetch(
        "monitored_users",
        queryset=Source.objects.exclude(id=pk)  # ❌ Не работало!
    )
    """)
    
    print("\n✅ НОВЫЙ ПОДХОД:")
    print("""
    # 1. Публичный API
    def details_query(self, request):
        return Source.objects.filter(...).to_select()  # ✅ Публичный!
    
    # 2. Prefetch с queryset (Django-style)
    monitored = prefetch(
        "monitored_users",
        queryset=Source.objects.exclude(id=pk)  # ✅ Работает!
    )
    
    # 3. Или с filters (проще)
    monitored = prefetch(
        "monitored_users",
        filters={"id__ne": pk}  # ✅ Тоже работает!
    )
    """)


def test_flexibility_demo():
    """Демонстрация гибкости"""
    print("\n" + "="*80)
    print("ДЕМОНСТРАЦИЯ: Гибкость queryset в prefetch")
    print("="*80)
    
    examples = [
        ("Простое исключение", 
         "prefetch('users', queryset=Source.objects.exclude(id=1))"),
        
        ("С фильтром",
         "prefetch('users', queryset=Source.objects.filter(is_active=True))"),
        
        ("Сложная цепочка",
         """prefetch('users', queryset=
    Source.objects
        .filter(is_active=True)
        .exclude(source_type='GROUP')
        .order_by(Source.name)
        .limit(10)
)"""),
        
        ("С lookups",
         """prefetch('users', queryset=
    Source.objects.filter(
        id__in=[1,2,3],
        name__contains='test'
    )
)"""),
    ]
    
    for title, code in examples:
        print(f"\n✅ {title}:")
        print(f"   {code}")


def test_real_world_example():
    """Реальный пример использования"""
    print("\n" + "="*80)
    print("РЕАЛЬНЫЙ ПРИМЕР: Сложный запрос в admin")
    print("="*80)
    
    print("""
class SourceAdmin(BaseAdmin):
    def details_query(self, request: Request) -> Select:
        pk = int(request.path_params["pk"])
        user_id = request.query_params.get("user_id")
        
        # Базовый prefetch для отслеживаемых пользователей
        monitored_qs = Source.objects.exclude(id=pk)
        
        # Дополнительная фильтрация если передан user_id
        if user_id:
            monitored_qs = monitored_qs.filter(
                id__in=get_user_monitored_ids(user_id)
            )
        
        # Создаем prefetch с отфильтрованным queryset
        monitored_prefetch = prefetch(
            "monitored_users",
            queryset=monitored_qs
        )
        
        # Аналитика только за последние 30 дней
        analytics_prefetch = prefetch(
            "analytics",
            queryset=AIAnalytics.objects.filter(
                created_at__gte=datetime.now() - timedelta(days=30)
            ).order_by(AIAnalytics.created_at.desc())
        )
        
        return (Source.objects
            .select_related("platform")
            .prefetch_related(
                monitored_prefetch,
                analytics_prefetch,
                "tracked_in_sources",
            )
            .filter(id=pk)
            .to_select()
        )
    """)
    
    print("\n✅ Что демонстрирует этот пример:")
    print("   1. Динамическое построение queryset для prefetch")
    print("   2. Условная фильтрация на основе параметров")
    print("   3. Множественные prefetch с разными условиями")
    print("   4. Комбинация select_related и prefetch_related")
    print("   5. Читаемая цепочка методов")
    print("   6. Публичный API to_select()")


def main():
    """Запуск всех демонстраций"""
    print("\n")
    print("🎯" * 40)
    print("ДЕМОНСТРАЦИЯ УЛУЧШЕННОГО API")
    print("🎯" * 40)
    
    asyncio.run(test_prefetch_with_queryset())
    test_to_select_public_api()
    test_admin_views_pattern()
    test_comparison()
    test_flexibility_demo()
    test_real_world_example()
    
    print("\n" + "="*80)
    print("✅ ВСЕ ДЕМОНСТРАЦИИ ЗАВЕРШЕНЫ")
    print("="*80)
    print("\n📝 ИТОГО:")
    print("  1. ✅ prefetch() поддерживает queryset= (Django-style)")
    print("  2. ✅ Публичный метод to_select() вместо _build_statement()")
    print("  3. ✅ Admin views не требуют async")
    print("  4. ✅ Гибкие и читаемые запросы")
    print("  5. ✅ Полная совместимость с Django подходом")
    print("\n")


if __name__ == "__main__":
    main()
