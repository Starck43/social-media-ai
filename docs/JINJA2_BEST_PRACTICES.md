# Jinja2 Best Practices - Шаблоны для проекта

## ⚠️ Частые ошибки и их решения

### 1. ❌ Неправильно: `hasattr()` в Jinja2

```jinja2
{# ❌ НЕ РАБОТАЕТ - hasattr не доступен в Jinja2 #}
{{ model.period_type.label if hasattr(model.period_type, 'label') else model.period_type }}
```

### ✅ Правильно: Использование `is defined`

```jinja2
{# ✅ РАБОТАЕТ - проверка через is defined #}
{{ model.period_type.label if model.period_type and model.period_type.label is defined else model.period_type }}
```

**Примеры из проекта**:
```jinja2
{# В source_check_results.html #}
{{ source.source_type.label if source.source_type.label is defined else source.source_type }}

{# В ai_analytics_detail.html #}
{{ model.period_type.label if model.period_type and model.period_type.label is defined else model.period_type }}
```

---

### 2. ❌ Неправильно: `tojson` без `ensure_ascii=False` для кириллицы

```jinja2
{# ❌ НЕ РАБОТАЕТ - кириллица экранируется #}
{{ model.summary_data | tojson(indent=2) }}
{# Результат: {"text": "\u0442\u0435\u043a\u0441\u0442"} #}
```

### ✅ Правильно: Добавить `ensure_ascii=False`

```jinja2
{# ✅ РАБОТАЕТ - кириллица читаемая #}
{{ model.summary_data | tojson(indent=2, ensure_ascii=False) }}
{# Результат: {"text": "текст"} #}
```

**Пример из проекта**:
```jinja2
{# В ai_analytics_detail.html #}
<pre><code>{{ model.summary_data | tojson(indent=2, ensure_ascii=False) }}</code></pre>
<pre><code>{{ model.response_payload | tojson(indent=2, ensure_ascii=False) }}</code></pre>
```

---

### 3. ❌ Неправильно: `len()` напрямую

```jinja2
{# ❌ НЕ РАБОТАЕТ #}
{{ len(items) }}
```

### ✅ Правильно: Использование фильтра `|length`

```jinja2
{# ✅ РАБОТАЕТ #}
{{ items|length }}
```

---

### 4. ❌ Неправильно: `isinstance()`

```jinja2
{# ❌ НЕ РАБОТАЕТ #}
{% if isinstance(value, dict) %}
```

### ✅ Правильно: Проверка типа через операторы

```jinja2
{# ✅ РАБОТАЕТ - проверка через итерацию #}
{% if value is mapping %}
{% if value is iterable %}
{% if value is number %}
{% if value is string %}
```

---

### 5. ❌ Неправильно: Многострочные f-strings

```jinja2
{# ❌ НЕ РАБОТАЕТ #}
{{ f"Total: {model.total}" }}
```

### ✅ Правильно: Использование фильтров и операторов

```jinja2
{# ✅ РАБОТАЕТ #}
{{ "Total: %d"|format(model.total) }}
{{ "Total: " ~ model.total }}
```

---

### 6. ❌ Неправильно: `datetime.strftime()` напрямую

```jinja2
{# ❌ Может не работать, если datetime None #}
{{ model.created_at.strftime('%d.%m.%Y') }}
```

### ✅ Правильно: С проверкой и форматером

```jinja2
{# ✅ РАБОТАЕТ - безопасно #}
{{ model.created_at.strftime('%d.%m.%Y') if model.created_at else '—' }}

{# Или через column_formatters в Python (предпочтительнее) #}
column_formatters = {
    "created_at": lambda m, a: m.created_at.strftime("%d.%m.%Y") if m.created_at else "—"
}
```

---

## 📋 Доступные встроенные функции Jinja2

