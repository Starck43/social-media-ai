# Contact core

Task: [P13-T07](tasks/p13-t07.md). This is a tenant-scoped address book, not an
importer, identity reconciliation engine or permission to send a message.

## Data and writes

`Contact`, `ContactIdentity`, `ContactConsent` and `ContactMergeJournal` are
registered through the public model facade. `Contact.objects` uses
`ContactManager` and the shared `with_db_session` transaction owner. Its aggregate
writers are `create`, `update_by_id`, `delete_by_id`, `attach_identity`,
`detach_identity` and `record_consent`. They require the corresponding
`contacts.contact` action and a selected tenant; a global bypass never removes
the aggregate's explicit tenant criterion. Parent rows are locked for writes.
Caller-owned sessions remain caller-owned and must be committed/rolled back by
the caller. Generic raw/bulk writes are not the aggregate's write API.

Every child reference and source connection uses a composite tenant FK. A unique
constraint enforces `(tenant_id, kind, blind_index, connection_id)`; a partial
unique index covers NULL connections so concurrent unbound duplicates cannot
slip through PostgreSQL's NULL semantics. Conflicting attach operations raise
`IntegrityError`; no pre-check is represented as concurrency protection.

Only exact normalized identifiers are searchable. `lookup(kind, value,
connection_id=None)` means the unbound namespace, not all provider connections.
It queries the blind index, never decrypts a full table to search. Contact names,
notes and calendar values have no plaintext/indexed search column. Date strings
are `YYYY-MM-DD` with `*_has_year=True` or `MM-DD` otherwise. A date flag must be
written together with the date. Rotation registration/marker rules are in
[PII_PROTECTION](PII_PROTECTION.md).

## HTTP and workspace UI

- `GET /api/v1/contacts`: bounded keyset page (`limit` 1–100, `before_id > 0`).
- `GET /api/v1/contacts/{contact_id}`: allowlisted detail, identities and consent
  evidence; a foreign/missing contact is an opaque 404.
- `GET /api/v1/contacts/{contact_id}/export`: additionally requires EXPORT and
  returns the complete aggregate, including journal entries, under one shared
  transaction/parent lock.
- `/app/contacts`: minimal read-only page in the communications navigation group.

Authentication and the existing active-membership context remain mandatory.
`contacts.contact` is a new explicit catalog resource. Existing owner/admin
presets receive its declared actions; manager/analyst/viewer presets receive no
new access. The nav entry and page use VIEW, export also uses EXPORT. No write,
merge or provider-import HTTP endpoint is introduced. Projections exclude
ciphertext, blind indexes and rotation markers. Decrypted contact data is
returned only to authorized readers and must not be logged.

## Consent and privacy

No consent is created with a contact/identity. `record_consent` records a grant
or withdrawal with timezone-aware time, purpose, channel, nonempty basis and
source evidence. Records are metadata only. D7 is now answered: batch delivery is email/bot-only,
requires explicit channel/purpose opt-in and unsubscribe, and forbids MTProto
bulk. This task neither implements a delivery policy engine nor relaxes any
approval/delivery gate. No `can_send` decision
is inferred from a verified identity or a consent record.

All four tables have tenant CASCADE references, so the existing reviewed
workspace offboarding flow deletes them; no public-schema deletion/migration is
authorized by this delivery. `ContactPrivacy.export_contact` powers the explicit
privacy export route; `delete_contact` uses the scoped aggregate delete and
child/journal CASCADE erasure. There is no generic P6 export/delete registry in
the baseline to register with. This module adapter does not claim to deliver
P6-T01's separately gated retention rollout. Deletion of a future merge target
that is still referenced by `merged_into_id` fails closed; P15-T03 owns the merge
and reconciliation lifecycle.

## Validation boundary

`tests/contacts/test_p13_t07_unit.py` is DB-exempt, guards DB connection/session
boundaries and uses generated synthetic keys. Its in-memory Alembic operation
capture proves metadata shape only, not executable PostgreSQL DDL.
`tests/contacts/test_p13_t07_db.py` requires root conftest, an exclusively owned
`test_schema`, migration `0130`, schema inventory before/after, and verified
synthetic-row cleanup. Remote-task execution hands this route to the local
runner. READY requires that evidence plus the actual upgrade → downgrade to
0129 → re-upgrade cycle; static metadata tests do not substitute for it.
