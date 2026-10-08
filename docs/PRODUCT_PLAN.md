# Universal AI Assistant: product plan

## Status and scope

Product direction proposed on 2026-10-09 after reading documentation on dev at `4b57c142daae332bf128ba252ccf4272491ff011`. This is a documentation-only plan, not a claim that the capabilities below are implemented, production-ready or legally certified. Keep the repository name `social-media-ai` and the product name **AI Assistant**; a technical rename is a separate decision.

The assistant helps people turn information distributed across public sources and authorized private systems into evidence-backed decisions and controlled business actions. Social monitoring is the first vertical, not the boundary of the product.

## Universal workflow

**Goal → authorized sources → collect/search → normalize and verify → analyze/calculate → proposed result/action → approval when required → deliver/execute → audit and follow-up.**

A user defines the outcome, scope, schedule, recipient, budget and allowed actions. Reusable templates supply defaults; advanced users can adjust them. One-off questions and recurring processes use the same capability contracts. An LLM can propose a bounded plan, but server-side authorization, scheduling, validation, budgets and approvals decide what may execute.

Keep the current separation: `AgentScenario` = analysis methodology; `AgentTask` = schedule, sources, reaction and targets; grouping = a read-time projection. A future multi-step workflow definition must not be hidden inside prompt text or overloaded onto `AgentScenario`.

## Capability areas

### 1. Research, monitoring and intelligence

- Search and monitor selected websites, social networks, messaging sources and video platforms such as YouTube and VK Video, subject to actual connector capabilities.
- Support targeted questions, incremental monitoring, period comparisons and alerts. Obtain transcripts/subtitles only where lawful access exists; distinguish a transcript analysis from a full audiovisual analysis.
- Produce digests with findings, metrics, source links, dates, coverage, missing sources, limitations and suggested next steps. Do not promise complete internet coverage or infer unavailable engagement metrics.
- Target delivery: application, email and authorized channels on a schedule or on demand. Existing messenger delivery remains the starting point; email and durable in-app report delivery require implementation.

### 2. Search across personal and business knowledge

- Federated search across user-selected computer folders, PDF/Word/Excel/text files, email and connected business applications.
- Extract relevant passages with file/page/sheet/cell/message references. Track versions, freshness and access rights; support OCR when needed and make extraction failures visible.
- Local access requires an explicitly installed, paired local connector. A hosted agent cannot inspect a computer merely because its user logged in.
- Default to narrow scopes and minimal transfer. Do not upload an entire disk, index system/secret folders or treat retrieved instructions as authority.

### 3. Unified communication and relationship context

- A single conversation workspace links a verified contact to channel-specific threads, identities, messages and files; original systems remain authoritative.
- Retrieve relevant history, suggest a reply and recommend a channel based on verified identity, recent conversation, availability and preference. Ambiguous recipients or routes require a choice.
- Import authorized CRM contacts, including Bitrix24 as a candidate adapter. Propose social connection invitations only where an official API supports them and applicable consent/platform policies permit them; no arbitrary bulk invitations.
- Track birthdays, anniversaries and commitments. Suggest advance reminders and greeting drafts using available prior greetings to avoid repetition; absence of history must be disclosed.
- User-selected drafts may enter a scheduled outbox after approval. Recheck contact, permission, consent, timezone and expiry before sending; cancellation must be possible.

### 4. Follow-up, correspondence audit and records hygiene

- Periodically identify unanswered requests, promised dates, expiring quotations, missing documents and supplier price-list refreshes from authorized mail, messages and notes.
- Turn extracted dates and obligations into proposed reminders with evidence and uncertainty; resolve ambiguous dates/timezones before scheduling.
- Suggest archive/deletion candidates using explicit retention rules. Preview affected records, obtain approval and prefer reversible archive; never silently delete correspondence or records under a legal hold.
- Keep completed reminders, snoozes and acknowledgements to prevent repeated noise. Escalation policies and quiet hours are configurable.

### 5. Commercial preparation, calculations and case management

- Compare product/service offers from authorized catalogs, price lists, websites, CRM/ERP and documents; 1C and Bitrix24 are integration examples, not required dependencies.
- Normalize currency, VAT, pack size, units, delivery cost, availability and quote validity. Preserve source and retrieval time; missing prices are not zero.
- Calculate quantities, unit prices and cost per area using deterministic decimal arithmetic and explicit domain rules, not an LLM's mental arithmetic.
- For paint: required liters = area × coats ÷ coverage per liter × (1 + waste rate); round required packs up by pack volume; total cost = packs × pack price. Keep material-only cost separate from labor/shipping. Coverage assumptions and substrate conditions must be editable.
- For wallpaper: use wall dimensions, roll width/length, pattern repeat and cutting losses; a simple price/roll-area comparison is not a sufficient purchasing calculation.
- Draft a request/quotation or bill of materials from client requirements. Track alternatives, supplier terms, versions, approvals and delivery documents in a case until completion.
- A case is universal: customer order, procurement, research engagement, support request or another configurable business process. Domain templates specify stages, required fields and formulas; the core is not a construction-materials application.

### 6. Documents, remote approval and formal correspondence

