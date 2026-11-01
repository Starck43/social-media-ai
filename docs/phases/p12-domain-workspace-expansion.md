# P12 — Domain workspace and navigation expansion

[Product direction](../PRODUCT_PLAN.md#expansion-sequence) · [ROADMAP](../../ROADMAP.md#p12)

**Goal:** Expand the `/app` web workspace from a flat 10-item social monitoring menu into a structured 5-domain business assistant (`Обзор и ИИ-чат`, `Мониторинг и источники`, `База знаний и документы`, `Коммуникации и фоллоу-апы`, `Кейсы и система`) while preserving 100% route continuity (`/app/settings` retains all 7 existing tabs and consolidates all external connectors under `/app/settings?tab=connections`).

---

## 1. Architectural Decisions (Canonical Reference for All Agents)

1. **Connectors Live in `/app/settings?tab=connections` (No Standalone `/app/connectors` Route):**
   - Social OAuth/MTProto (`VK`, `Telegram`) and new external business/cloud connectors (`Email IMAP/SMTP`, `Bitrix24`, `1C OData`, `Яндекс.Диск`, `Облако Mail.ru`, `Локальный шлюз`) live in a single unified tab `/app/settings?tab=connections` driven by `app/services/social/connections.py` and `app/web/settings.py`.
2. **Extend `Platform` (`platforms`), Do NOT Rename:**
   - Building on `Q-16` (`d3a4a40c`), `Platform` (`app/models/collection/platform.py`) remains the global catalog of known external systems (1 row per `platform_type`, managed via SQLAdmin / `_BOOTSTRAP` in `app/services/social/platform_config.py`).
   - Both `PlatformType` and `SourceType` live in `app/types/enums/platform_types.py` and use the 3-tuple format `("db_value", "Display Name", "Emoji")`.
   - Extend `PlatformType` with 6 new values (`email`, `bitrix24`, `onec`, `yandex_disk`, `mailru_cloud`, `local_gateway`) and `SourceType` with `DRIVE_FOLDER = ("drive_folder", "Папка облачного диска", "📁")`.
3. **Per-User, Per-Workspace Credentials (`UserCredential`):**
   - `UserCredential` (`app/models/identity/user_credential.py`) already has `platform: String(30)`, `kind: String(30)`, `secret_encrypted: Text`, and `meta: JSON`.
   - Store workspace scoping (`"tenant_id": current_tenant.id`) and non-secret parameters (`portal_url`, `odata_url`, `imap_host`, `account_login`, `data_boundary`) in `UserCredential.meta` (and optional `tenant_id` filter in `app/services/social/connections.py`), avoiding destructive public-schema migrations while enforcing strict per-workspace isolation.
4. **Cloud Drive & Document Connection Flow (`SourceType.DRIVE_FOLDER`):**
   - Mounted folders on Yandex Disk (`https://cloud-api.yandex.net/v1/disk/resources`) or Mail.ru Cloud WebDAV (`https://webdav.cloud.mail.ru`) are persisted as `Source(platform_id=..., source_type=SourceType.DRIVE_FOLDER, external_id="disk:/Договоры 2026", params={"credential_id": cred.id, "allowed_extensions": [".pdf", ".docx", ".xlsx"]})`.
5. **Zero-Conflict Parallelism Rule (`app/web/nav.py` & `app/web/__init__.py`):**
   - Wave 1/2 domain tasks (`P12-T03`, `P12-T04`, `P12-T05`) create **only** their own service package (`app/services/<domain>/`), route module (`app/web/<section>.py`), template (`app/web/templates/web/<section>.html`), and isolated unit test (`tests/unit_web/test_p12_t0X_*.py`).
   - They **must NOT edit `app/web/nav.py`, `app/web/__init__.py`, or `tests/unit_web/test_p12_t01_nav_groups.py`**.
   - Central router registration in `app/web/__init__.py` and flipping `ready=True` in `app/web/nav.py` happen once in **Wave 3 (`P12-T06`)**.

---

## 2. Multi-Agent Wave Matrix & Role Assignment (`<= C3` Local Safe)

```
[Wave 0: DONE in dev (ec7582c4, ddd9ace4, d3a4a40c)]
  ├── Q-16: Shared Platform catalog & canonical Platform.params JSON
  └── P12-T01: Grouped sidebar in app/web/nav.py + 6 draft Jinja2 templates in app/web/templates/web/
         │
         ├─────────────────────────────────────────────────────────┐
         ▼                                                         ▼
[Wave 1A: Foundation (C3)]                                [Wave 1B: Independent Track (C3 — Local Agent)]
  └── P12-T02 (P2 · C3)                                     └── P12-T05 (P3 · C3)
      /app/settings?tab=connections                             Cases & Decimal Supplier Comparison (/app/cases)
      PlatformType (8 services) + SourceType.DRIVE_FOLDER       Pure Python decimal.Decimal (zero DB/API wait!)
         │                                                         │
         ├──────────────────────────────┐                          │
         ▼                              ▼                          │
[Wave 2A: Parallel Track A (C4)] [Wave 2B: Parallel Track B (C4)]  │
  └── P12-T03 (P3 · C4)            └── P12-T04 (P3 · C4)           │
      Knowledge & Documents            Communications & Follow-ups │
      (/app/knowledge, /app/documents) (/app/communications,       │
                                        /app/followups)            │
         │                              │                          │
         └───────────────┬──────────────┴──────────────────────────┘
                         ▼
[Wave 3: Cross-Domain Finalizer (C3 — Local Agent)]
  └── P12-T06 (P3 · C3)
      Register P12 routers in app/web/__init__.py, flip ready=True in app/web/nav.py,
      wire #context-panel in base.html & /app/chat?context_type=...&boundary=local_only
```

| Wave | Task ID | Priority / Complexity | Recommended Agent Role | Depends On | Parallelizable With | Strictly Isolated `Owned paths` |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Wave 0** | [P12-T01](../audit/p12-t01.md) | `P2` · `C2` | **AI Studio Coordinator** (**DONE**) | — | — | `app/web/nav.py`, `app/web/templates/web/_shell_nav.html` |
| **Wave 1A** | [P12-T02](../tasks/p12-t02.md) | `P2` · `C3` | **AI Studio Coordinator** *(or Remote Agent)* | `P12-T01`, `Q-16` | `P12-T05` | `app/types/enums/platform_types.py`, `app/services/social/platform_config.py`, `app/services/social/connections.py`, `app/web/settings.py`, `app/web/templates/web/settings.html`, `tests/unit_web/test_p12_t02_settings_connectors.py` |
| **Wave 1B / 2C** | [P12-T05](../tasks/p12-t05.md) | `P3` · **`C3`** | **Local Agent (`<= C3`)** *(can start immediately!)* | `P12-T01` | `P12-T02`, `P12-T03`, `P12-T04` | `app/services/cases/`, `app/web/cases.py`, `app/web/templates/web/cases.html`, `tests/unit_web/test_p12_t05_cases_comparison.py` |
| **Wave 2A** | [P12-T03](../tasks/p12-t03.md) | `P3` · `C4` | **AI Studio Coordinator** | `P12-T01`, `P12-T02` | `P12-T04`, `P12-T05` | `app/services/knowledge/`, `app/web/knowledge.py`, `app/web/documents.py`, `app/web/templates/web/knowledge.html`, `app/web/templates/web/documents.html`, `tests/unit_web/test_p12_t03_knowledge_documents.py` |
| **Wave 2B** | [P12-T04](../tasks/p12-t04.md) | `P3` · `C4` | **Remote Agent** | `P12-T01`, `P12-T02` | `P12-T03`, `P12-T05` | `app/services/communications/`, `app/web/communications.py`, `app/web/followups.py`, `app/web/templates/web/communications.html`, `app/web/templates/web/followups.html`, `tests/unit_web/test_p12_t04_communications_followups.py` |
| **Wave 3** | [P12-T06](../tasks/p12-t06.md) | `P3` · **`C3`** | **Local Agent (`<= C3`)** *(after Wave 2 merges)* | `P12-T03`, `P12-T04`, `P12-T05` | None | `app/web/__init__.py`, `app/web/nav.py`, `app/web/templates/web/base.html`, `app/web/chat.py`, `app/static/js/context_panel.js`, `tests/unit_web/test_p12_t01_nav_groups.py`, `tests/unit_web/test_p12_t06_context_handoff.py` |

---

## 3. Draft Jinja2 Templates & Progressive Wiring Contract

Draft Jinja2 templates for all expansion views are already checked into `app/web/templates/web/` (`ddd9ace4`):
- `app/web/templates/web/settings.html` (`tab=connections` extended in `P12-T02` — expects `workspace_connectors: list[dict]`)
- `app/web/templates/web/knowledge.html` (`P12-T03` — expects `knowledge_snippets: list[dict]`)
- `app/web/templates/web/documents.html` (`P12-T03` — expects `documents: list[dict]`)
- `app/web/templates/web/communications.html` (`P12-T04` — expects `outbox_items: list[dict]`)
- `app/web/templates/web/followups.html` (`P12-T04` — expects `followup_items: list[dict]`)
- `app/web/templates/web/cases.html` (`P12-T05` — expects `case_record: dict`, `comparison_rows: list[dict]`)
