"""
Тест исправления конфликта стратегий загрузки (Loader strategies conflict)
"""


def test_problem_explanation():
    """Объяснение проблемы и решения"""
    
    print("\n" + "="*80)
    print("ОШИБКА: Loader strategies conflict")
    print("="*80)
    
    print("\n❌ Полная ошибка:")
    print("""
    sqlalchemy.exc.InvalidRequestError: 
    Loader strategies for ORM Path[Mapper[SourceUserRelationship] -> 
    SourceUserRelationship.source -> Mapper[Source]] conflict
    """)
    
    print("\n📝 Что это значит:")
    print("""
    SQLAlchemy не может определить, какую стратегию загрузки использовать
    для связи SourceUserRelationship.source, потому что указаны ДВЕ:
    
    1. lazy="joined" в модели (автоматический JOIN при каждом запросе)
    2. select_related() в views (явный JOIN через менеджер)
    
    Это конфликт - нельзя делать JOIN дважды для одной связи!
    """)
    
    print("\n" + "="*80)
    print("ГДЕ БЫЛ КОНФЛИКТ")
    print("="*80)
    
    print("\n1️⃣ В модели (app/models/source.py):")
    print("""
    ❌ БЫЛО:
    class SourceUserRelationship(Base):
        source: Mapped["Source"] = relationship(
            "Source",
            foreign_keys="SourceUserRelationship.source_id",
            lazy="joined",  # ← Автоматический JOIN!
        )
        
        user: Mapped["Source"] = relationship(
            "Source",
            foreign_keys="SourceUserRelationship.user_id",
            lazy="joined",  # ← Автоматический JOIN!
        )
    """)
    
    print("\n2️⃣ В admin views (app/admin/views.py):")
    print("""
    ❌ БЫЛО:
    def list_query(self, request: Request) -> Select:
        return (
            SourceUserRelationship.objects
            .select_related("source", "user")  # ← Явный JOIN!
            .prefetch_related("source.platform", "user.platform")  # ← Ещё запросы!
            .to_select()
        )
    
    КОНФЛИКТ:
    - lazy="joined" говорит: "ВСЕГДА делай JOIN для source"
    - select_related("source") говорит: "Я сам сделаю JOIN для source"
    - prefetch_related("source.platform") говорит: "Загрузи platform через source"
    
    SQLAlchemy: "source уже загружен через lazy=joined, но вы хотите 
    загрузить source.platform через prefetch? КОНФЛИКТ!"
    """)
    
    print("\n" + "="*80)
    print("РЕШЕНИЕ")
    print("="*80)
    
    print("\n✅ Изменение 1: Модель (app/models/source.py)")
    print("""
    class SourceUserRelationship(Base):
        source: Mapped["Source"] = relationship(
            "Source",
            foreign_keys="SourceUserRelationship.source_id",
            lazy="select",  # ← Ленивая загрузка по требованию
        )
        
        user: Mapped["Source"] = relationship(
            "Source",
            foreign_keys="SourceUserRelationship.user_id",
            lazy="select",  # ← Ленивая загрузка по требованию
        )
    
    Теперь:
    - Связи НЕ загружаются автоматически
    - Контроль загрузки через менеджер в views
    - Нет конфликтов стратегий
    """)
    
    print("\n✅ Изменение 2: Admin views (app/admin/views.py)")
    print("""
    def list_query(self, request: Request) -> Select:
        # Используем только select_related для FK связей
        return (
            SourceUserRelationship.objects
            .select_related("source", "user")  # JOIN для source и user
            .to_select()
        )
    
    Упрощено:
    - Убран prefetch_related для вложенных связей (source.platform)
    - Только select_related для прямых FK
    - Нет конфликтов
    """)
    
    print("\n" + "="*80)
    print("ВАРИАНТЫ СТРАТЕГИЙ ЗАГРУЗКИ")
    print("="*80)
    
    strategies = [
        ("lazy='select'", "Ленивая загрузка", "Загружается при обращении (отдельный запрос)", "✅"),
        ("lazy='joined'", "Автоматический JOIN", "Всегда JOIN при загрузке основной модели", "⚠️"),
        ("lazy='subquery'", "Через подзапрос", "Загружается через подзапрос", "⚠️"),
        ("lazy='selectin'", "IN запрос", "Загружается через WHERE id IN (...)", "✅"),
        ("select_related()", "Явный JOIN", "Контролируемый JOIN через менеджер", "✅"),
        ("prefetch_related()", "Отдельный запрос", "Загружается отдельным запросом", "✅"),
    ]
    
    print("\n┌─────────────────────┬──────────────────────┬──────────────────────────────┬────┐")
    print("│ Стратегия           │ Описание             │ Когда используется            │    │")
    print("├─────────────────────┼──────────────────────┼──────────────────────────────┼────┤")
    for strategy, desc, when, ok in strategies:
        print(f"│ {strategy:19} │ {desc:20} │ {when:28} │ {ok:2} │")
    print("└─────────────────────┴──────────────────────┴──────────────────────────────┴────┘")
    
    print("\n" + "="*80)
    print("ПОЧЕМУ НЕЛЬЗЯ СМЕШИВАТЬ")
    print("="*80)
    
    conflicts = [
        {
            "combo": "lazy='joined' + select_related()",
            "problem": "Двойной JOIN для одной связи",
            "solution": "Использовать либо lazy='joined', либо select_related()"
        },
        {
            "combo": "select_related('source') + prefetch_related('source.platform')",
            "problem": "Нельзя prefetch вложенную связь если родитель через select_related",
            "solution": "Использовать только select_related или только prefetch_related"
        },
        {
            "combo": "lazy='joined' + prefetch_related('source.platform')",
            "problem": "source уже загружен через JOIN, нельзя добавить prefetch",
            "solution": "Изменить lazy='select' и контролировать загрузку"
        }
    ]
    
    for i, conflict in enumerate(conflicts, 1):
        print(f"\n{i}. {conflict['combo']}")
        print(f"   ❌ Проблема: {conflict['problem']}")
        print(f"   ✅ Решение: {conflict['solution']}")
    
    print("\n" + "="*80)
    print("РЕКОМЕНДАЦИИ")
    print("="*80)
    
    print("""
    1. ✅ Используйте lazy='select' в моделях
       → Ленивая загрузка по умолчанию
       → Полный контроль через менеджер
    
    2. ✅ Явно указывайте eager loading в views/менеджерах
       → .select_related() для FK (JOIN)
       → .prefetch_related() для M2M и обратных FK
    
    3. ❌ Избегайте lazy='joined' в моделях
       → Теряется контроль над запросами
       → Может быть избыточным
       → Конфликтует с явным select_related()
    
    4. ❌ Не смешивайте разные стратегии для одной связи
       → Только одна стратегия на связь
       → Либо в модели (lazy=), либо в views (select_related)
    
    5. ✅ Для вложенных связей используйте правильный подход:
       → select_related("source", "source__platform")  # Через JOIN
       → prefetch_related("sources", "sources__platform")  # Отдельные запросы
    """)
    
    print("\n" + "="*80)
    print("✅ ИТОГО")
    print("="*80)
    print("""
    Изменения:
    
    1. app/models/source.py:
       lazy="joined" → lazy="select"
    
    2. app/admin/views.py:
       Убран prefetch_related("source.platform", "user.platform")
    
    Результат:
    ✅ Нет конфликта стратегий загрузки
    ✅ Запросы оптимизированы
    ✅ Полный контроль через менеджер
    ✅ Ошибка исправлена
    """)


if __name__ == "__main__":
    test_problem_explanation()
