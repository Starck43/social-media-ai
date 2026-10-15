# Platform-role lookup runtime import

## Source defect and bounded correction

At dev `caec98ea02b61cad9f5c55a91924d1c60b4533c7`,
`RoleManager.get_by_codename` uses `PlatformRoleType` in `isinstance`, but imports
it only under `TYPE_CHECKING`. Deferred annotations do not bind a runtime name.
The correction imports the existing enum inside the lookup method, before use.
It does not change role identity, aliases, grants, fixed catalogs or the new
platform/workspace separation, and adds no eager manager-import dependency.

`tests/test_platform_role_lookup_isolated.py` adds six unittest methods using
the real enum and manager source, module-local dependency bindings, and synthetic
query results. Intended coverage: all three platform enum values, normalized
strings, retained aliases, query miss, compatibility lookup and disabled arbitrary
role creation. Subcases are not additional methods. No conftest/app bootstrap,
settings, database or external service is needed.

## Validation status

**NOT RUN.** The remote sandbox shell currently fails with an infrastructure
error before command execution. GitHub MCP is available, so the source change
and tests can be reviewed in a Draft PR. No syntax, formatting, collection,
positive/negative-control PASS or application acceptance is claimed for this
patch. The previous 47 digest mocks do not validate this new manager fix.

When execution is available, run isolated collection before execution under
current AGENTS/R10; retain the owner-authorized no-DB mock exception only for
its stated scope. A negative control against the old manager should reproduce
NameError in lookup cases. Do not substitute a mocked lookup implementation.

## Separate local acceptance evidence

The owner relayed a 21-case access-model run on `eb6cad6` plus an owner working
diff: 12 passed, 9 failed, 0 skipped; process exit unknown. A later batch repeated
the nine failures; it is not nine new distinct regressions. Four API scope cases
report this NameError. Root causes of the other five failures are not established
by this patch and need their existing redacted failure excerpts.

The local tree/config/test changes are now published in dev through `17d7bb5`
and `caec98e`; they take precedence over older remote assumptions. Test success
must still be attributed to the actual executed tree, not the later commit by
name alone. The follow-on fixes session was interrupted. Process absence does
not prove cleanup or data preservation: fixture writes occurred, but pre/post
fingerprints and cleanup outcome remain unknown.

No new DB rerun is assigned by this document. First reconcile the prior target
read-only, preserve backups, catalogs/guards and unrelated data, and obtain the
five remaining failure excerpts from permitted runner output. Never repair by
reset/drop/create/stamp/migrate/reseed, restore old roles or weaken assertions.
Missing historical preflight cannot be fabricated: a fresh baseline establishes
only future comparisons, not historical preservation. PR #176 remains Draft
pending its own current-model integration evidence.
