# Q-19 — Owner decisions before the messaging stack (2026-11-04)

Raised by the local auditor while the P15 inbox stack and P14-T09 were in flight.
The questions are about whichever agent lands **sending** (P15-T05/T06, P14-T05/T06,
P15-T08) first: each of them would otherwise invent a policy the others then copy.

Answers are recorded here and referenced by ID from the task cards; the ROADMAP row
is removed once every decision is answered.

## Answers

- D10 — **answered (owner, 2026-11-04): split the two numbering pairs.**
  `P15-T05` (persistent outbox and scheduling) and `P15-T06` (channel senders:
  SMTP, Telegram bot, MAX bot) are the **technical transport layer**. `P14-T05`
  (approved greeting delivery) and `P14-T06` (batch messaging with consent and
  unsubscribe) are **business features on top of it** and must not build a second
  outbox or a second sender. Consequence recorded in consequence section below.

- D11 — **answered (owner, 2026-11-04): D3 stands, with no content storage at all.**
  Neither message bodies nor attachments are persisted for search or display; the
  inbox shows metadata only and, where the source allows it, opens the original in
  its own system. No `message_content_cache`, no `tsvector`, no language
  configuration, no "small cache of last N bodies". The P13-T01 audit F-4 question
  stays closed as "not built". Opening it again needs a new decision backed by a
  measurement of real use, not a build-time argument.

- D12 — **answered (owner, 2026-11-04): send through the customer's own mail
  account, never through ours.** SMTP uses the same account credentials the
  workspace already stored for IMAP (implicit TLS 465, application password),
  `From:` is that account, and SPF/DKIM/DMARC are the mail provider's and the
  customer's domain responsibility — we do not relay from a shared installation
  domain, do not maintain our own MTA, and do not own sender reputation. A refused
  or bounced send is reported as the provider's outcome, not retried past the
  provider's own limits.

- D13 — **answered (owner, 2026-11-04): mail networking is an outbound consumer,
  not a bypass.** SMTP and IMAP resolve and validate their destination through the
  [P14-T09](../archive/tasks/p14-t09.md) seam exactly like every other connector: no connector keeps
  its own allowlist, DNS call or `is_global` shortcut. The pinned TLS policy in
  `app/integrations/mail_check.py` stays the transport detail; the seam decides
  **where** we may connect. Private-network mail servers therefore need an
  installation-level trusted entry like any client-hosted portal.

- D14 — **answered (owner, 2026-11-04): sending rights are a permission
  registration, not a new authorization model.** The existing catalog
  (`app/core/access_catalog.py`, `has_permission`, `member_decision`) already answers
  "which action on which subsystem" for API, web and managers alike; what is missing
  is that it has **no communications resources at all**. Register
  `comms.message` (`VIEW CREATE UPDATE DELETE`) and `comms.outbox`
  (`VIEW CREATE APPROVE CONFIGURE`, no `CREATE` — nothing is sent without an approved
  item, and `APPROVE` is the sender confirming what is on screen, not a second
  person). Both workspace-scoped. Role presets: `owner`/`admin` all actions,
  `manager` all but `DELETE`, `analyst` and `viewer` read only.
  Special consequence: with the current catalog, `contact.VIEW` is what scopes a
  contact to a `Bitrix24Contact` row, and a manager without that permission sees the
  `Bitrix24Contact` link **plus its contact** as an unresolved record in Bitrix24,
  while a manager with `VIEW` sees the same thing merged. See the consequence below.

- D15 — **answered (owner, 2026-11-04): an explicitly recorded opt-in, and a reply
  is not one.** A message in the dialog is exempt because the person wrote to us.
  Greeting and marketing require a recorded `contact_consents` row for that channel
  and purpose, granted and not withdrawn. A channel merely present on a
  `Bitrix24Contact` card, or a phone number imported from CRM, is **not** consent:
  CRM supplies candidates, consent is added separately.

- D16 — **answered (owner, 2026-11-04): one click, no login.** Every message carries
  an unsubscribe link/command (already promised by D7). The click writes a withdrawal
  for that channel and purpose and the contact drops out of every future campaign
  immediately; the person sees a confirmation without signing in.

- D17 — **answered (owner, 2026-11-04): a manager may record consent manually, and
  it is never silent.** `ContactManager.record_consent` already refuses a consent
  without a granted or withdrawn timestamp and requires basis and evidence; a manual
  record keeps that requirement and additionally writes an audit row naming the
  manager.

## Consequences worth remembering

- **D10 renumbering:** the four rows keep their IDs — no ID is reused for a
  different task, which would corrupt every reference and audit that mentions it.
  Only the *meaning* is fixed: T05/T06 of P15 are the transport, T05/T06 of P14 are
  features. A feature card that needs a transport behaviour changes the transport
  card, it does not fork it.
- **D10 dependency order:** `P15-T06` (senders) → `P15-T05` (outbox) →
  `P14-T05` (greeting delivery) → `P14-T06` (batch) → `P15-T08` (reply from the
  inbox). `P15-T05` currently lists `P15-T06` as its dependency in the phase plan
  while the card lists the reverse; the transport order above is the one to follow,
  and the phase plan is corrected when the transport is claimed.
- **D11 removes a feature that earlier drafts assumed:** "search inside messages"
  is not a P15 feature. If subscribers ask for it, that is a new decision with a
  measurement, a retention rule and a tenant-visible explanation.
- **D12 makes deliverability a customer-side property:** an installation never
  sends mail from a domain it does not own, so no installation-wide SPF record,
  DMARC policy or relay reputation is maintained here. Onboarding must state this,
  and a customer whose domain lacks SPF/DKIM gets a warning, not a workaround.
- **D13 couples mail to P14-T09:** `P15-T06` and `P15-T01` are only complete once
  the seam exists. Whoever lands sending before the seam uses a temporary call-site
  shim that the seam then replaces — never a private resolver.

- **D14 exposes an existing gap that sending makes reachable.** A `Bitrix24Contact`
  row stores the person's identity PII; the contact is scoped by `contact.VIEW` and
  the link by the bitrix resource. So a manager **without** `contact.VIEW` sees the
  same thing (the link plus its contact) as a manager **with** it, but cannot see
  that the two are connected — a record in Bitrix24 that does not look like a
  contact. Replying to and briefing a client from a single thread therefore needs
  both permissions. This is a Bitrix-linkage defect that predates sending and is
  **not fixed here**; recorded so that P14-T05/T06 and [P15-T08](../archive/tasks/p15-t08.md)
  do not invent a per-feature workaround for it.

- **D14 removes a tempting shortcut.** `comms.outbox` gets no `CREATE` action: nothing
  enters the outbox without an approved item, so a future feature cannot "just send"
  by writing a row. `APPROVE` stays the sender confirming content (P12-T04 binding),
  never an approval workflow with a second person.

- **D15 makes CRM imports honest.** An imported base produces candidates, not a
  sendable list. A workspace that expects "just mail everyone in Bitrix24" gets a
  coverage warning at the campaign step, not a silent smaller send.
