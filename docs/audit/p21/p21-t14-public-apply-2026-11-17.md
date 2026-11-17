# P21-T14 — authorized local schema application, 2026-11-17

The owner explicitly authorized the public migration in the task dialogue
`codex resume 01a1e4db-c4b6-7521-936b-42e5bcfce587`. Scope was
`p21t13_catalog_storage` → `p21t14_rate_limits` on local
`localhost:5432/social_manager`; P21-T15 was not applied to public.

Preflight used clean dev `4eda8345`, head `p21t14_rate_limits`, and verified the
migration file unchanged from independently accepted P21-T14 `73a3c7a4`.
[Accepted validation](p21-t14.md) includes the PostgreSQL migration cycle and
five real-schema tests; that unchanged revision supplies the prior test-schema
validation. No new tests or synthetic public records were created.

No other client backends or local runtime/test/migration writers were present.
The coordinator held cooperative advisory lock `1370093` across backup,
application and verification. Lock/statement timeouts were 5/120 seconds.

A full custom-format database backup was created before either schema changed:

- Path: `/Users/admin/.local/state/social-media-ai/backups/p21-t14-public-20261117T125847Z/social_manager-before-p21t14.dump`
- Backup directory mode 0700, dump mode 0600; intentionally retained for recovery.
- `pg_restore --list` and full extraction to `/dev/null`: PASS.
- SHA256, byte size, full schema inventories and per-table row counts/content hashes:
  `evidence.json` in the same protected directory. Raw backup/data are not in Git.

Applied sequentially under the lock, using the matching `DB_SCHEMA` and sync URL:

```sh
alembic upgrade p21t14_rate_limits
```

Both `test_schema` and `public` now report `p21t14_rate_limits`. Each gained only
`llm_rate_buckets` and `llm_dispatch_leases`, their sequences/composite types and
seven indexes; no existing schema object disappeared. Both new tables are empty.
Every pre-existing table except the expected Alembic revision row retained its
exact row count and SHA256 of sorted row JSON. Model-type/permission data were
included in this comparison, including the migration's normal registry hook.

The lock was released. No provider request, flag change, deployment, application
runtime or P21-T15/P21-T16 migration was performed. Recovery is the retained full
backup; the additive migration also has its accepted downgrade. Do not discard
new production rows by downgrading after use without a separate reviewed plan.
