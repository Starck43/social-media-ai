# Read-only source identity inventory

Before changing legacy `auto` or Telegram MTProto fallback, an authorized
operator can inventory one workspace at a time. The command reads the configured
database and schema; confirm the target, authorization and safe output location
before invoking it. Do not run it against public/production merely because this
file exists. `--target-approved` records operator intent; it is not an access
control mechanism. Use a least-privilege database identity and an approved,
access-controlled output location. The command refuses SQL logging, but cannot
control database/server logging or third-party instrumentation. It never applies
a mapping, changes membership, fetches ciphertext,
uses a provider, or reads the deployment session.

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
value more strictly than the runtime; this is not a simulation of execution. For an active
owner it reports only whether metadata for the expected kinds exists: it does
not decrypt or validate credentials, expiry, authorization, scopes or provider
capability. Malformed owner values are treated conservatively and never printed.
No source names, arbitrary params, raw owner values, token/secret material or
provider responses are output. A source's mode is a bounded enum, not proof of
what a particular run used. Review actual selection policy, obtain per-source
owner consent and approve any migration separately; do not blindly bulk-fill
`owner_user_id` or delete sessions. For rollback, preserve the approved mapping
and backup under the operator's established retention policy.
