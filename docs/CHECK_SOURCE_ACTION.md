# SourceAdmin @action: Проверить сейчас

## Функциональность

### check_source_action
**Кнопка:** "Проверить сейчас" в списке и деталях источников

**Что делает:**
1. Собирает контент из источника в реальном времени
2. Показывает статистику (лайки, комментарии, просмотры)
3. Отображает первые 20 постов в таблице
4. Предоставляет ссылки на оригинальный контент

**Пример использования:**
```
1. Откройте админку → Sources
2. Выберите источник (например, "Станислав")
3. Нажмите кнопку "Проверить сейчас"
4. Увидите страницу с результатами
```

## Шаблон

**Файл:** `app/templates/sqladmin/source_check_results.html`

**Разделы:**
- **Header:** Название источника, платформа, время проверки
- **Statistics Cards:** 4 карточки с метриками
  - Всего постов (синяя)
  - Лайков (зелёная)
  - Комментариев (голубая)
  - Просмотров (жёлтая)
- **Content Table:** Таблица с постами
  - Дата публикации
  - Текст (первые 200 символов)
  - Метрики (лайки, комментарии, просмотры)
  - Ссылка на оригинал
- **Action Buttons:** Назад и Обновить

## Реализация

### views.py
```python
@action(name="check_source", label="Проверить сейчас", add_in_list=True, add_in_detail=True)
async def check_source_action(self, request: Request):
    """Collect content and display in template."""
    from app.services.social.factory import SocialClientFactory

    source_id = int(request.query_params.get("pks", "").split(",")[0])
    source = await Source.objects.select_related("platform").get(id=source_id)

    # Collect content in real-time
    client = SocialClientFactory.get_client(source.platform.platform_type)
    content = await client.collect_data(
        source_type=source.source_type,
        external_id=source.external_id,
        content_type=ContentType.POSTS,
        params=source.params
    )

    # Show results in template
    return self.templates.TemplateResponse(
        "sqladmin/source_check_results.html",
        {
            "request": request,
            "source": source,
            "content": content[:20],  # First 20 items
            "total_count": len(content),
            "checked_at": datetime.now(),
            "stats": {...}
        }
    )
```

## Отличия от API endpoint

### API endpoint (`/api/v1/monitoring/collect/source`)
- **Асинхронный:** Возвращает `{"status": "started"}` сразу
- **Background task:** Сбор происходит в фоне
- **Для автоматизации:** Celery, cron, webhook

### @action кнопка в админке
- **Синхронный:** Собирает контент и показывает результаты
- **Real-time:** Ждёт завершения сбора
- **Для людей:** Ручная проверка, просмотр контента

## Пример результата

```
┌─────────────────────────────────────────────┐
│ Проверка источника: Станислав                │
│ 13.10.2024 18:30:00 • ВКонтакте • USER      │
└─────────────────────────────────────────────┘

┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐
│ 67      │ │ 458     │ │ 0        │ │ 17,016   │
│ Постов  │ │ Лайков  │ │ Комменты │ │ Просмотры│
└─────────┘ └─────────┘ └──────────┘ └──────────┘

┌─────────────────────────────────────────────┐
│ # │ Дата    │ Текст       │ ❤️ │ 💬 │ 👁   │
├───┼─────────┼─────────────┼───┼───┼──────┤
│ 1 │ 10.10   │ Сегодня...  │ 15│ 0 │ 245  │
│ 2 │ 09.10   │ Новый...    │ 22│ 1 │ 310  │
│...│         │             │   │   │      │
└─────────────────────────────────────────────┘
```

## Обработка ошибок

Если произошла ошибка (например, invalid token):
```html
⚠️ Ошибка
VK API error: Invalid access_token
```

## Технические детали

- **Timeout:** Синхронная операция (ждёт завершения)
- **Limit:** Показывает первые 20 постов
- **Real-time:** Не использует кэш
- **Fresh data:** Всегда актуальные данные

## Следующие шаги

1. **Добавить фильтры:** По дате, типу контента
2. **Экспорт:** Кнопка "Скачать CSV/JSON"
3. **AI анализ:** Кнопка "Анализировать" прямо на странице
4. **Сравнение:** Сравнить с предыдущим сбором
