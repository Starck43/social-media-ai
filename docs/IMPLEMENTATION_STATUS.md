# Delivered-work index

Optional starting point for auditing completed work; no active priorities or
current release verdicts. Read only the selected area, then its evidence and
current subsystem contract. Historical checks never certify a new revision.

Full pre-compaction evidence and boundaries remain available with
`git show 17fc7894066aad64703ff24d009f2fe8ff82e109:docs/IMPLEMENTATION_STATUS.md`.
New deliveries append one short outcome/evidence row; detailed results belong
in the parent phase archive or PR, not here. Active work: [ROADMAP](../ROADMAP.md).

## Personal LLM catalog and workspace-safe bindings

P3-T93–T96 source accepted at `b34353645fe37de53c97c4d022c05af0044dc8ba`
(#229/#230); source integration is not deployment/migration acceptance.
[Exact execution attribution and limitations](audit/personal-llm-ownership.md).

## Delivered foundations

| Area | Historical delivered slice | Evidence entry |
| --- | --- | --- |
| Identity and tenancy | Fail-closed active-user and permission checks, tenant-scoped model/manager boundaries, tenant-scoped agent-session uniqueness | [PR #22](https://github.com/Starck43/social-media-ai/pull/22) |
| PostgreSQL jobs | Claim fencing, renewal, ownership preflight, periodic heartbeat, cancellation and bounded recovery/admission primitives | [#155](https://github.com/Starck43/social-media-ai/pull/155) |
| Agent state | Atomic session state/history changes, typed memory/output boundaries and untrusted-data framing of modality results in unified-summary prompts. | [PR #58](https://github.com/Starck43/social-media-ai/pull/58) |
| Atomic confirmation consumption | Versioned actor-bound intent, tenant-scoped locked consume of the exact pending snapshot and post-commit single-use authorization; losers,… | [PR #105](https://github.com/Starck43/social-media-ai/pull/105) |
| Collection and analysis | Partial-result diagnostics, tenant-scoped staging, bounded retirement, attachment snapshots and conservative text/media coverage | [PR #69](https://github.com/Starck43/social-media-ai/pull/69) |
| Source-aware explicit analysis admission | Stored-period staged-input retry and shared source-detail analysis rendering; tenant-scoped active-job membership and a short transactional… | [PR #192](https://github.com/Starck43/social-media-ai/pull/192) |
| Telegram admission | Durable L1/L2 admission with cursor/receipt coordination and media placeholders. | [PR #97](https://github.com/Starck43/social-media-ai/pull/97) |
| Platform rate limits | A short platform rate limit gets a bounded pause and retry (VK code 6, HTTP 429 `Retry-After`, Bot API 429 `retry_after`; Telethon waits out… | [PR #143](https://github.com/Starck43/social-media-ai/pull/143) |
| Credential diagnostic privacy | Bounded API credential-test and MTProto check-session failure statuses without provider/exception secret text; success labels and access… | [PR #107](https://github.com/Starck43/social-media-ai/pull/107) |
| Data retention | `DATA_RETENTION_MONTHS` (`app/core/config.py`, `.env.example`; default 1 calendar month, minimum 1) | [PR #139](https://github.com/Starck43/social-media-ai/pull/139) |
| Digest delivery | Per-target/per-part state, snapshot/checkpoint foundations and tenant-safe routing safeguards; legacy-path partial delivery is terminal… | [PR #12](https://github.com/Starck43/social-media-ai/pull/12) |
| LLM routing | DB-managed providers/models, model-level defaults and capability-aware selection/fallback. | [PR #96](https://github.com/Starck43/social-media-ai/pull/96) |
| LLM dispatch receipt storage — DONE, UNWIRED | Immutable tenant-bound preparation receipts with frozen tariff state, UTC preparation day, globally unique dispatch identity, RESTRICT payer… | [PR #137](https://github.com/Starck43/social-media-ai/pull/137) |
| LLM outcome evidence storage — DONE, UNWIRED | Append-only receipt-bound outcome/normalized-price observations with trusted payer scope, explicit unknown/free/price states, immutable… | [PR #168](https://github.com/Starck43/social-media-ai/pull/168) |
| Atomic LLM receipt and budget reservation — MERGED, UNWIRED | One manager-owned transaction locks payer/day, appends receipt-bound immutable reservation and updates explicit priced/undefined/unbounded… | [PR #180](https://github.com/Starck43/social-media-ai/pull/180) |
| LLM analyzer journal foundation — DONE | Tenant-scoped captured stage records before result projection; tariff snapshot, Decimal USD and explicit unknown cost; execution IDs… | [PR #114](https://github.com/Starck43/social-media-ai/pull/114) |
| LLM non-analyzer journal capture — DONE | Every `chat_with_fallback` try (agent chat, learning, reflection, prompt suggestion; unlabelled `chat`), the agent model-test tool and the… | [PR #163](https://github.com/Starck43/social-media-ai/pull/163) |
| Web and operations | Truthful job outcomes, secure cookies outside development, runtime readiness, conservative recovery guidance and redacted `/app/jobs`… | [PR #27](https://github.com/Starck43/social-media-ai/pull/27) |
| NULL-role display | `member_roles.key_of()` returns no level for a NULL role; labels no longer infer owner, without changing stored membership data or grants. | [tenancy contract](TENANCY.md#roles-and-permissions) |
| Home pipeline strip | Staged/analysis counts and a bounded active-job preview are present on `/app/`; no model/provider calls are made by this projection. | `c598b70` |
| Manual digest-send permission parity | Agent and web send require `digestrun.create`; view/update alone cannot authorize a send. | [PR #176](https://github.com/Starck43/social-media-ai/pull/176) |
| Development workflow | Canonical local/remote skills, collaboration rules and compact handoff format; required Kilo launch message precedes flags, and orchestrator… | [PR #183](https://github.com/Starck43/social-media-ai/pull/183) |
| Web chat UX | `/app/chat` offers one-tap quick replies: the agent may end a reply with a trailing `[[options: …]]` line (validated: up to 6 labels, 60… | [PR #171](https://github.com/Starck43/social-media-ai/pull/171) |
| Run-status modal | `/app/tasks` run modal shows real per-source progress for collect/analyze (bounded snapshot in the running job's `result["progress"]`,… | [PR #169](https://github.com/Starck43/social-media-ai/pull/169) |
| JWT authentication backend | Access/refresh tokens remain HS256 JWTs with unchanged claims; signing/verification moved from the unpatched `python-jose` to PyJWT with… | [PR #174](https://github.com/Starck43/social-media-ai/pull/174) |
| Shared destructive-action confirmation UI | One text-only in-app dialog confirms jobs delete/clear/cancel and source/task deletion, including task editor submitter overrides | [PR #175](https://github.com/Starck43/social-media-ai/pull/175) |
| Web stored-text escaping | The separate /app Jinja environment autoescapes HTML/XML and string templates | [PR #178](https://github.com/Starck43/social-media-ai/pull/178) |
| Test-bootstrap evidence protection | Read-only catalog guards refuse unknown live tables, missing existing columns and UNIQUE drift before schema/reset/seed writes; automatic… | [PR #179](https://github.com/Starck43/social-media-ai/pull/179) |
| Home overview cleanup | Compact permission-gated analysis/task overview, one setup step and permitted chat entry; removed KPI/aggregation and command panels | [PR #185](https://github.com/Starck43/social-media-ai/pull/185) |
| Shell and workspace attention | Real ASGI/DB acceptance for Viewer/Manager/owner/operator shell guards, workspace-bound count/list and read parity, hidden… | [A #188](https://github.com/Starck43/social-media-ai/pull/188) |
| Access-model restructuring | Independent platform/workspace role catalogs, stable domain resource permissions, operator-only admin, membership-only workspace UI/API,… | [ACCESS_MIGRATION.md](ACCESS_MIGRATION.md) |
| Notification display — C browser-local slice | Caller-keyed browser-only display preferences for optional connection/task signals, opt-in hint and device-local quiet hours | [PR #190](https://github.com/Starck43/social-media-ai/pull/190) |
| Settings/access contexts — D bounded slice | Selected permitted tabs load only their own data; members receive a limited workspace summary, management needs a concrete-target capability | [PR #191](https://github.com/Starck43/social-media-ai/pull/191) |

An evidence link certifies only its original scope/SHA; read the full limitations
there before reusing it. Current contracts: [subsystem index](DOCS_INDEX.md).