### Tests (проверки):
```jinja2
{% if variable is defined %}         {# Переменная определена #}
{% if variable is undefined %}       {# Переменная не определена #}
{% if variable is none %}            {# Значение None #}
{% if variable is number %}          {# Число #}
{% if variable is string %}          {# Строка #}
{% if variable is mapping %}         {# Словарь #}
{% if variable is iterable %}        {# Итерируемый объект #}
{% if variable is sequence %}        {# Последовательность #}
{% if variable is sameas(other) %}   {# Тот же объект #}
{% if variable is equalto(value) %}  {# Равно значению #}
{% if variable is even %}            {# Четное число #}
{% if variable is odd %}             {# Нечетное число #}
```

### Filters (фильтры):
```jinja2
{{ variable|default('—') }}          {# Значение по умолчанию #}
{{ text|length }}                    {# Длина #}
{{ text|upper }}                     {# Верхний регистр #}
{{ text|lower }}                     {# Нижний регистр #}
{{ text|title }}                     {# Заглавные буквы #}
{{ text|trim }}                      {# Убрать пробелы #}
{{ list|join(', ') }}                {# Объединить список #}
{{ list|first }}                     {# Первый элемент #}
{{ list|last }}                      {# Последний элемент #}
{{ dict|tojson }}                    {# Конвертировать в JSON (ASCII-safe) #}
{{ dict|tojson(indent=2, ensure_ascii=False) }}  {# JSON с кириллицей ✅ #}
{{ number|round(2) }}                {# Округлить #}
{{ number|int }}                     {# К целому #}
{{ number|float }}                   {# К float #}
{{ number|abs }}                     {# Абсолютное значение #}
{{ text|safe }}                      {# Отключить экранирование HTML #}
{{ text|escape }}                    {# Экранировать HTML #}
{{ text|striptags }}                 {# Удалить HTML теги #}
{{ text|truncate(100) }}             {# Обрезать до 100 символов #}
{{ text|wordcount }}                 {# Количество слов #}
{{ list|sort }}                      {# Сортировать #}
{{ list|reverse }}                   {# Перевернуть #}
{{ list|unique }}                    {# Уникальные значения #}
```

### Операторы:
```jinja2
{{ value1 ~ value2 }}                {# Конкатенация строк #}
{{ value1 + value2 }}                {# Сложение #}
{{ value1 - value2 }}                {# Вычитание #}
{{ value1 * value2 }}                {# Умножение #}
{{ value1 / value2 }}                {# Деление #}
{{ value1 // value2 }}               {# Целочисленное деление #}
{{ value1 % value2 }}                {# Остаток от деления #}
{{ value1 ** value2 }}               {# Возведение в степень #}
```

---

## 🎯 Паттерны из нашего проекта

### 1. Безопасное отображение enum label:

```jinja2
{# Для period_type, source_type, notification_type и т.д. #}
{{ model.period_type.label if model.period_type and model.period_type.label is defined else model.period_type }}
```

### 2. Форматирование даты:

```jinja2
{# Безопасно #}
{{ model.created_at.strftime('%d.%m.%Y %H:%M:%S') if model.created_at else '—' }}

{# Или короче через фильтр #}
{{ model.created_at|default('—') }}
```

### 3. Форматирование чисел:

```jinja2
{# Стоимость в центах → доллары #}
${{ "%.4f"|format((model.estimated_cost or 0) / 100) }}

{# Токены с разделителями тысяч #}
{{ "{:,}".format(model.request_tokens) if model.request_tokens else '—' }}

{# Через фильтр #}
{{ model.request_tokens|default('—') }}
```

### 4. Работа с JSON:

