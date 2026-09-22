# План визуального редактора конфигурации

## Проблема
Редактировать JSON вручную в textarea неудобно и подвержено ошибкам.

## Решение
Создать визуальный редактор с чекбоксами, полями ввода и подсказками.

---

## 1. Response Format Config Editor

### Базовые настройки (всегда видны)

```html
<div class="config-section">
    <h4>Режим работы</h4>
    
    <div class="form-group">
        <label>
            <input type="radio" name="mode" value="events" checked>
            <strong>Events</strong> - анализ по событиям/датам
        </label>
        <small class="help-text">
            Система группирует контент по дням и создаёт отдельную запись для каждого дня.
            Подходит для мониторинга активности пользователей.
        </small>
    </div>
    
    <div class="form-group">
        <label>
            <input type="radio" name="mode" value="topics">
            <strong>Topics</strong> - анализ по темам
        </label>
        <small class="help-text">
            ИИ автоматически определяет темы и группирует похожий контент.
            Подходит для мониторинга обсуждений и трендов.
        </small>
    </div>
</div>

<div class="config-section">
    <h4>Параметры формата ответа</h4>
    
    <div class="form-group">
        <label for="max_events">Максимум событий:</label>
        <input type="number" id="max_events" name="max_events" value="50" min="1" max="200">
        <small class="help-text">
            Максимальное количество событий в одном анализе (рекомендуется 30-100)
        </small>
    </div>
    
    <div class="form-group">
        <label for="annotation_length">Длина аннотаций:</label>
        <select id="annotation_length" name="annotation_length">
            <option value="brief">Краткие (1-2 предложения)</option>
            <option value="detailed">Подробные (3-5 предложений)</option>
            <option value="full">Полные (без ограничений)</option>
        </select>
        <small class="help-text">
            Насколько подробно ИИ описывает каждое событие
        </small>
    </div>
    
    <div class="form-group">
        <label>
            <input type="checkbox" name="include_target_info" checked>
            Включать информацию о целевом объекте
        </label>
        <small class="help-text">
            Группа, пост, стена - куда направлено действие пользователя
        </small>
    </div>
    
    <div class="form-group">
        <label>
            <input type="checkbox" name="include_content_preview" checked>
            Включать превью контента
        </label>
        <small class="help-text">
            Первые 100-200 символов текста поста/комментария
        </small>
    </div>
</div>
```

### Автоматически генерируемые поля (на основе analysis_types)

```html
<div class="config-section auto-generated">
    <h4>Поля в ответе (генерируются автоматически)</h4>
    <p class="info">
        На основе выбранных analysis_types система автоматически добавит в JSON schema:
    </p>
    
    <ul class="field-list">
        <li>✅ <code>analysis_title</code> - всегда</li>
        <li>✅ <code>analysis_summary</code> - всегда</li>
        <li>✅ <code>events</code> - для mode=events</li>
        <li>✅ <code>sentiment</code> - если выбран analysis_type "sentiment"</li>
        <li>✅ <code>keywords</code> - если выбран analysis_type "keywords"</li>
        <li>✅ <code>topics</code> - если выбран analysis_type "topics"</li>
    </ul>
</div>
```

---

## 2. Output Display Config Editor

```html
<div class="config-section">
    <h4>Настройки отображения в Dashboard</h4>
    
    <div class="form-group">
        <label>
            <input type="checkbox" name="show_sentiment_emoji" checked>
            Показывать эмодзи для настроения
        </label>
        <small class="help-text">
            😊 Позитивный, 😞 Негативный, 😐 Нейтральный
        </small>
    </div>
    
    <div class="form-group">
        <label>
            <input type="checkbox" name="show_keywords" checked>
            Показывать ключевые слова
        </label>
        <small class="help-text">
            Отображать keywords как цветные badges
        </small>
    </div>
    
    <div class="form-group">
        <label for="max_keywords_display">Максимум keywords для отображения:</label>
        <input type="number" id="max_keywords_display" value="10" min="3" max="30">
    </div>
    
    <div class="form-group">
        <label>
            <input type="checkbox" name="show_source_links" checked>
            Показывать ссылки на источники
        </label>
        <small class="help-text">
            Прямые ссылки на посты/комментарии в соцсети
        </small>
    </div>
    
    <div class="form-group">
        <label>
            <input type="checkbox" name="show_content_annotations">
            Показывать аннотации событий
        </label>
        <small class="help-text">
            Краткое описание каждого события под постом
        </small>
    </div>
    
    <div class="form-group">
        <label>
            <input type="checkbox" name="show_trigger_reason">
            Показывать причину срабатывания триггера
        </label>
        <small class="help-text">
            Только если настроен trigger_type для сценария
        </small>
    </div>
    
    <div class="form-group">
        <label for="group_by">Группировка записей:</label>
        <select id="group_by" name="group_by">
            <option value="date">По дате</option>
            <option value="topic">По теме</option>
            <option value="sentiment">По настроению</option>
        </select>
    </div>
</div>
```

