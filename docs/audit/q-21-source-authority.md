# Q-21 — External sources are the owner of what they decide (2026-11-05)

Owner rule 2026-11-05, recorded before the planner and calendar stacks are claimed:
whatever an external service decides stays with that service; we read it and keep a
reference plus what is genuinely ours.

## Answers

- D24 — **answered (owner, 2026-11-05): the source decides, we reference.** The
  rule has three parts and all three are binding.

  **1. A decision taken by the source is not copied.** We store the identifier of the
  object in the source, the link to it, and the few stable facts we need to find it
  again. No second event table beside a CRM, no duplicate schedule beside a calendar,
  no mirrored status beside a task system. If the source can answer a question, we ask
  the source; if we cannot ask it, we say we cannot, and do not replace its answer with
  ours.

  **2. A decision we took is stored by us.** The text of a greeting draft, our own
  task record, who inside the workspace may see something, what we already showed the
  user — none of that belongs to the source and none of it is re-read from it. Our
  tables hold our decisions; the source holds its own.

  **3. A reference must survive the source being unreachable.** A connection failure
  degrades to "the source is unavailable", never to an empty screen presented as the
  truth, and never to a silent guess. This is deliberately **not** a content cache:
  [D11](q-19-messaging-decisions.md) still forbids storing bodies, attachments and
  copies of source content. What survives is the reference and the last fact we learned
  from it, marked with when we learned it.

- D25 — **answered (owner, 2026-11-05): orchestrate, do not invent a second copy of
  the model.** People already have tasks and calendars in several services, and one
  workspace may connect more than one. We do not build a task system or a calendar of
  our own; we build **one registry of every connected source** and an orchestration
  layer over it. This **modifies part 1 of D24**: the identifier of an object is stored
  once, in a source registry that every service shares, not inside each module's own
  table.

## Consequences worth remembering for D25

- **`calendar_events` and `planner_tasks` must not each grow their own external-id
  scheme.** The uniqueness is already on `(tenant_id, connection_id, external_id)` —
  the identifier lives in the connection's source, so it moves into one registry of
  source objects, and a module keeps only what it decided about the object.

- **One object, several sources is the normal case, not an exception.** A meeting may
  exist in Google Calendar and in a client's Bitrix24; a task may be in our planner and
  in a Yandex calendar. The registry records which source is authoritative for which
  field, per object, and the merge is read-time. Never auto-merge two source objects:
  suggestion first, a human decides, exactly as [P15-T03](../tasks/p15-t03.md) settles
  contacts.

- **Per-connection consent is not shared** ([D4](q-18-owner-decisions.md)). Connecting a
  second calendar does not extend the first connection's consent, and a conflict in one
  provider is approved per item, not globally.

- **The orchestration layer owns scheduling, not content.** It answers "what is on the
  agenda", "which source is late", "which two objects look like the same thing". It
  does not compute recurrence when the source does, and does not store a merged event.

- **Outbound writes become the hard part.** Two-way sync means writing into a source
  we do not own; the approval, `If-Match` and conflict rules already in
  [P16-T03](../tasks/p16-t03.md) apply unchanged and must not be relaxed because a
  second provider was added.

## Where this bites first

| Task | The source decides | We keep | Forbidden |
| --- | --- | --- | --- |
| [P16-T01](../tasks/p16-t01.md) planner tasks | nothing yet — no external task system is connected | the task itself, its recurrence, its status, its visibility | a mirrored copy of a Bitrix24 task or calendar event |
| [P16-T03](../tasks/p16-t03.md)/[P16-T04](../tasks/p16-t04.md) calendar | event times, recurrence, attendees, which calendar | our link, our per-connection consent ([D4](q-18-owner-decisions.md)), our per-event audit | a second calendar model that re-computes recurrence once the connection exists |
| [P14-T01..T04](p13-t04-p14-t04-business-crm.md) greetings | birthdays/anniversaries and contact facts from Bitrix24 | the draft text and the once-per-year ledger | a copy of the contact's events |
| [P14-T02](p13-t04-p14-t04-business-crm.md) CRM sync | entity fields, deletion, ownership | the link and the sync position | a third party's field values served from our copy when the portal answers |
| P16-T01/T03 together | which source owns a field of a shared object | the registry entry and the read-time merge | a merged event or task stored as a new object |
| [P15-T02](../tasks/p15-t02.md) unified messages | the message itself and its body | metadata, original URL, content hash | storing bodies or attachments (D11) |

## Consequences worth remembering

- **A disappearing source is not a deletion.** When a record vanishes upstream we mark
  the reference unavailable and keep it; only an explicit source event (or the owner's
  offboarding) removes anything. This extends the rule already used for messages.

- **Recurrence moves once, and the storage decides whether it is cheap.** Until a
  calendar connection exists, the planner computes its own RRULE subset. The moment
  [P16-T03](../tasks/p16-t03.md) lands, a task connected to a calendar event reads the
  recurrence from the calendar and stops computing it. That is a call-site change
  **only if** the planner never expands a rule into pre-created occurrences and carries
  a `recurrence_source` column from its first migration
  ([P16-T01](../tasks/p16-t01.md), steps 2, 2a, 2b). Expanding a rule into a year of
  rows would turn the hand-over into a data migration with a reconciliation on top.

- **The reference is not a licence to read.** Holding the identifier of an upstream
  object authorizes nothing; the read still re-checks the connection, the actor and the
  workspace at execution, exactly as any other outbound call does.

- **No "useful copy" of the source.** A local counter, a mirror table or a
  denormalised copy of source fields is the same defect at smaller scale; it becomes a
  second answer to a question the source already answers.

- **This is the general form of D11 and D20.** D11 keeps message bodies out of the
  database, D20 keeps notification policy in one service, D24 says why: the owner of a
  fact is the system that decides it, and we are the owner only of what we decide.
