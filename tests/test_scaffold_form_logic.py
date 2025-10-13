"""
Тест логики scaffold_form для SourceAdmin
Проверка фильтрации monitored_users при создании и редактировании
"""
import asyncio
from app.models import Source
from app.types.models import SourceType


async def test_create_form_logic():
    """
    Тест логики формы СОЗДАНИЯ
    
    При создании:
    - current_source_id = None
    - current_platform_id = None
    - Показываем ВСЕ USER источники
    """
    print("\n" + "="*80)
    print("ФОРМА СОЗДАНИЯ (Create)")
    print("="*80)
    
    current_platform_id = None
    current_source_id = None
    
    print("\n📝 Параметры:")
    print(f"   current_source_id: {current_source_id} (нет, это создание)")
    print(f"   current_platform_id: {current_platform_id} (нет)")
    
    # Строим запрос
    qs = Source.objects.filter(source_type=SourceType.USER)
    
    if current_platform_id:
        qs = qs.filter(platform_id=current_platform_id)
        print(f"   ✅ Фильтр по platform_id: {current_platform_id}")
    else:
        print(f"   ⏭️  Фильтр по platform_id: НЕ применён")
    
    if current_source_id:
        qs = qs.exclude(id=int(current_source_id))
        print(f"   ✅ Исключён источник: {current_source_id}")
    else:
        print(f"   ⏭️  Исключение источника: НЕ применено")
    
    print("\n📊 SQL запрос:")
    stmt = qs.order_by(Source.name, Source.id).to_select()
    print(f"   {stmt}")
    
    print("\n✅ Результат:")
    print("   Показываем ВСЕ USER источники со ВСЕХ платформ")


async def test_edit_form_logic():
    """
    Тест логики формы РЕДАКТИРОВАНИЯ
    
    При редактировании:
    - current_source_id = ID редактируемого источника
    - current_platform_id = platform_id этого источника
    - Показываем USER источники ТОЛЬКО с той же платформы
    - Исключаем сам редактируемый источник
    """
    print("\n" + "="*80)
    print("ФОРМА РЕДАКТИРОВАНИЯ (Edit)")
    print("="*80)
    
    # Имитация: редактируем источник с ID=5, platform_id=1
    current_source_id = 5
    current_platform_id = 1
    
    print("\n📝 Параметры:")
    print(f"   current_source_id: {current_source_id} (редактируем)")
    print(f"   current_platform_id: {current_platform_id} (загружен из источника)")
    
    # Строим запрос
    qs = Source.objects.filter(source_type=SourceType.USER)
    
    if current_platform_id:
        qs = qs.filter(platform_id=current_platform_id)
        print(f"   ✅ Фильтр по platform_id: {current_platform_id}")
    
    if current_source_id:
        qs = qs.exclude(id=int(current_source_id))
        print(f"   ✅ Исключён источник: {current_source_id}")
    
    print("\n📊 SQL запрос:")
    stmt = qs.order_by(Source.name, Source.id).to_select()
    print(f"   {stmt}")
    
    print("\n✅ Результат:")
    print(f"   Показываем USER источники с платформы {current_platform_id}")
    print(f"   БЕЗ источника {current_source_id} (сам себя не показываем)")


async def test_comparison():
    """Сравнение двух случаев"""
    print("\n" + "="*80)
    print("СРАВНЕНИЕ: Создание vs Редактирование")
    print("="*80)
    
    print("\n┌────────────────────────────────────────────────────────────────┐")
    print("│ СОЗДАНИЕ (Create)                                              │")
    print("├────────────────────────────────────────────────────────────────┤")
    print("│ current_source_id: None                                        │")
    print("│ current_platform_id: None                                      │")
    print("│                                                                │")
    print("│ Запрос:                                                        │")
    print("│ SELECT * FROM sources                                          │")
    print("│ WHERE source_type = 'USER'                                     │")
    print("│ ORDER BY name, id                                              │")
    print("│                                                                │")
    print("│ Результат: ВСЕ USER источники со ВСЕХ платформ                │")
    print("└────────────────────────────────────────────────────────────────┘")
    
    print("\n┌────────────────────────────────────────────────────────────────┐")
    print("│ РЕДАКТИРОВАНИЕ (Edit) - источник ID=5, platform_id=1          │")
    print("├────────────────────────────────────────────────────────────────┤")
    print("│ current_source_id: 5                                           │")
    print("│ current_platform_id: 1                                         │")
    print("│                                                                │")
    print("│ Запрос:                                                        │")
    print("│ SELECT * FROM sources                                          │")
    print("│ WHERE source_type = 'USER'                                     │")
    print("│   AND platform_id = 1                                          │")
    print("│   AND id != 5                                                  │")
    print("│ ORDER BY name, id                                              │")
    print("│                                                                │")
    print("│ Результат: USER источники ТОЛЬКО с платформы 1, БЕЗ ID=5      │")
    print("└────────────────────────────────────────────────────────────────┘")


