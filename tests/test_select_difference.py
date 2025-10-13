"""
Демонстрация разницы между .to_select(), await и .all()
"""
import asyncio
from sqlalchemy.sql import Select
from app.models import Source


async def test_difference():
    """Показываем разницу между методами"""
    
    print("\n" + "="*80)
    print("РАЗНИЦА МЕЖДУ .to_select(), await и .all()")
    print("="*80)
    
    # 1. QuerySet (ленивый объект)
    print("\n1. QuerySet (ленивый объект):")
    qs = Source.objects.filter(is_active=True)
    print(f"   Тип: {type(qs)}")
    print(f"   Это: QuerySet")
    print(f"   SQL: НЕ выполнен")
    print(f"   Возвращает: Объект для построения запроса")
    
    # 2. .to_select() - преобразование в Select
    print("\n2. .to_select() - преобразование в Select:")
    stmt = qs.to_select()
    print(f"   Тип: {type(stmt)}")
    print(f"   Это: {stmt.__class__.__name__}")
    print(f"   SQL: НЕ выполнен")
    print(f"   Возвращает: SQLAlchemy Select объект")
    print(f"   Можно использовать: В SQLAdmin, для модификации SQL")
    
    # 3. await - выполнение запроса
    print("\n3. await - выполнение запроса:")
    results = await qs
    print(f"   Тип: {type(results)}")
    print(f"   Это: Список объектов")
    print(f"   SQL: ВЫПОЛНЕН!")
    print(f"   Возвращает: Результаты запроса (list)")
    
    # 4. .all() - то же что await
    print("\n4. await qs.all() - то же что await:")
    results2 = await qs.all()
    print(f"   Тип: {type(results2)}")
    print(f"   Это: Список объектов")
    print(f"   SQL: ВЫПОЛНЕН!")
    print(f"   Возвращает: Результаты запроса (list)")
    
    print("\n" + "="*80)
    print("КОГДА ЧТО ИСПОЛЬЗОВАТЬ")
    print("="*80)
    
    print("\n📌 Используйте .to_select():")
    print("   ✅ В SQLAdmin методах list_query() и details_query()")
    print("   ✅ Когда нужен Select объект для дальнейших модификаций")
    print("   ✅ Для отладки SQL без выполнения")
    print("""
   def list_query(self, request: Request) -> Select:
       return User.objects.filter(is_active=True).to_select()
   """)
    
    print("\n📌 Используйте await (или await .all()):")
    print("   ✅ Когда нужны РЕЗУЛЬТАТЫ запроса")
    print("   ✅ В обычном коде приложения")
    print("   ✅ В API endpoints")
    print("""
   # В FastAPI endpoint
   @app.get("/users")
   async def get_users():
       users = await User.objects.filter(is_active=True)  # Результаты!
       return users
   """)
    
    print("\n" + "="*80)
    print("ПРОБЛЕМА ЕСЛИ НЕ ИСПОЛЬЗОВАТЬ .to_select() В ADMIN")
    print("="*80)
    
    print("\n❌ Если попробовать вернуть QuerySet:")
    print("""
   def list_query(self, request: Request) -> Select:
       return User.objects.filter(is_active=True)  # ❌ Неверный тип!
       # SQLAdmin ожидает Select, а получит QuerySet
       # TypeError: expected Select, got QuerySet
   """)
    
    print("\n❌ Если попробовать вернуть результаты:")
    print("""
   async def list_query(self, request: Request) -> Select:
       return await User.objects.filter(is_active=True)  # ❌ Неверный тип!
       # SQLAdmin ожидает Select, а получит list[User]
       # TypeError: expected Select, got list
   """)
    
    print("\n✅ Правильно - вернуть Select:")
    print("""
   def list_query(self, request: Request) -> Select:
       return User.objects.filter(is_active=True).to_select()  # ✅ Верный тип!
       # SQLAdmin получает Select и сам выполнит запрос когда нужно
   """)


async def test_why_to_select_exists():
    """Почему .to_select() нужен для SQLAdmin"""
    
    print("\n" + "="*80)
    print("ПОЧЕМУ SQLAdmin ТРЕБУЕТ Select, А НЕ РЕЗУЛЬТАТЫ")
    print("="*80)
    
    print("""
SQLAdmin работает так:

1. Вызывает list_query() для получения ШАБЛОНА запроса (Select)
2. Добавляет свои условия (пагинация, сортировка, поиск)
3. Выполняет модифицированный запрос
4. Отображает результаты

Пример:
    # 1. SQLAdmin получает базовый Select от вас
    base_query = admin.list_query(request)  # Select
    
    # 2. Добавляет пагинацию
    query = base_query.limit(20).offset(40)
    
    # 3. Добавляет поиск
    if search:
        query = query.where(User.username.contains(search))
    
    # 4. Выполняет запрос
    results = await session.execute(query)

Если бы вы вернули результаты (list), SQLAdmin не смог бы добавить
свои условия - результаты уже получены!
    """)


async def test_practical_example():
    """Практический пример"""
    
    print("\n" + "="*80)
    print("ПРАКТИЧЕСКИЙ ПРИМЕР")
    print("="*80)
    
    print("\n📝 В обычном коде (получаем результаты):")
    print("""
async def get_active_users():
    # Вариант 1: через await
    users = await User.objects.filter(is_active=True)
    
    # Вариант 2: через .all()
    users = await User.objects.filter(is_active=True).all()
    
    # Результат: list[User]
    for user in users:
        print(user.username)
    """)
    
    print("\n📝 В SQLAdmin (возвращаем Select):")
    print("""
class UserAdmin(BaseAdmin):
    def list_query(self, request: Request) -> Select:
        # Возвращаем Select, НЕ результаты
        return User.objects.filter(is_active=True).to_select()
    
    # SQLAdmin сам выполнит запрос и добавит свою логику
    """)
    
    print("\n📝 Для отладки SQL:")
    print("""
# Получить SQL без выполнения
stmt = User.objects.filter(is_active=True).to_select()
print(stmt)  # SELECT users.* FROM users WHERE users.is_active = true
    """)


async def main():
    await test_difference()
    await test_why_to_select_exists()
    await test_practical_example()
    
    print("\n" + "="*80)
    print("✅ ИТОГО")
    print("="*80)
    print("""
┌─────────────────┬──────────────────┬─────────────────────────────────┐
│ Метод           │ Возвращает       │ Когда использовать              │
├─────────────────┼──────────────────┼─────────────────────────────────┤
│ .to_select()    │ Select (шаблон)  │ SQLAdmin, отладка SQL           │
│ await           │ list (результаты)│ Обычный код, API endpoints      │
│ await .all()    │ list (результаты)│ То же что await                 │
│ await .first()  │ object (1 объект)│ Получить первый результат       │
│ await .count()  │ int (количество) │ Подсчитать без загрузки объектов│
│ await .exists() │ bool (да/нет)    │ Проверить существование         │
└─────────────────┴──────────────────┴─────────────────────────────────┘

ВЫВОД:
• .to_select() НЕ выполняет запрос - возвращает шаблон
• await ВЫПОЛНЯЕТ запрос - возвращает результаты
• SQLAdmin нужен именно шаблон (Select), чтобы добавить свою логику
    """)


if __name__ == "__main__":
    asyncio.run(main())
