# Read-only operator inventories

Two one-off inventories support access and credential work. Both read the
configured database and schema, change nothing, and print no external IDs, chat
IDs, token material or provider responses. Confirm the exact target and
authorization first: `--target-approved` records operator intent, it is not
access control. Do not run either against public/production without separate
approval. Use a least-privilege database identity and an approved,
access-controlled output location. Both refuse local SQL echo/logging but cannot
control database/server logging or third-party instrumentation, which can receive
allowlist values as query parameters.

## Messenger bootstrap inventory

Inventory the **configured bootstrap workspace** for one running configuration.
The command is read-only: it does not change configuration, memberships, bindings,
sessions or grants.

```bash
python -m scripts.ops.inventory_owner_bootstrap --target-approved --tenant-id <workspace-id> --limit 100
```

The report lists messenger membership and binding database IDs, active states,
coarse role/link states and whether each member appears on this instance's
allowlist; it does not output external IDs, chat IDs or configuration values.
IDs and counts are still sensitive operational metadata. The `without_membership`
count includes allowlisted IDs with no persisted membership and can expose future
bootstrap risk. Inactive and non-owner memberships count as present; this is not
a determination of eligibility. The current dev bootstrap can reactivate or
promote an existing membership; the safety change in draft PR #321 is not yet
integrated. Binding rows cannot identify which person controls a chat: do not
infer a person-to-chat link or actual effective access from this report.

`next_member_after` and `next_binding_after` are independent cursors. Continue
only the remaining stream using its cursor, and ignore already completed stream
rows on subsequent calls. Each invocation is a separate repeatable-read snapshot;
concurrent changes can affect totals or pages. Configuration shown is for this
process only; inspect all running instances through approved operations. The tool
refuses allowlists over 1,000 distinct IDs per channel rather than silently
truncating them.

Before revocation, coordinate an approved backup and access review. Remove the
intended ID from every running instance's allowlist, then separately review and
revoke persisted membership and bindings via approved administration, and verify
denial. Removing an environment ID alone does not revoke persisted access;
deleting a membership while retaining the ID can permit new bootstrap. Never copy
IDs or credentials into public task evidence. An actual revocation or rollback
requires separate authorization and is not performed by this command.

## Source identity inventory

Run before changing legacy `auto` or Telegram MTProto fallback; one workspace at a
time. The command reads the configured database and schema and never applies a
mapping, changes membership, fetches ciphertext, uses a provider, or reads the
deployment session.

```bash
python -m scripts.ops.inventory_source_credentials --target-approved --tenant-id <workspace-id> --limit 100
```

If `next_after_id` is not null, repeat with `--after-id <next_after_id>` until it
is null. Each page is a separate read-only, repeatable-read transaction, not a
single point-in-time snapshot of a concurrently changing catalog. Coordinate a
maintenance snapshot if stable totals are required. Rows cover VK and Telegram,
including inactive sources. Source IDs and active owner IDs are sensitive
operational metadata; limit access and retention of the report.

The tool reports ownerless, malformed/ambiguous, inactive/nonmember or active
membership; it never proposes an owner for an unresolved source. Its parsing and
account-active checks are intentionally conservative and may classify a legacy
value more strictly than the runtime; this is not a simulation of execution. For
an active owner it reports only whether metadata for the expected kinds exists: it
does not decrypt or validate credentials, expiry, authorization, scopes or
provider capability. Malformed owner values are treated conservatively and never
printed. No source names, arbitrary params, raw owner values, token/secret
material or provider responses are output. A source's mode is a bounded enum, not
proof of what a particular run used. Review actual selection policy, obtain
per-source owner consent and approve any migration separately; do not blindly
bulk-fill `owner_user_id` or delete sessions. For rollback, preserve the approved
mapping and backup under the operator's established retention policy.
