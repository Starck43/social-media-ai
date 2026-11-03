# P18 — Local file connector (TZ module 5)

[Product direction](../PRODUCT_PLAN.md#business-modules) · [Adapted specification and review](../BUSINESS_MODULES.md) · [ROADMAP](../../ROADMAP.md#p18)

**Phase status: PLANNED** (registered by [P13-T01](../tasks/p13-t01.md); ROADMAP owns task status).
Owner estimate (TZ): 6–8 weeks — not a repository commitment.

**Goal:** An installable desktop connector that indexes user-selected folders locally (including OCR) and answers federated search from the cloud with metadata and snippets only, uploading a file only on explicit request.

## Baseline: done and arriving

- In `dev`: `LOCAL_GATEWAY` descriptor (certificate fingerprint), `local_only`/`company`/`cloud` boundaries with chat refusal for local-only content (P12-T11), local-connector constraints in [PRODUCT_PLAN](../PRODUCT_PLAN.md#extension-contracts).
- Arriving via open PRs: P12-T12 file workspace — private catalog, scans/traversal, bounded text read, line evidence, private storage, admission contract ([PR #327](https://github.com/Starck43/social-media-ai/pull/327)); [P12-T13 contract review (accepted)](../audit/p12-t13.md); P8-T34 Yandex Disk read-only pilot. Folder policies: P12-T15 (TODO).

## Adapted requirements

TZ 5.1–5.5, adapted:

- **Application:** desktop app (D6, R-17), folder selection, local SQLite FTS5 index, local UI on `127.0.0.1` only, authentication by pairing (not a static API key), auto-update on file changes.
- **Indexing:** PDF, DOCX, XLSX, TXT, MD; text extraction; Tesseract OCR; metadata; full-text; incremental.
- **Search:** full-text, filters, snippets, file reference (path, page/sheet), ranking.
- **Security/privacy:** no bulk upload; only metadata/snippets leave the machine; user-selected folders; system/secret exclusions; ACL-aware; index deletion and device revocation.
- **Cloud integration:** search requests delivered over the connector's **outbound** channel (R-05), optional file fetch on user request, version tracking.

## Data model (adapted)

Cloud: `local_connectors`, pairing codes; results reuse P12-T12 catalog/evidence records. Local: SQLite FTS5 index (files, chunks, anchors).

Conventions: integer keys, `TenantScopedMixin`, `settings.DB_SCHEMA`, Alembic revision = next free after rebasing on `dev` (R-01, R-20).

## API (adapted)

Cloud: `POST /api/v1/files/connectors/register`, `GET /api/v1/files/connectors`, `DELETE /api/v1/files/connectors/{id}`, `POST /api/v1/files/search`, connector WebSocket. Connector operations (`health`, `search`, `file.metadata`, `file.fetch`, `index.status`, `index.delete`/rebuild) are protocol messages, not an inbound HTTP API (R-05).

## Acceptance

TZ criteria: install (T07); index selected folders (T03); OCR (T04); full-text search (T05); snippets (T05); files not uploaded (T01/T06); incremental indexing (T03); index deletion (T03/T06); cloud search requests (T02); file reference (T05).

## Plan

1. [P18-T01](../audit/p18-t01.md) — Local connector protocol, pairing and revocation contract · `P3` · `C3` · **ACCEPTED 2026-11-03** (`8b805aaa`, [audit](../audit/p18-t01.md)); P12-T12 increment integrated. The protocol ships in `docs/LOCAL_CONNECTOR_PROTOCOL.md`
2. [P18-T02](../tasks/p18-t02.md) — Cloud connector gateway: registration, device management and search proxy · `P3` · `C4` · depends: [P18-T01](../audit/p18-t01.md), [P13-T05](../tasks/p13-t05.md)
3. [P18-T03](../tasks/p18-t03.md) — Desktop connector core: folder selection, safe paths and incremental index · `P3` · `C5` · depends: [P18-T01](../audit/p18-t01.md)
4. [P18-T04](../tasks/p18-t04.md) — Text extraction and OCR with resource limits · `P3` · `C4` · depends: [P18-T03](../tasks/p18-t03.md)
5. [P18-T05](../tasks/p18-t05.md) — Local search API: ranking, filters, snippets and ACL awareness · `P3` · `C4` · depends: [P18-T04](../tasks/p18-t04.md), P12-T15 (folder policy, optional)
6. [P18-T06](../tasks/p18-t06.md) — Explicit file fetch, version tracking and index deletion · `P3` · `C3` · depends: [P18-T05](../tasks/p18-t05.md), [P18-T02](../tasks/p18-t02.md)
7. [P18-T07](../tasks/p18-t07.md) — Packaging, signing and auto-update for Windows, macOS and Linux · `P3` · `C4` · depends: [P18-T03](../tasks/p18-t03.md)

## Dependencies and gates

[P18-T01](../audit/p18-t01.md) protocol is accepted; an independent security review remains required before any desktop code. P12-T12's delivered increment is integrated (merge `48caea61`). Platforms: Windows, macOS, Linux.