---

## 3. Scope Editor (унифицированный)

### Режим работы

```html
<div class="config-section">
    <h4>Режим работы анализатора</h4>
    
    <div class="form-group">
        <label>
            <input type="checkbox" name="event_based" checked>
            Event-based режим
        </label>
        <small class="help-text">
            ✅ Включено: анализ по дням, запись для каждого дня<br>
            ❌ Выключено: анализ по темам, ИИ сам определяет группировку
        </small>
    </div>
</div>
```

### Параметры для каждого analysis_type

```html
<!-- Если выбран sentiment -->
<div class="config-section" data-analysis-type="sentiment">
    <h4>Настройки Sentiment Analysis</h4>
    
    <div class="form-group">
        <label>Категории настроения:</label>
        <div class="tag-input">
            <span class="tag">Позитивный <button>×</button></span>
            <span class="tag">Негативный <button>×</button></span>
            <span class="tag">Нейтральный <button>×</button></span>
            <input type="text" placeholder="Добавить категорию...">
        </div>
        <small class="help-text">
            Список возможных значений sentiment_label
        </small>
    </div>
    
    <div class="form-group">
        <label for="confidence_threshold">Порог уверенности:</label>
        <input type="range" id="confidence_threshold" min="0" max="1" step="0.1" value="0.7">
        <output>0.7</output>
        <small class="help-text">
            Минимальная уверенность ИИ для классификации (0.0 - 1.0)
        </small>
    </div>
</div>

<!-- Если выбран keywords -->
<div class="config-section" data-analysis-type="keywords">
    <h4>Настройки Keywords Extraction</h4>
    
    <div class="form-group">
        <label for="max_keywords">Максимум keywords:</label>
        <input type="number" id="max_keywords" value="20" min="5" max="50">
        <small class="help-text">
            Сколько ключевых слов извлекать из контента
        </small>
    </div>
    
    <div class="form-group">
        <label for="min_frequency">Минимальная частота:</label>
        <input type="number" id="min_frequency" value="2" min="1" max="10">
        <small class="help-text">
            Слово должно встретиться минимум N раз
        </small>
    </div>
    
    <div class="form-group">
        <label>
            <input type="checkbox" name="extract_entities">
            Извлекать именованные сущности
        </label>
    </div>
    
    <div class="form-group" id="entity-values-group" style="display: none;">
        <label>Типы сущностей:</label>
        <div class="tag-input">
            <span class="tag">Персоны <button>×</button></span>
            <span class="tag">Организации <button>×</button></span>
            <input type="text" placeholder="Добавить тип...">
        </div>
    </div>
</div>

<!-- Если выбран topics -->
<div class="config-section" data-analysis-type="topics">
    <h4>Настройки Topic Detection</h4>
    
    <div class="form-group">
        <label>Возможные темы:</label>
        <div class="tag-input">
            <span class="tag">Дизайн <button>×</button></span>
            <span class="tag">Работа <button>×</button></span>
            <span class="tag">Технологии <button>×</button></span>
            <input type="text" placeholder="Добавить тему...">
        </div>
        <small class="help-text">
            ИИ будет выбирать из этого списка или добавит "Другое"
        </small>
    </div>
    
    <div class="form-group">
        <label for="max_topics">Максимум тем:</label>
        <input type="number" id="max_topics" value="5" min="1" max="10">
        <small class="help-text">
            Сколько основных тем выделять в анализе
        </small>
    </div>
</div>
```

---

## 4. JavaScript для визуального редактора

