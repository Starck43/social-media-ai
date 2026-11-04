# Q-20 — Notification surface before the planner and calendar (2026-11-05)

Raised by the local auditor while P14-T09 was in review. Two upcoming stacks would
each have invented their own answer: the notification preferences and messenger
already in the tree, and the user-facing notifications P16-T02 wants to add.

## Answers

- D18 — **answered (owner, 2026-11-05): one notification surface, and no second
  sender.** The existing notification service is the only owner of "tell a person
  something in the product": `Notification`, `app/services/notifications/`,
  `preferences.py` (per-user `connections`, `tasks`, `popup`, quiet hours) and
  `messenger.py` (Telegram and VK to an explicitly bound workspace recipient).
  P16-T02 does **not** build a parallel delivery path: it schedules reminders and
  calls the existing surface. Anything a sender adds — a new channel, retries, a
  delivery ledger — is added there, once, for every caller.

- D19 — **answered (owner, 2026-11-05): a person is never notified through the outbox.**
  Notifying an internal user is not messaging a client. It skips outbox approval,
  consent and unsubscribe, and it must never reuse `contact_consents`: a user of the
  workspace is not a marketing contact. The boundary is the recipient, not the
  transport.

- D20 — **answered (owner, 2026-11-05): quiet hours have one implementation.** Today
  `app/services/communications` owns quiet-hours arithmetic and `preferences.py` owns
  the per-user window; a new reminder scheduler that re-implements either would give
  the workspace two different definitions of "night". P16-T02 reuses both, and a
  change to the window is a change to `preferences.py`, not to the reminder.

- D21 — **answered (owner, 2026-11-05): P12-T15 gets no folder-management of its own.**
  `P12-T12` owns the file workspace, originals-first navigation and safe file actions,
  including whatever it decides about folder grants. `P12-T15` is a future request
  that must be re-scoped against the accepted P12-T12 before anyone builds it; it may
  extend that management, never start a second one.

## Consequences worth remembering

- **What P16-T02 is allowed to own:** `planner_reminders` (task, remind at, channel,
  state, sent at, snoozed until), the runtime job that claims due reminders, snooze
  and reschedule actions, and the AI suggestion as a non-binding proposal. It is not
  allowed to own a transport, a quiet-hours rule or a preference schema.

- **The notification channel list is closed by `messenger.py`** — Telegram and VK
  today. A reminder on "email" or "MAX" is not a plan until the sender capability
  exists ([P15-T06](../tasks/p15-t06.md)); the card must say what happens for a
  channel that is configured but not implemented, and the answer cannot be silence.

- **Two later tasks will want the same answers.** Notifications for planner
  ([P16-T02](../tasks/p16-t02.md)) and calendar reminders
  ([P16-T03](../tasks/p16-t03.md)) are the same mechanism at two call sites; the
  second one must not repeat D18.

- **D19 protects the consent table.** Reusing `contact_consents` for staff would
  require a consent record with a "basis" nobody can produce, and would make an
  internal reminder legally look like a marketing message.

- **P12-T15 is not cancelled.** It stays `TODO`; the decision only says that the
  thing it would build already has an owner.

## Open, deliberately not answered here

- Whether a reminder that falls into quiet hours **defers** or **drops**. The owner
  answers it when P16-T02 is claimed; until then the card must not assume.
