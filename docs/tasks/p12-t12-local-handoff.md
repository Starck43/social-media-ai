# P12-T12 — integrated increment and remaining roadmap

Entry: [task card](p12-t12.md), [validation](../P12_FILE_WORKSPACE_VALIDATION.md),
[implementation](../FILE_WORKSPACE_PRIVATE_IMPLEMENTATION.md),
[original plan reconciliation](../FILE_WORKSPACE_ROADMAP.md).

## Completion evidence

The local completion fixes the shared migration history, exact migration-test
restoration, automatic bounded expiry and explicit private-result purge approval
during key rotation. Real PostgreSQL checks cover consent/revoke/regrant,
forget versus in-flight work, quotas, corruption/clock behavior, cleanup and
transaction rollback. Synthetic authenticated HTTP and browser checks are
recorded in validation; mocks are not live-provider proof.

The owner authorizes integration and test/public migrations. Live connections
will be checked by the owner after merge and are waived as pre-merge acceptance
criteria. Keep source and tested revisions explicit in the PR/audit.

## Owner live validation after merge

- Confirm the intended local database is at the reviewed head before enabling
  FILE_CATALOG_UI_ENABLED or FILE_WORKSPACE_ACTIONS_ENABLED. Both default off.
- Use a personally owned test connection with the intended folder root and
  explicit consent; verify the read-only listing and bounded text attachment.
- Verify actual download hosts and sanitized provider errors. Unknown hosts
  remain refused; a sample host is not an unrestricted allowlist.
- A native authenticated provider-open and immutable exact-byte version authority
  remain unavailable. Do not enable durable Yandex results based on MD5, mtime,
  equal metadata or a successful GET. No write or delete is enabled by this pilot.

## Future task scope

P12-T12 remains the broader roadmap: exact-version provider capability, native
original-open, local-only execution, richer version/activity workflows and
capacity/restore evidence. P12-T15 owns separately reviewed folder controls and
approved mutations. P12-T13 is the independent capability-contract review;
P8-T34 retains provider capability limitations. The original #307 requirements
are preserved in the reconciled plan rather than overwritten by a short card.

Accepting the delivered default-off increment does not report the entire future
roadmap, live provider or deployment as complete. Resume any further implementation
from current dev under the relevant task claim, preserving tenant/owner/consent
fences and the single runtime owner.
