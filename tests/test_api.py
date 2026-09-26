"""
Демонстрация улучшенного API:
1. Публичный метод to_select() вместо _build_statement()
"""
import asyncio
from app.models import Source


async def test_prefetch_basic():
    """Тест базового prefetch"""
    print("\n" + "="*80)
    print("ТЕСТ: Базовый prefetch")
    print("="*80)
    
    # Пример prefetch для relationship
    print("\n✅ Prefetch для relationship:")
    print(f"   prefetch('analytics', queryset=Source.objects.filter(is_active=True))")
    print(f"   Гибко: можно использовать любые методы QuerySet!")


def test_to_select_public_api():
    """Тест публичного метода to_select()"""
    print("\n" + "="*80)
    print("ТЕСТ: Публичный метод .to_select()")
    print("="*80)
    
    print("\n✅ СТАЛО (правильно):")
    print("   stmt = qs.to_select()  # Публичный API!")
    
    # Пример использования
    print("\n📝 Пример в admin views:")
    print("""
    def list_query(self, request: Request) -> Select:
        return Source.objects.filter(is_active=True).to_select()
    """)
    
    # Реальный пример
    print("\n🔍 Реальный запрос:")
    stmt = Source.objects.filter(is_active=True).to_select()
    print(f"   Тип результата: {type(stmt)}")
    print(f"   Это SQLAlchemy Select: {stmt.__class__.__name__}")


def test_admin_views_pattern():
    """Демонстрация правильного паттерна для admin views"""
    print("\n" + "="*80)
    print("ТЕСТ: Правильный паттерн для Admin Views")
    print("="*80)
    
    print("\n📋 SourceAdmin.list_query():")
    print("""
    def list_query(self, request: Request) -> Select:
        return Source.objects.to_select()
    """)
    
    print("\n📋 SourceAdmin.details_query():")
    print("""
    def details_query(self, request: Request) -> Select:
        pk = int(request.path_params["pk"])
        return (Source.objects
            .prefetch_related("analytics", "platform")
            .filter(id=pk)
            .to_select()
        )
    """)
    
    print("\n✅ Преимущества:")
    print("   1. Методы НЕ async (SQLAdmin требует sync)")
    print("   2. Используем публичный API to_select()")
    print("   3. Читаемые цепочки методов")


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
    """)
    
    print("\n✅ НОВЫЙ ПОДХОД:")
    print("""
    # 1. Публичный API
    def details_query(self, request):
        return Source.objects.filter(...).to_select()  # ✅ Публичный!
    """)


def main():
    """Запуск всех демонстраций"""
    print("\n")
    print("🎯" * 40)
    print("ДЕМОНСТРАЦИЯ УЛУЧШЕННОГО API")
    print("🎯" * 40)
    
    asyncio.run(test_prefetch_basic())
    test_to_select_public_api()
    test_admin_views_pattern()
    test_comparison()
    
    print("\n" + "="*80)
    print("✅ ВСЕ ДЕМОНСТРАЦИИ ЗАВЕРШЕНЫ")
    print("="*80)
    print("\n📝 ИТОГО:")
    print("  1. ✅ Публичный метод to_select() вместо _build_statement()")
    print("  2. ✅ Admin views не требуют async")
    print("  3. ✅ Гибкие и читаемые запросы")
    print("\n")


if __name__ == "__main__":
    main()