- Draft business letters, offers, claims and responses using verified case facts, attachments and versioned templates. Mark unsupported claims and missing terms; require human review of legal/financial statements.
- Send a specific document version for remote approval with authenticated recipient, expiry and evidence of the decision. A later edit invalidates the prior approval.
- Distinguish a product approval click from a legally recognized electronic signature. Legal effect depends on jurisdiction, parties' agreement, document type and signature provider; a drawn signature or LLM output is not certification.
- Integrate an appropriate signature provider only after legal/security review. Track delivery, approval/rejection, signature status and evidence separately.

## Current foundation versus planned capabilities

| Area | Documented foundation | Expansion and remaining gates |
| --- | --- | --- |
| Collection | VK API/user authorization; Telegram Bot API updates and MTProto history | Web, YouTube, VK Video, mail and business adapters are not promised as implemented; MAX transport is not MAX collection |
| Analysis | Scenario-based structured analysis, aggregates and source navigation | Evidence-backed cross-source research and domain extraction need distinct contracts |
| Scheduling | PostgreSQL-backed schedules and jobs | Durable workflow steps, fairness, leases and controlled side effects need validation/design |
| Delivery | Telegram/MAX digest and workspace notifications | Per-target retries/concurrency remain open; email and report inbox are future work |
| Agent | Messenger/web chat, tools, KV memory and feedback | Unified customer inbox, verified identities and business cases are future work |
| Configuration | Encrypted credentials, tenant scopes and DB-configured LLM fleet | Current identity/budget/lifecycle gaps still require the readiness gates |

This table reflects documentation, not a fresh full-code audit. Detailed operational contracts live in the reference docs; the historical readiness review must not be interpreted as a current green release gate.

## Delivery sequence and acceptance

Follow [ROADMAP_INTEGRATED.md](ROADMAP_INTEGRATED.md) and [BUSINESS_PRODUCTION_READINESS.md](BUSINESS_PRODUCTION_READINESS.md) for release safety before expanding capabilities. The phases below describe product expansion, not calendar promises.

| Phase | Bounded outcome | Dependencies and exit evidence |
| --- | --- | --- |
| Foundation | Trustworthy invited monitoring pilot | Tenant/identity isolation, every-call budget policy, retry-safe delivery, backups/restore, honest outcomes and retention; existing release gates remain binding |
| Research | One additional authorized source and one evidence-backed report journey | Connector contract, permissions, collection coverage, quality fixtures, incremental sync; add web/video/email individually, not all at once |
| Knowledge | Search one approved local folder and one connected mailbox | Paired local connector, ACL-aware search, extraction/citation fixtures, deletion/revocation and offline behavior verified |
| Communication | Verified contact + unified threads + human-approved outbox | Identity reconciliation, per-channel send capability, attachment checks, actor-bound approvals, retry/ambiguous-send tests |
| Follow-up | Commitment/birthday reminders and read-only correspondence audit | Timezone/date confirmation, available-history disclosure, consent/quiet hours, cancellation and duplicate suppression |
| Cases | One end-to-end case template with deterministic calculations and versioned documents | Select one business pilot; catalog mapping, arithmetic tests, supplier freshness, audit trail and rollback/reconciliation for external writes |
| Approval | Remote approval for a fixed document version; signature adapter only when justified | Recipient authentication, immutable document hash/version, expiry/replay tests and jurisdiction-specific legal review |
| Scale | Repeatable B2B service, then self-service where demanded | Capacity/economics evidence, tenant fairness, upgrade/restore, support ownership; subscriptions and resilient infrastructure are separate packages |

Suggested first vertical remains authorized monitoring → analytical digest. The next vertical should be selected from actual pilot demand, not by implementing every idea simultaneously. Start new integrations read-only; permit external writes only after their safety contract passes.

## Quality, control and user experience

The main interaction is outcome-driven: explain the need → confirm a compact plan → receive the result. Review screens make sources, estimated spend, destinations, schedule, access scopes and risky actions visible. A single approval may authorize a bounded schedule, not every future action with unrestricted authority.

Measure first useful result, successful workflow completion, factual/citation coverage, retrieval precision and missed relevant items, source freshness, reminder usefulness, accepted-versus-edited drafts, delivery success/duplication, deterministic calculation correctness, cost per completed workflow and time saved. Establish representative datasets and measured targets before advertising guarantees.

Human control includes pause, revoke connector, edit scope, cancel scheduled send, correct memory, export/delete eligible data and inspect action history. No unattended messaging, destructive cleanup or contract approval by default.

## Decisions before each implementation package

- Choose pilot user/job and minimum supported source/channel/document set.
- Agree deployment and data geography, connector scopes, retention and local/cloud transfer policy.
- Approve proposed schema/API changes, role matrix, spend and workload limits.
- Specify acceptance fixtures, external failure behavior and recovery procedure.
- For messages/marketing/signatures, establish recipient consent and applicable platform/legal rules.
- Use a small branch from fresh dev, recheck overlapping user changes and test only isolated data. This documentation commit is explicitly requested on dev; it does not change the default implementation branch policy.

See [ASSISTANT_ARCHITECTURE.md](ASSISTANT_ARCHITECTURE.md) for extension boundaries and [design/vision.md](design/vision.md) for the concise product vision.