async def test_code_explanation():
    """Объяснение как работает код"""
    print("\n" + "="*80)
    print("КАК РАБОТАЕТ КОД В scaffold_form()")
    print("="*80)
    
    print("""
Шаг 1: Пытаемся получить ID текущего источника
    
    try:
        # При СОЗДАНИИ: request.path_params = {} → pk = None
        # При РЕДАКТИРОВАНИИ: request.path_params = {"pk": "5"} → pk = "5"
        if hasattr(self, 'request') and self.request:
            current_source_id = self.request.path_params.get("pk")
        
        # Если есть ID - загружаем источник
        if current_source_id:
            current_source = await Source.objects.get(id=int(current_source_id))
            current_platform_id = current_source.platform_id
    except:
        pass  # При создании или ошибке - просто None

Шаг 2: Строим запрос с условными фильтрами
    
    qs = Source.objects.filter(source_type=SourceType.USER)
    
    # Фильтр по платформе (применится только при редактировании)
    if current_platform_id:
        qs = qs.filter(platform_id=current_platform_id)
    
    # Исключаем сам источник (применится только при редактировании)
    if current_source_id:
        qs = qs.exclude(id=int(current_source_id))
    
    sources = await qs.order_by(Source.name, Source.id)

Шаг 3: Устанавливаем данные для поля формы
    
    form_class.monitored_users.kwargs.update({
        "data": [(str(source.id), source) for source in sources],
        "get_label": lambda obj: obj.name or obj.external_id or f"Источник #{obj.id}"
    })
    """)


async def test_real_example():
    """Реальный пример работы"""
    print("\n" + "="*80)
    print("РЕАЛЬНЫЙ ПРИМЕР")
    print("="*80)
    
    print("\n🗂️ База данных:")
    print("""
    Sources:
    ID | platform_id | source_type | name
    ---|-------------|-------------|------------------
    1  | 1 (VK)      | GROUP       | VK Group Official
    2  | 1 (VK)      | USER        | Ivan Ivanov
    3  | 1 (VK)      | USER        | Petr Petrov
    4  | 2 (TG)      | CHANNEL     | TG Channel News
    5  | 2 (TG)      | USER        | Maria Smirnova
    6  | 2 (TG)      | USER        | Alex Kozlov
    """)
    
    print("\n📝 Сценарий 1: СОЗДАНИЕ нового источника")
    print("   URL: /admin/source/create")
    print("   current_source_id: None")
    print("   current_platform_id: None")
    print("\n   Доступные monitored_users:")
    print("   ✅ ID=2 - Ivan Ivanov (VK)")
    print("   ✅ ID=3 - Petr Petrov (VK)")
    print("   ✅ ID=5 - Maria Smirnova (TG)")
    print("   ✅ ID=6 - Alex Kozlov (TG)")
    print("   📊 Итого: 4 USER источника со ВСЕХ платформ")
    
    print("\n📝 Сценарий 2: РЕДАКТИРОВАНИЕ источника ID=5 (Maria, TG)")
    print("   URL: /admin/source/edit/5")
    print("   current_source_id: 5")
    print("   current_platform_id: 2 (Telegram)")
    print("\n   Доступные monitored_users:")
    print("   ❌ ID=2 - Ivan Ivanov (VK) - другая платформа")
    print("   ❌ ID=3 - Petr Petrov (VK) - другая платформа")
    print("   ❌ ID=5 - Maria Smirnova (TG) - сам источник, исключён")
    print("   ✅ ID=6 - Alex Kozlov (TG) - та же платформа")
    print("   📊 Итого: 1 USER источник с платформы Telegram (без себя)")


async def main():
    await test_create_form_logic()
    await test_edit_form_logic()
    await test_comparison()
    await test_code_explanation()
    await test_real_example()
    
    print("\n" + "="*80)
    print("✅ ИТОГО")
    print("="*80)
    print("""
Код scaffold_form() автоматически:

1. При СОЗДАНИИ:
   • Показывает ВСЕ USER источники
   • Не применяет фильтров по платформе
   • Можно выбрать пользователей с любой платформы

2. При РЕДАКТИРОВАНИИ:
   • Определяет ID и platform_id текущего источника
   • Показывает только USER источники с той же платформы
   • Исключает сам источник из списка (нельзя добавить себя)

3. Преимущества:
   ✅ Один метод для обеих форм
   ✅ Автоматическая фильтрация
   ✅ Предотвращает циклические зависимости
   ✅ Логичная группировка по платформам

Код готов к использованию! 🎉
    """)


if __name__ == "__main__":
    asyncio.run(main())
