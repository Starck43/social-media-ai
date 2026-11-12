# Messenger bootstrap inventory

An authorized operator may inventory the **configured bootstrap workspace** for
one running configuration using the command below. This is read-only and does
not change configuration, memberships, bindings, sessions or grants. Confirm the
exact target and authorization first; `--target-approved` is an affirmation, not
access control. Do not run against public/production without separate approval.
Use a least-privilege database identity and a protected output location.

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
integrated. Binding rows cannot identify which person controls a chat: do not infer
a person-to-chat link or actual effective access from this report.

`next_member_after` and `next_binding_after` are independent cursors. Continue
only the remaining stream using its cursor, and ignore already completed stream
rows on subsequent calls. Each invocation is a separate repeatable-read snapshot;
concurrent changes can affect totals or pages. Configuration shown is for this
process only; inspect all running instances through approved operations. The tool
refuses allowlists over 1,000 distinct IDs per channel rather than silently
truncating them. It refuses local SQL echo/logging, but cannot control server logging or third-party
instrumentation, which can receive allowlist values as query parameters.

Before revocation, coordinate an approved backup and access review. Remove the
intended ID from every running instance's allowlist, then separately review and
revoke persisted membership and bindings via approved administration, and verify
denial. Removing an environment ID alone does not revoke persisted access;
deleting a membership while retaining the ID can permit new bootstrap. Never
copy IDs or credentials into public task evidence. An actual revocation or
rollback requires separate authorization and is not performed by this command.
