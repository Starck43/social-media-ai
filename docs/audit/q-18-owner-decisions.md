# Q-18 — Owner decisions for business modules P13–P19

Closed 2026-11-04. Raised by [P13-T01 accepted](../audit/p13-t01.md) while adapting the
owner's TZ; request: https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f

All nine decisions are answered, so the ROADMAP row is removed and this note is
the history. The short reference stays in
[BUSINESS_MODULES → Owner decisions](../product/BUSINESS_MODULES.md#owner-decisions).

Each task card names its decision by ID and date where the answer constrains the
build; a card is not blocked on a decision any more.

## Answers

- D1 — **answered (owner, 2026-11-04)**: P13 + P14 in `P2`; P15–P19 in `P3`.
  ROADMAP row order follows TZ order within each band. This changes queue order
  only, not scope: P14 tasks that used to sit in `P3` (T05, T06) move up to `P2`.
- D2 — **answered (owner, 2026-11-04)**: OAuth 2.0 local application per portal
  (user-delegated access, scopes `crm`); an incoming webhook stays an admin-only
  alternative for a single portal, never a second equal path.
- D3 — **answered (owner, 2026-11-04)**: metadata only for the start. Search
  covers subject, sender, dates, channels and document metadata; message bodies
  and document content are not stored for search, so no content cache and no
  PostgreSQL full-text index are built. The `tsvector`/language-configuration
  question (audit F-4 of [P13-T01](p13-t01.md)) therefore stays undecided and
  unbuilt — enabling content search later is a new decision backed by a
  measurement, not an already-approved option.
- D4 — **answered (owner, 2026-11-04)**: explicit consent per calendar
  connection plus a per-event audit record; creating and moving under that
  consent, while conflicting or deleting changes need per-item approval.
- D5 — **answered (owner, 2026-11-04)**: published HTTPS endpoint with an
  allowlisted host first; the P18 connector is the documented fallback, not a
  prerequisite.
- D6 — **answered (owner, 2026-11-04)**: Python + PyInstaller, and the pilot and
  early builds ship **unsigned**; Windows code signing and macOS notarization are
  deferred to a separate follow-up once the pilot has real users, not part of
  P18-T03/T07 acceptance. Consequences accepted with this choice: SmartScreen
  warns on first run and Gatekeeper requires an explicit "Open anyway" on macOS,
  so install instructions must say so, and auto-update keeps a signed manifest
  even while the binaries themselves are unsigned (the manifest protects the
  update channel, not the publisher identity).
- D7 — **answered (owner, 2026-11-04)**: email and bot channels only, explicit
  opt-in record with channel and purpose, unsubscribe link/command in every
  message; no MTProto bulk sending. Legal review of the consent wording is still
  recommended before the first live campaign, but does not block the build.
- D8 — **answered (owner, 2026-11-03)**: separate `PII_ENCRYPTION_KEYS` as an
  ordered `MultiFernet` list (first key active, older keys decrypt only) and a
  separate `PII_INDEX_KEY`; neither reuses `CREDENTIALS_KEY`. `PII_INDEX_KEY` is
  kept stable — the blind index is not rotated in P13-T02; rotating it is a
  separate, separately approved task with its own index rebuild. Accepted default
  of [BUSINESS_MODULES](../product/BUSINESS_MODULES.md) D8.
- D9 — **answered (owner, 2026-11-04)**: PostgreSQL plus the existing private
  storage from P12-T12; S3-compatible storage only after a measured need.
  **Clarified (owner, 2026-11-10):** S3 belongs in the project as a connector for
  the future, not as a postponed storage choice — other people do keep files
  there. "Only after a measured need" constrains when S3 becomes the *default*
  byte store, never whether an S3 connection can exist in the catalog. Rendering
  and outbox work (P15-T14, P19-T05) must not assume PostgreSQL is the only byte
  source: the reference carries a location the reader resolves, so a later S3
  source does not change the outbox or approval contract.
- D9a — **answered (owner, 2026-11-10)**: rendered DOCX/PDF attachment bytes are
  **not** stored in PostgreSQL; they live in a transient filesystem spool from
  render until dispatch plus a TTL and are then deleted. Long-term storage is the
  tenant's own cloud or local store. Limits are configurable with defaults:
  `ATTACHMENT_MAX_FILE_BYTES` 5 MiB per file, `ATTACHMENT_MAX_TOTAL_BYTES` 10 MiB
  per message, `ATTACHMENT_SPOOL_TTL_SECONDS` 900, `ATTACHMENT_STORAGE_DIR` the
  system temp directory. Rationale: thousands of tenants would bloat the database
  for bytes needed for minutes. Implementation and the restart guarantee are in
  [CONFIGURATION](../platform/CONFIGURATION.md) and `app/services/attachments/`.

## Authorizations that outlive the register

Closing the register does not mean every question is asked and answered again.
These stand as authorizations for future tasks:

- **Tenant isolation exception for platform-wide technical rows (2026-11-04).**
  A model may omit `tenant_id` only when the row holds no customer data and is
  global by nature (one provider account's remaining budget, one connector's
  health, one cache's expiry); the row names the reason in its docstring and a
  subscriber never sees it. Anything carrying customer data stays tenant-scoped.
  Recorded in [BUSINESS_MODULES](../product/BUSINESS_MODULES.md); a task that needs the
  exception says so at planning time, not at review.
- **`test_schema` migrations (standing rule, not a decision).** Reviewed Alembic
  migrations on the isolated schema need no repeated permission
  ([database rules](../process/COLLABORATION.md#database)); separate authorization applies
  to the **public** schema only.
- **D6 signing deferral** and **D7 consent wording**: each is deferred to a named
  later moment, not approved. Reaching that moment needs a new decision.

## Consequences worth remembering

- D1 moved P14-T05 and P14-T06 from `P3` to `P2` in ROADMAP (queue order only).
- D3 cancels the content cache and the full-text index that earlier drafts of
  P15-T02/T04 assumed. The `tsvector`/language question from P13-T01 audit F-4
  stays open and unbuilt; enabling content search needs a new, measured decision.
- D6 leaves the connector unsigned for the pilot. Windows SmartScreen and macOS
  Gatekeeper warnings are expected and must be documented in the install guide;
  signing and notarization are a follow-up task, not part of P18-T07 acceptance.
- D7's consent wording still needs a legal review before the first live campaign;
  the reviewer is not blocked by it.
- D9a supersedes an earlier record. Commit `bb702f45` ("Record owner artifact
  storage decision and P19-T05 DDL request") had recorded the opposite: rendered
  bytes stored encrypted in PostgreSQL with a new hash-read service and a
  `document_export_artifacts` table. The owner re-answered the same question on
  2026-11-10 and chose the transient spool. That DDL request is withdrawn and gets
  no revision number; both cards carry a SUPERSEDED banner. Kept in history so the
  discarded design stays traceable — it is not an open alternative.
