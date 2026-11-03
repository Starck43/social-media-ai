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
3. **Explicit Workspace Connections (P12-T08):**
   - `Platform.params` remains global non-secret API configuration. Versioned provider/method/form contracts live in `app/integrations/catalog/`.
   - Mail.ru/Yandex Mail instances use `WorkspaceConnection` with explicit tenant + owner and separate encrypted `WorkspaceConnectionSecret` (migration 0121). Configuration, storage and IMAP login checks are implemented; synchronization and sending are not.
   - This supersedes the earlier proposal to scope new business connections only through `UserCredential.meta`. Existing personal VK/Telegram credentials and legacy consumers remain unchanged and are not auto-bound to a workspace. See [connector contract](../CONNECTOR_CONFIGURATION.md).
4. **Cloud Drive & Document Connection Flow (`SourceType.DRIVE_FOLDER`):**
   - The existing descriptor contract (not an implemented drive adapter or the new mail-connection storage): mounted folders on Yandex Disk (`https://cloud-api.yandex.net/v1/disk/resources`) or Mail.ru Cloud WebDAV (`https://webdav.cloud.mail.ru`) are persisted as `Source(platform_id=..., source_type=SourceType.DRIVE_FOLDER, external_id="disk:/Договоры 2026", params={"credential_id": cred.id, "allowed_extensions": [".pdf", ".docx", ".xlsx"]})`.