```jinja2
{# ❌ НЕПРАВИЛЬНО - кириллица будет экранирована (\u0433 вместо "г") #}
<pre><code>{{ model.summary_data | tojson(indent=2) }}</code></pre>

{# ✅ ПРАВИЛЬНО - кириллица читаемая #}
<pre><code>{{ model.summary_data | tojson(indent=2, ensure_ascii=False) }}</code></pre>

{# Результат без ensure_ascii=False: #}
{# {"main_topics": ["\u0433\u043b\u0430\u0432\u043d\u044b\u0435..."]} #}

{# Результат с ensure_ascii=False: #}
{# {"main_topics": ["главные темы обсуждений"]} #}

{# Проверка наличия ключа #}
{% if model.summary_data and 'multi_llm_analysis' in model.summary_data %}
    {# ... #}
{% endif %}

{# Безопасный доступ к вложенным ключам #}
{{ model.summary_data.get('multi_llm_analysis', {}).get('text_analysis', {}) }}
```

**⚠️ ВАЖНО**: Всегда используйте `ensure_ascii=False` для JSON с кириллицей!

### 5. Итерация по списку:

```jinja2
{# С проверкой существования #}
{% if model.summary_data and model.summary_data.get('multi_llm_analysis', {}).get('text_analysis', {}).get('main_topics') %}
    {% for topic in model.summary_data.multi_llm_analysis.text_analysis.main_topics %}
        <li>{{ topic }}</li>
    {% endfor %}
{% else %}
    <p class="text-muted">Данные недоступны</p>
{% endif %}
```

### 6. Условное отображение badge/label:

```jinja2
{# Провайдер с бейджем #}
<span class="badge-provider">{{ model.provider_type or '—' }}</span>

{# Условный класс #}
<span class="badge {{ 'badge-success' if model.is_active else 'badge-secondary' }}">
    {{ 'Активен' if model.is_active else 'Неактивен' }}
</span>
```

### 7. Массивы типов медиа:

```jinja2
{# Через join #}
{{ ", ".join(model.media_types) if model.media_types else '—' }}

{# Или через цикл #}
{% if model.media_types %}
    {% for media_type in model.media_types %}
        <span class="badge">{{ media_type }}</span>
    {% endfor %}
{% else %}
    —
{% endif %}
```

---

## 🔧 Отладка шаблонов

### 1. Вывод всех доступных атрибутов:

```jinja2
{# Для отладки - показать все атрибуты объекта #}
<pre>{{ model|pprint }}</pre>

{# Или через dir (если доступен) #}
<pre>{{ model.__dict__|pprint }}</pre>
```

### 2. Проверка типа:

```jinja2
{# Узнать тип переменной #}
Type: {{ variable.__class__.__name__ }}

{# Или через tests #}
{% if variable is mapping %}Dictionary{% endif %}
{% if variable is sequence %}List/Tuple{% endif %}
{% if variable is string %}String{% endif %}
```

### 3. Дебаг вывод:

```jinja2
{# Вывести в консоль браузера через JavaScript #}
<script>
    console.log('Model data:', {{ model.summary_data | tojson | safe }});
</script>
```

---

## ⚡ Performance Tips

### 1. Кэширование в column_formatters (Python):

```python
# ✅ Предпочтительнее - форматирование в Python
column_formatters = {
    "estimated_cost": lambda m, a: f"${(m.estimated_cost or 0) / 100:.4f}",
    "created_at": lambda m, a: m.created_at.strftime("%d.%m.%Y") if m.created_at else "—",
}
```

### 2. Минимизация логики в шаблонах:

```python
# ❌ Плохо - вся логика в шаблоне
{{ calculate_complex_metric(model.data) }}

# ✅ Хорошо - логика в Python, простое отображение в шаблоне
# В Python:
@property
def formatted_metric(self):
    return calculate_complex_metric(self.data)

# В шаблоне:
{{ model.formatted_metric }}
```

---

## 📚 Полезные ссылки

- [Jinja2 Documentation](https://jinja.palletsprojects.com/)
- [List of Built-in Tests](https://jinja.palletsprojects.com/en/3.1.x/templates/#list-of-builtin-tests)
- [List of Built-in Filters](https://jinja.palletsprojects.com/en/3.1.x/templates/#list-of-builtin-filters)

---

**Дата создания**: 16.10.2025  
**Версия**: 1.0  
**Автор**: Factory Droid
