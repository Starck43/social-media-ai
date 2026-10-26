# Task-selection migration history

Historical design plan, not current migration authorization. Current contract:
[selection](../COLLECTION.md#task-selection-contract); migration permissions:
[database safeguards](../COLLABORATION.md#database).

### Removal plan of `monitored_users` (single migration window, pre-production)

1. Code: no reader of `payload.monitored_users` or
   `Source.params["monitored_users"]`; `collect_monitored_users` is replaced by
   the selection path (see `docs/COLLECTION.md`) or deleted with its API route.
2. Data: an Alembic migration strips the key from `agent_tasks.payload` and
   `sources.params`, and (for tasks that carried it) writes the equivalent
   `tracked_users` only where the value can be resolved to a platform id and the
   operator confirms; unresolved values are listed in the migration log, never
   guessed.
3. Docs, CLI flags, API schema, chat tools, web forms and admin help are changed
   in the same release; no compatibility flag is kept.

### Validation focus

No-DB tests for parsing and matching boundaries (`any`/`all`, `ё`, word bounds,
platform scope, empty scope), task-payload normalisation rejects malformed
entries, and a mocked collector proves that a non-tracked author's item is
staged but not analysed. Screening accuracy against the real platform is a
separate, live acceptance step and is not claimed by unit tests.
