# Manual digest-send acceptance

## Current source of truth

The owner's authorization redesign in `dev` at
`eb6cad6c56e78768b6482eba312082b849d24712` supersedes the old policy and test
fixtures. Preserve that implementation; this follow-up changes only the
assistant's extra tests and documentation. The authoritative
`tests/test_digest_send_permissions.py` is copied byte-for-byte from this dev
revision, not replaced with the assistant's older loader. Its SHA-256 is
`2c105d8d2cc88ce05ec513c3fd09350d829161a0ede0a6646af95d04ac2344fb`.

Current contract:

- Agent send declares `notifications.digest_run.create`; read-only preview
  declares `notifications.digest_run.view`. The web handler's structured
  `digestrun` / `create` check resolves to the same versioned catalog resource.
- Platform roles (`user`, `operator`, `admin`) are separate from workspace roles.
  Platform authority cannot replace missing workspace digest grants.
- The real workspace presets allow digest creation for `owner`, `admin` and
  `analyst`, not `manager` or `viewer`. Sending still needs valid membership,
  current rights and the surface's existing safeguards.
- Agent admission additionally requires AI chat access (implicit for an owner,
  explicit for other members). Web digest permission is independent of that AI
  flag. Neither a platform admin nor an AI grant is a digest permission.
- Pending authorization is version 3, bound to the actor, workspace role and AI
  admission. Role/AI changes and older contracts require a fresh request; the
  runtime rechecks after storage consumption before invoking the publisher.

See [access cutover and remaining DB/ASGI acceptance](../ACCESS_MIGRATION.md).
This work does not run or modify migration `0096`, change presets, restore old
role enums, or waive cutover safeguards. The development `public` migration
record does not establish that the shared test schema is migrated.

## Fresh sandbox checks

The owner authorized assistant-run mock tests. This remains a narrow no-DB
exception, not a modification of the new OpenCode-primary pytest policy in
`AGENTS.md` / `.agent/rules.md` R10. Neither OpenCode nor Kilo is installed in the
sandbox; no pytest fallback was attempted.

The assistant's helper now loads the real `access_catalog`, enums, policy,
identity, confirmation, runtime, registry, WebPerms, handler and CSRF code with
module-local imports. Storage, HTTP plumbing, LLM and publisher are doubles;
workspace catalog/classification and allow/deny predicates are not mocked.
The ten dev-maintained tests remain unchanged; 37 assistant boundary tests
include 12 new authorization-cutover methods. Loop subcases are not extra tests.

```sh
/data/pr176-env/bin/python -m unittest discover -s tests -p 'test_digest_send*.py' -v
```

On the `eb6cad6` source baseline plus the adapted additions: **47 tests passed,
exit 0** (0.572 seconds), Python 3.13, real Jinja2 and `itsdangerous==2.2.0`.

Coverage includes:

- actual fixed presets for all five workspace roles through runtime and web;
- platform-admin-plus-workspace-viewer denial, even with a permissive platform
  predicate, and foreign posted workspace rejection;
- AI-disabled staging, AI revocation before yes and during mocked consumption;
- workspace-role change before and during consumption, including a replacement
  role that still has CREATE; unknown and unresolved role denial;
- old version-2 approvals and old bare/UPDATE permission-contract rejection;
- existing rights revocation, inactive/missing identity components, actor
  binding, expiry, replaced intent, one-use confirmation and mocked CAS race;
- real CSRF missing/invalid/tampered/wrong-key denial and global import isolation.

During adaptation, three assertion failures exposed an incorrect assumption in
new tests: the current runtime clears a rightful actor's rejected intent through
CAS; it does not leave that intent unconsumed. The tests now assert clearance
and zero publisher calls. Application code was not changed to fit those tests.

A temporary-source negative control changed only the canonical CREATE tool
right to `notifications.digest_run.update`. Three selected runtime regressions
all failed as expected, with zero errors. Source files were untouched. This is
sensitivity evidence, not three additional passing tests.

Black/isort check-only runs (line length 120), whitespace checks and published
byte verification are part of the follow-up closeout. The PR records the exact
published test snapshot and result; a local run is not relabeled as that commit.

## Limits and remaining work

Mock managers do not prove ORM/schema compatibility or middleware membership
refresh. In-memory CAS does not prove PostgreSQL locking, durable commits or
acknowledgement recovery. Direct handler calls do not prove ASGI/session behavior.
No `.env`, DB, bootstrap, migration, provider, transport or live send was used.

Keep the PR Draft until current-model integration evidence is established.
Use the [replacement local intake](pr176-local-evidence-intake.md), not the old
pinned revision/task/plugin/matrix without review. Do not apply old test fixtures
to the new schema or run the new app against an old schema.

## Historical evidence — not current acceptance

The original fix reached dev as `6d16436`. The previous assistant follow-up
`f81cffe` had 35 mock tests passing on the pre-cutover source; that result does
not validate `eb6cad6`. Its loader workaround is superseded where dev now
maintains the same test file. Original local plugin/matrix hashes, old PASS
counts, the unresolved `exit 3 / collected 0`, and previous delegation pauses
remain in the [archived handoff](https://github.com/Starck43/social-media-ai/pull/176#issuecomment-6187394197).
Recover historical failure details only if relevant to the current runner;
fixing an obsolete run is not a prerequisite invented for the new architecture.