```javascript
// Автоматическое обновление JSON при изменении полей
class ConfigEditor {
    constructor(formId, outputId) {
        this.form = document.getElementById(formId);
        this.output = document.getElementById(outputId);
        this.bindEvents();
    }
    
    bindEvents() {
        // Listen to all inputs
        this.form.addEventListener('change', () => this.updateJSON());
        this.form.addEventListener('input', () => this.updateJSON());
    }
    
    updateJSON() {
        const config = this.buildConfig();
        this.output.value = JSON.stringify(config, null, 2);
    }
    
    buildConfig() {
        const formData = new FormData(this.form);
        const config = {};
        
        // Checkboxes
        for (let [key, value] of formData.entries()) {
            if (this.form.elements[key].type === 'checkbox') {
                config[key] = this.form.elements[key].checked;
            } else if (this.form.elements[key].type === 'number') {
                config[key] = parseFloat(value);
            } else {
                config[key] = value;
            }
        }
        
        return config;
    }
    
    loadFromJSON(json) {
        const config = JSON.parse(json);
        
        for (let [key, value] of Object.entries(config)) {
            const element = this.form.elements[key];
            if (!element) continue;
            
            if (element.type === 'checkbox') {
                element.checked = value;
            } else {
                element.value = value;
            }
        }
        
        this.updateJSON();
    }
}

// Init editors
const responseFormatEditor = new ConfigEditor('response-format-form', 'response_format_config');
const outputDisplayEditor = new ConfigEditor('output-display-form', 'output_display_config');
```

---

## 5. Преимущества визуального редактора

| Аспект | Ручной JSON | Визуальный редактор |
|--------|-------------|---------------------|
| **Простота** | ❌ Нужно знать структуру | ✅ Интуитивно понятно |
| **Ошибки** | ❌ Легко допустить синтаксическую ошибку | ✅ Валидация на лету |
| **Подсказки** | ❌ Нет контекстной помощи | ✅ Description для каждого поля |
| **Скорость** | ❌ Медленно | ✅ Чекбоксы и селекты |
| **Автогенерация** | ❌ Нужно всё писать | ✅ Поля добавляются автоматически |

---

## 6. Roadmap реализации

### Фаза 1: Базовый визуальный редактор (MVP)
- [ ] Создать HTML формы для response_format_config
- [ ] Создать HTML формы для output_display_config
- [ ] JavaScript для преобразования форма → JSON
- [ ] Интегрировать в админку BotScenario

### Фаза 2: Улучшенный редактор
- [ ] Tag input для списков (sentiment values, topics)
- [ ] Условное отображение полей (если выбран analysis_type)
- [ ] Автогенерация полей на основе analysis_types
- [ ] Превью результата (как будет выглядеть в dashboard)

### Фаза 3: Продвинутые фичи
- [ ] Шаблоны конфигураций (presets)
- [ ] Импорт/экспорт конфигураций
- [ ] Валидация с подсветкой ошибок
- [ ] Визуальный конструктор JSON schema

---

## 7. Пример финальной формы

```html
<form id="scenario-config-form">
    <!-- Tabs -->
    <ul class="nav nav-tabs">
        <li><a href="#scope-tab">Scope</a></li>
        <li><a href="#response-format-tab">Response Format</a></li>
        <li><a href="#output-display-tab">Output Display</a></li>
        <li><a href="#preview-tab">Preview JSON</a></li>
    </ul>
    
    <!-- Tab content -->
    <div class="tab-content">
        <div id="scope-tab">
            <!-- Scope editor here -->
        </div>
        
        <div id="response-format-tab">
            <!-- Response format editor here -->
        </div>
        
        <div id="output-display-tab">
            <!-- Output display editor here -->
        </div>
        
        <div id="preview-tab">
            <h4>Scope JSON:</h4>
            <pre><code id="scope-json-preview"></code></pre>
            
            <h4>Response Format JSON:</h4>
            <pre><code id="response-format-json-preview"></code></pre>
            
            <h4>Output Display JSON:</h4>
            <pre><code id="output-display-json-preview"></code></pre>
        </div>
    </div>
</form>
```

---

**Дата:** 19 октября 2025  
**Статус:** План готов, ожидает реализации  
**Приоритет:** Высокий (значительно улучшит UX)
