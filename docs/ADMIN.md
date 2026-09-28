# Admin Panel Reference

The application includes a sqladmin panel for managing all database entities.

**Base URL:** `http://localhost:8000/admin`

## Authentication

Session-based authentication. The admin panel requires a logged-in user session.

- **Login:** `GET /admin/login`
- **Logout:** handled by sqladmin's built-in logout
- **Password Reset:** `GET /admin/reset-password` → `POST /admin/reset-password`

### Password Reset Flow

```
GET  /admin/reset-password     → form (email + CSRF token)
POST /admin/reset-password     → generates temp password, logs to console
GET  /admin/login?message=password_reset → login with temp password
```

### Change Password (logged-in user)

```
GET  /admin/user/change-password/{user_id}
POST /admin/user/change-password/{user_id}
```

Requires: `current_password`, `new_password`, `confirm_password`, `csrf_token`.

### Verify Current Password (AJAX)

```
POST /admin/verify-current-password
Content-Type: application/json

{"current_password": "pass123"}
```

Returns: `{"valid": true/false}`

---

## CSRF Protection

The admin panel uses CSRF tokens for all state-changing operations. Tokens are
managed by `CSRFTokenManager` and injected into all forms.

- Token name: `csrf_token`
- Token lifetime: `SCRF_TOKEN_EXPIRY_MINUTES` (default 15 min)
- Token length: `SCRF_TOKEN_LENGTH` (default 32 bytes)

---

## Rate Limiting

| Endpoint | Rate Limit |
|---|---|
| Password Reset | `3/hour` |
| Change Password | `5/minute` |

Rate limiting is disabled in `DEBUG` mode.

---

## Admin Pages

The following model pages are available (all under `/admin/<identity>`):

| Page | Model | Icon | Description |
|---|---|---|---|
| Пользователи | `User` | 👤 | User management |
| Роли | `Role` | 🛡️ | Role definitions |
| Разрешения | `Permission` | 🔑 | Permission entries |
| Платформа | `Platform` | 🌐 | VK, Telegram, MAX platforms |
| Источник | `Source` | 📡 | Content sources |
| Сценарий агента | `AgentScenario` | 🤖 | AI analysis scenarios |
| AI Аналитика | `AIAnalytics` | 📊 | AI analysis results |
| Уведомление | `Notification` | 🔔 | System notifications |
| Провайдер LLM | `LLMProvider` | 🖥️ | LLM provider config |
| Модель LLM | `LLMModel` | 💾 | LLM model definitions |
| Креды платформ | `TenantCredential` | 🔐 | Platform credentials vault |

---

## Custom Actions

Each admin page has custom actions available in list and detail views.

### User

| Action | Description |
|---|---|
| **Изменить пароль** | Redirects to change-password form for the selected user |

### Platform

| Action | Description |
|---|---|
| **Синхронизировать** | Placeholder for platform sync (TODO: implement) |

### Source

| Action | Description |
|---|---|
| **Проверить сейчас** | Collects content from this source in real-time and displays results with stats (likes, comments, views) and pagination. Handles VK privacy errors gracefully. |

### AgentScenario

| Action | Description |
|---|---|
| **👁️ Просмотр промптов** | Shows all prompts (text, image, video, audio, unified) with JSON instructions and scope config |
| **Активировать/Деактивировать** | Toggles `is_active` status for selected scenarios |

### AIAnalytics

| Action | Description |
|---|---|
| **Просмотр анализа** | Redirects to the analytics detail page |

### Notification

| Action | Description |
|---|---|
| **Пометить прочитанным** | Marks selected notifications as read |
| **Отправить в мессенджер** | Sends notification via Telegram messenger |

### LLMProvider

No custom actions (managed via CRUD).

### LLMModel

| Action | Description |
|---|---|
| **🧪 Тестировать модель** | Sends a test prompt to the model (mock or real) and displays the response, latency, token usage, and estimated cost. Can auto-update model prices from the response. |

### Задача (AgentTask)

| Action | Description |
|---|---|
| **Выполнить сейчас** | Enqueues the selected task's job immediately; a one-time (`@once`) task is completed (deactivated) in the process |

The task create/edit form exposes **Источники** (multi-select over the
`agent_task_sources` m2m table) and **Сценарий бота** (`agent_scenario_id`) in
addition to the `payload` JSON (flat keys such as `period`, `monitored_users`,
`excluded_users`).

---

## Analytics Dashboard

Two custom dashboard pages (outside sqladmin):

### Main Dashboard

```
GET /dashboard
```

Displays aggregated analytics:
- Sentiment trends
- Top topics
- LLM statistics and costs
- Content mix
- Engagement metrics

Requires authentication via session (redirects to `/admin/login` if not logged in).

### Topic Chains Dashboard

```
GET /dashboard/topic-chains
```

Displays topic chain evolution with:
- List of topic chains
- Topic evolution over time
- Links to sources in social networks
- Timeline of analyses

---

## Static Files

```
GET /static/<file>
```

Serves static assets (logo, CSS, JS) from `app/static/`.

---

## Configuration

### Enable/Disable

Controlled by `ADMIN_ENABLED` env var (default `true`).

### Templates

Custom templates are loaded from:
1. `app/templates/` (project templates)
2. `sqladmin/templates/` (sqladmin default templates)

Custom create/edit/detail templates:
- `sqladmin/source_create.html` / `source_edit.html` / `source_details.html`
- `sqladmin/bot_scenario_create.html` / `bot_scenario_edit.html`
- `sqladmin/scenario_prompts.html`
- `sqladmin/ai_analytics_detail.html`
- `llm_provider/create.html` / `edit.html`
- `llm_model/test_results.html` / `test_error.html`

### Template Globals

All templates have access to:
- `csrf_token()` — generates a new CSRF token
- `settings` — application settings
- `debug` — debug mode flag

---

## Security

### Session Authentication

- Uses sqladmin's `AuthenticationBackend` with session storage
- Token stored in browser session cookies
- Configurable timeout via `LOGIN_TIMEOUT_MINUTES`

### Permission Checks

- All admin operations run with `PlatformScopeMiddleware` (bypass tenancy)
- The admin panel is the operator's console, not a client API
- Password reset requires the user to exist and have admin-like permissions

### Password Policy

Password changes enforce the same policy as API:
- Minimum length: `PASSWORD_MIN_LENGTH`
- Uppercase, lowercase, digits required
- Special characters optional (`PASSWORD_REQUIRE_SPECIAL`)

---

## Architecture

```
app/admin/
├── __init__.py          # Empty
├── actions.py           # LLMModelActions (test model logic)
├── auth.py              # AdminAuthBackend (session auth)
├── base.py              # BaseAdmin (common config)
├── csrf.py              # CSRFTokenManager
├── endpoints.py         # Custom endpoints (reset-password, change-password, dashboards)
├── middleware.py        # CSRF middleware for non-admin paths
├── setup.py             # setup_admin() — initializes sqladmin, registers views
├── views.py             # All admin view classes (UserAdmin, SourceAdmin, etc.)
└── widgets.py           # Custom WTForms widgets (EuropeanDateField)
```

### Setup Flow

```python
# app/admin/setup.py
def setup_admin(app):
    # 1. Create CSRF manager
    # 2. Include admin router (custom endpoints)
    # 3. Create AdminAuthBackend
    # 4. Create sqladmin instance
    # 5. Register all view classes
    # 6. Mount static files
    # 7. Attach admin to app.state
```
