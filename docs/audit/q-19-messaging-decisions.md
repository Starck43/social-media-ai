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
  [P14-T09](p14-t09.md) seam exactly like every other connector: no connector keeps
  its own allowlist, DNS call or `is_global` shortcut. The pinned TLS policy in
  `app/integrations/mail_check.py` stays the transport detail; the seam decides
  **where** we may connect. Private-network mail servers therefore need an
  installation-level trusted entry like any client-hosted portal.

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
