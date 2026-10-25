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
| Identity and tenancy | Active-user/permission checks and tenant-scoped manager/session boundaries. | [PR #22](https://github.com/Starck43/social-media-ai/pull/22) |
| PostgreSQL jobs | Claim fencing, renewal, heartbeat, bounded recovery and cancellation evidence. | [#155](https://github.com/Starck43/social-media-ai/pull/155) |
| Agent state | Atomic session history/state, typed memory/output and untrusted-input framing. | [PR #58](https://github.com/Starck43/social-media-ai/pull/58) |
| Atomic confirmation consumption | Actor-bound, locked, single-use confirmation with replay/concurrency guards. | [PR #105](https://github.com/Starck43/social-media-ai/pull/105) |
| Collection and analysis | Tenant-scoped staging, partial-result diagnostics and bounded text/media coverage. | [PR #69](https://github.com/Starck43/social-media-ai/pull/69) |
| Source-aware explicit analysis admission | Staged-input retry and source-bound active-job admission. | [PR #192](https://github.com/Starck43/social-media-ai/pull/192) |
| Telegram admission | Durable L1/L2 admission, cursor/receipt coordination and media placeholders. | [PR #97](https://github.com/Starck43/social-media-ai/pull/97) |
| Platform rate limits | Bounded platform-specific throttling, Retry-After and wait handling. | [PR #143](https://github.com/Starck43/social-media-ai/pull/143) |
| Credential diagnostic privacy | Bounded credential/session statuses without raw secret-bearing exceptions. | [PR #107](https://github.com/Starck43/social-media-ai/pull/107) |
| Data retention | Configurable raw/analysis retention; deployment activation remains separately gated. | [PR #139](https://github.com/Starck43/social-media-ai/pull/139) |
| Digest delivery | Tenant-safe target/part checkpoints and truthful partial outcomes. | [PR #12](https://github.com/Starck43/social-media-ai/pull/12) |
| LLM routing | DB-managed providers/models, model defaults and capability-aware fallback. | [PR #96](https://github.com/Starck43/social-media-ai/pull/96) |
| LLM dispatch receipt storage — DONE, UNWIRED | Immutable tenant/payer-bound preparation receipts and frozen tariffs; storage slice only. | [PR #137](https://github.com/Starck43/social-media-ai/pull/137) |
| LLM outcome evidence storage — DONE, UNWIRED | Append-only receipt-bound outcome and cost observations; storage slice only. | [PR #168](https://github.com/Starck43/social-media-ai/pull/168) |
| Atomic LLM receipt and budget reservation — MERGED, UNWIRED | Transactional payer/day reservation and immutable replay; original unwired package. | [PR #180](https://github.com/Starck43/social-media-ai/pull/180) |
| LLM analyzer journal foundation — DONE | Tenant-bound attempt identity, tariff snapshot, Decimal USD and explicit unknown costs. | [PR #114](https://github.com/Starck43/social-media-ai/pull/114) |
| LLM non-analyzer journal capture — DONE | Separate attempt evidence for fallback chat, model probes and digest summaries. | [PR #163](https://github.com/Starck43/social-media-ai/pull/163) |
| Web and operations | Truthful outcomes, secure cookies, readiness and redacted operational signals. | [PR #27](https://github.com/Starck43/social-media-ai/pull/27) |
| NULL-role display | Unknown roles display no inferred owner level or implicit grant. | [tenancy contract](TENANCY.md#roles-and-permissions) |
| Home pipeline strip | Read-only staged/analysis counts and bounded active-job preview. | `c598b70` |
| Manual digest-send permission parity | Agent/web send both require digestrun.create. | [PR #176](https://github.com/Starck43/social-media-ai/pull/176) |
| Development workflow | Earlier local/remote coordination foundations; current policy is AGENTS/COLLABORATION. | [PR #183](https://github.com/Starck43/social-media-ai/pull/183) |
| Web chat UX | Validated quick replies, confirmation options and visible in-flight feedback. | [PR #171](https://github.com/Starck43/social-media-ai/pull/171) |
| Run-status modal | Per-source progress, window sizes, background mode and scoped cancellation. | [PR #169](https://github.com/Starck43/social-media-ai/pull/169) |
| JWT authentication backend | PyJWT HS256-only signing/verification replaces python-jose. | [PR #174](https://github.com/Starck43/social-media-ai/pull/174) |
| Shared destructive-action confirmation UI | Shared text-only confirmation preserves CSRF and original form semantics. | [PR #175](https://github.com/Starck43/social-media-ai/pull/175) |
| Web stored-text escaping | Autoescaped web templates and text-safe stored-value confirmation rendering. | [PR #178](https://github.com/Starck43/social-media-ai/pull/178) |
| Test-bootstrap evidence protection | Read-only unknown-table/column/UNIQUE drift guards before schema writes. | [PR #179](https://github.com/Starck43/social-media-ai/pull/179) |
| Home overview cleanup | Permission-gated overview, bounded previews and scoped setup/chat links. | [PR #185](https://github.com/Starck43/social-media-ai/pull/185) |
| Shell and workspace attention | Scoped notification state, revocation/CSRF guards and partial-refresh recovery. | [A #188](https://github.com/Starck43/social-media-ai/pull/188) |
| Access-model restructuring | Separate platform/workspace roles, resource permissions and private AI memory. | [ACCESS_MIGRATION.md](ACCESS_MIGRATION.md) |
| Notification display — C browser-local slice | Caller-keyed local display preferences and device quiet hours at that source revision. | [PR #190](https://github.com/Starck43/social-media-ai/pull/190) |
| Settings/access contexts — D bounded slice | Lazy permitted settings tabs, scoped membership controls and explicit workspace context. | [PR #191](https://github.com/Starck43/social-media-ai/pull/191) |

An evidence link certifies only its original scope/SHA; read the full limitations
there before reusing it. Current contracts: [subsystem index](DOCS_INDEX.md).
