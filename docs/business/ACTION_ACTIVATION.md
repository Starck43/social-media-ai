# Capability-specific external action activation

P9-T01 selects **`telegram.bound_message.v1`** from the existing transport
inventory: one manually reviewed plain-text message through
`TelegramChannel.send_part` / the official [Bot API sendMessage method](https://core.telegram.org/bots/api#sendmessage).
It does not enable VK writes, the generic social executor, automatic actions,
private/group messaging, digest delivery, media, HTML, splitting or retries.
The P9-T08 ledger and generic `action_send` preview/simulation APIs remain separate.

## Release controls and kill switches

The capability is **off by default**. An operator must explicitly configure both:

```dotenv
P9_TELEGRAM_MESSAGE_ENABLED=1
P9_TELEGRAM_MESSAGE_BINDINGS=12:34,15:2
```

Pairs are exact positive `workspace_id:tenant_channel_id` values, comma-separated
with no whitespace or wildcards. Empty/malformed lists fail closed. Every process
must use the same deployment configuration; changing an environment variable in
one process does not update other processes. Disable the master switch or remove
the exact pair in every serving process to stop new admissions. Deactivate the
binding for a database-backed per-target stop. Checks run on proposal, reservation
and immediately before HTTP; final database authority/target locks are held over
the single HTTP call. A revoke waits for an already admitted call, and no switch
can recall a request already issued. This is not a promise of instantaneous
cross-process environment propagation or remote-effect rollback.

An allowlist is an operator rollout decision, not workspace authorization. It
cannot substitute for the fresh owner, account, membership, role, workspace and
target checks. A configured bot token alone grants no workspace permission.
Do not enable any production pair from this PR or its test results automatically.

## User and authorization contract

1. An active **workspace owner** with `botaction.view` and `botaction.update`
   proposes one message in `/app/actions`. The server resolves an existing active
   Telegram binding with `kind=channel` in that workspace; only canonical numeric
   channel IDs are allowed. No arbitrary destination, token, payer or owner override.
   Digest-target flags and a Source are not publication authority.
2. The proposal seals binding ID, exact destination, text and a nonsecret
   fingerprint of the configured bot token/API endpoint, alongside mode/capability
   and the ledger context. Token/endpoint rotation requires a new reviewed intent.
   Only `https://api.telegram.org` is supported by this capability; alternate,
   insecure or self-hosted endpoints fail closed without transmitting credentials.
   The raw credential is never stored in an action, result, history or response.
3. Existing review displays that intent. CSRF POST approval binds the real User
   and web membership, reviewed hash and expiry (default 15 minutes, existing
   maximum one hour). Approval itself never sends.
4. A **separate CSRF POST** `/{id}/execute-telegram`, carrying the reviewed hash,
   admits only the same approving owner and unexpired, unchanged intent. Current
   database authorization and the target/credential/release policy are checked
   again before HTTP. Platform-admin/operator bypass, another owner, manager,
   revoked account/membership/workspace, changed binding or credential cannot send.
   GET and the `action_send` agent tool never publish.

Text is nonblank, NUL-free, at most 4096 UTF-16 code units, sent with
`parse_mode=None`. This is a manual, explicitly approved free Bot API operation:
no LLM generation, paid provider, collection entitlement, personal payer consent
or auto-action plan entitlement is acquired or expanded. Task/source/scenario
references are forbidden for this capability; unattended scheduling is out of scope.

## Idempotency, audit and uncertain effects

- The existing database unique `(tenant_id, idempotency_key)` identifies one
  exact sealed intent across **all** statuses. Changed intent with the same key
  fails. Reopening/reposting a form does not create another action.
- A row-locked, own-transaction reservation commits `CLAIMED`, one attempt,
  captured actor, payload hash and an in-flight marker **before** HTTP. Only the
  winning request receives the in-memory send descriptor; neither a request token
  nor the generic simulation API can dispatch/replay a reservation.
- One transport call, no HTTP retries (including 429), no split or fallback.
  Repeating execution for CLAIMED or any terminal state never sends again.
  Telegram `sendMessage` has no provider idempotency parameter in this contract:
  this is an at-most-one application dispatch fence, **not exactly-once delivery**.
- A verified message ID records EXECUTED; explicit transport rejection or a proven
  pre-HTTP block records FAILED. Timeout, malformed response or other uncertain
  transport outcome records UNKNOWN. Only fixed safe fields/message ID persist;
  provider bodies and exceptions do not. A factual receipt may be recorded after
  actor revocation/deletion because doing so authorizes no new effect; captured
  actor IDs remain in transition history even when membership FKs become NULL.
- Cancellation, process death or unconfirmed commit/outcome persistence preserves
  the durable fence. A fresh active owner may mark an expired reservation UNKNOWN
  through `/{id}/reconcile-telegram`, even if the original actor/binding was revoked.
  It cannot reset or resend the action. Reconciliation with Telegram and any new
  proposal/key require an explicit human review; a new key is not automatic recovery.
- P9-T08 transition history keeps actor IDs/time/hash after reference deletion;
  action result retains safe outcome/receipt. It is manager-maintained evidence,
  not a tamper-proof audit log. Existing tenant deletion/retention policy still applies.

## Verification and release claims

Focused checks cover real PostgreSQL reservation/idempotency/concurrency,
revocation and receipt persistence; the real web middleware/CSRF/review path;
policy validation; and mocked single-request transport outcomes. No migration is
needed. The task PR records exact revisions, commands and limitations.

Passing sandbox checks certifies only this default-off code path under mocked
Telegram I/O. It does not certify bot posting rights in a real channel, live
Telegram availability, production readiness, a general outbound-action engine or
any other transport/path. Independent audit and owner integration/rollout choices
remain separate. Before live rollout, the owner must verify the exact bot/channel
permissions and perform a deliberately authorized smoke send; never use test
approval or an environment flag as permission to post externally.