5. **Zero-Conflict Parallelism Rule (`app/web/nav.py` & `app/web/__init__.py`):**
   - Wave 1/2 domain tasks (`P12-T03`, `P12-T04`, `P12-T05`) create **only** their own service package (`app/services/<domain>/`), route module (`app/web/<section>.py`), template (`app/web/templates/web/<section>.html`), and isolated unit test (`tests/unit_web/test_p12_t0X_*.py`).
   - They **must NOT edit `app/web/nav.py`, `app/web/__init__.py`, or `tests/unit_web/test_p12_t01_nav_groups.py`**.
   - Router registration and `ready=True` activation were implemented as the separate P12-T09 reachability follow-up (`e577951b`), not by P12-T06. P12-T06 delivered the evidence drawer/chat handoff; its task card is historical and must not assign the router files to new T06 work.

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
[Wave 3: Cross-Domain Integration (after Wave 2)]
  ├── P12-T06: Evidence drawer and /app/chat handoff (PR #303; context UI/runtime boundary)
  └── P12-T09: Route reachability and nav activation follow-up (DONE; e577951b)
```

| Wave | Task ID | Priority / Complexity | Recommended Agent Role | Depends On | Parallelizable With | Strictly Isolated `Owned paths` |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Wave 0** | [P12-T01](../audit/p12-t01.md) | `P2` · `C2` | **AI Studio Coordinator** (**DONE**) | — | — | `app/web/nav.py`, `app/web/templates/web/_shell_nav.html` |
| **Wave 1A** | [P12-T02](../tasks/p12-t02.md) | `P2` · `C3` | **AI Studio Coordinator** *(or Remote Agent)* | `P12-T01`, `Q-16` | `P12-T05` | `app/types/enums/platform_types.py`, `app/services/social/platform_config.py`, `app/services/social/connections.py`, `app/web/settings.py`, `app/web/templates/web/settings.html`, `tests/unit_web/test_p12_t02_settings_connectors.py` |
| **Wave 1B / 2C** | [P12-T05](../tasks/p12-t05.md) | `P3` · **`C3`** | **Local Agent (`<= C3`)** *(can start immediately!)* | `P12-T01` | `P12-T02`, `P12-T03`, `P12-T04` | `app/services/cases/`, `app/web/cases.py`, `app/web/templates/web/cases.html`, `tests/unit_web/test_p12_t05_cases_comparison.py` |
| **Wave 2A** | [P12-T03](../tasks/p12-t03.md) | `P3` · `C4` | **AI Studio Coordinator** | `P12-T01`, `P12-T02` | `P12-T04`, `P12-T05` | `app/services/knowledge/`, `app/web/knowledge.py`, `app/web/documents.py`, `app/web/templates/web/knowledge.html`, `app/web/templates/web/documents.html`, `tests/unit_web/test_p12_t03_knowledge_documents.py` |
| **Wave 2B** | [P12-T04](../tasks/p12-t04.md) | `P3` · `C4` | **Remote Agent** | `P12-T01`, `P12-T02` | `P12-T03`, `P12-T05` | `app/services/communications/`, `app/web/communications.py`, `app/web/followups.py`, `app/web/templates/web/communications.html`, `app/web/templates/web/followups.html`, `tests/unit_web/test_p12_t04_communications_followups.py` |
| **Wave 3** | [P12-T06](../tasks/p12-t06.md) | `P3` · `C3` | **Evidence drawer / chat handoff** | `P12-T03`, `P12-T04`, `P12-T05` | P12-T09 (disjoint implementation paths) | `app/web/templates/web/base.html`, `app/web/chat.py`, `app/web/templates/web/chat.html`, `app/static/js/context_panel.js`, `tests/unit_web/test_p12_t06_context_handoff.py` |
| **Wave 3 follow-up** | [P12-T09](../tasks/p12-t09.md) | historical | **Route reachability / nav activation** | `P12-T03`, `P12-T04`, `P12-T05` | P12-T06 (disjoint implementation paths) | `app/web/__init__.py`, `app/web/nav.py`, `tests/unit_web/test_p12_t01_nav_groups.py`, `tests/unit_web/test_p12_t03_knowledge_documents.py`, `tests/unit_web/test_p12_t04_communications_followups.py`, `tests/unit_web/test_p12_t05_cases_comparison.py`, `tests/web/test_p12_routes.py` |

---

## 3. Draft Jinja2 Templates & Progressive Wiring Contract

Draft Jinja2 templates for all expansion views are already checked into `app/web/templates/web/` (`ddd9ace4`):
- `app/web/templates/web/settings.html` (`tab=connections` extended in `P12-T02` — expects `workspace_connectors: list[dict]`)
- `app/web/templates/web/knowledge.html` (`P12-T03` — expects `knowledge_snippets: list[dict]`)
- `app/web/templates/web/documents.html` (`P12-T03` — expects `documents: list[dict]`)
- `app/web/templates/web/communications.html` (`P12-T04` — expects `outbox_items: list[dict]`)
- `app/web/templates/web/followups.html` (`P12-T04` — expects `followup_items: list[dict]`)
- `app/web/templates/web/cases.html` (`P12-T05` — expects `case_record: dict`, `comparison_rows: list[dict]`)

## Provider-specific connector follow-up

- [P12-T08](../tasks/p12-t08.md): provider catalog, workspace connection configuration and
  safe credential UX. **DONE 2026-11-02**, merge `f36a1a53` (PR #304), migration 0121
  applied to `public`. Review: [audit/p12-t08.md](../audit/p12-t08.md).
- [P12-T10](../tasks/p12-t10.md): unified provider presentation catalog. **DONE 2026-11-02**,
  merge `3a4af09e` (PR #305). Code-owned descriptors, local SVG glyphs, shared
  components; no transport, migration or production change.
- [P12-T11](../audit/p12-t11.md): **DONE 2026-11-02**. Persisted tenant-isolated
  knowledge/document evidence, workspace ACL and working drawer/chat handoff;
  accepted source `f5cabb09e536515000532e6767f108ec79775a17`, merge `5651d37c`
  ([PR #306](https://github.com/Starck43/social-media-ai/pull/306)). Migration 0122
  validated only in test_schema; public apply/deployment remain separately gated.
  Only cloud-bound context reaches the shared web-chat runtime; company/local-only
  sends are refused until a corresponding end-to-end route exists.

- [P12-T16](../audit/p12-t16.md): **ACCEPTED 2026-11-02**. Migration 0123 adds
  nullable personal source ownership and `SOURCE_AUTH_REQUIRED`. Source ownership
  is actor-derived, revalidated against active workspace membership, and never
  falls back to the workspace owner's personal credential.

## Credential and connector security follow-up

- [P12-T14](../audit/p12-t14.md): **DONE 2026-11-03**. Protected credentials,
  explicit personal account/grant/source authorization and reviewed runtime fences;
  accepted source `9885a1f94148c5a0db8532a6c31f5b47ff4797b8`. Live checks are owner-run.

## File-workspace continuation

- [P12-T12](../tasks/p12-t12.md): originals-first file workspace, compact
  version-aware agent knowledge, safe original-file opening and approved actions.
  See the active card for implementation, dependencies and remaining work.

- [P12-T13](../tasks/p12-t13.md): prepare file-workspace slice 1 contracts and
  candidate-provider review.
- [P8-T34](../tasks/p8-t34.md): owner-selected Yandex Disk pilot, transferred from
  P8; one folder, read-only. Folder-level restrictions and mutation safety are
  required; no provider writes or delete operation in this pilot.

- [P12-T15](../tasks/p12-t15.md): user-managed per-folder restrictions and
  separately gated, explicitly approved file mutations after the read-only pilot.

- [P12-T17](../audit/p12-t17.md): improve navigation, spacing and clarity across the existing evidence workspaces. **DONE 2026-11-02**, accepted PR head `ed49ad8c` (PR #316, recovers #310). Template-level evidence only; DB/ASGI and authenticated-browser checks remain a follow-up handoff.

## Business-module continuation (P13–P19)

The owner's business-module TZ continues the P12 connectors and domain workspaces
in [P13–P19](../BUSINESS_MODULES.md): Bitrix24 and 1C adapters behind the existing
catalog descriptors, a persistent outbox and inbox on top of P12-T04, document
management extending the P12-T03/T11 document store, and a local file connector
reusing the P12-T12 file-workspace contracts once [PR #327](https://github.com/Starck43/social-media-ai/pull/327)
is integrated. P12 tasks keep their own scope and status.
