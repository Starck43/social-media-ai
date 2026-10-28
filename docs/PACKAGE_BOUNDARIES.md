# Incremental application package boundaries

## Task-plan CREATE core (P11-T07)

Use `app.services.task_preparation` to debug plan resolution and projection
without importing persistence or transports. Existing consumers may keep importing
its five public types/functions from `app.services.task_creation`: these are
identity-preserving re-exports, not alternate implementations. The shell retains
`persist_task_create` / `create_task_with_sources` and their existing lazy ORM
imports, explicit session/UoW, permission and admission responsibilities.

The reviewed dependency direction is:

```text
task_preparation -> task_plans -> collection.intent -> core.config -> core.settings_views
                             -> social.vk_access
task_contracts -> task_plans

task_creation (compatibility + persistence shell) -> task_preparation
                                                -> lazy database / model managers
```

The pure preparation core has no model, manager, transport or provider edge.
Its transitive closure includes the actual package initializers. Config is an
explicit dependency because collection intent reads configured limits; this is
not a declaration that all `app.core` is a bottom layer. `task_contracts` includes
its existing lazy error projection import. All local imports, including lazy and
type-only imports, count in the selected core; new imports require review.

### Surrounding cycles are not hidden

The broader model facade is **not** in this core. Source inspection finds paths
from `app.models` through `app.models.agent.agent_session` to `app.agent.session`,
and through `app.models.actions.agent_action` / manager package initialization /
`bot_action_manager` / `actions.telegram_message` to `app.channels.telegram`.
One concrete static cycle is `app.models -> app.models.agent ->
app.models.agent.agent_session -> app.agent.session -> app.agent.tools ->
app.core.permissions -> app.models`. Agent tooling also returns to the task-create
shell through `app.agent.toolset.tasks`.
These are static paths (including lazy/type-only imports), not assertions that
those operations execute at import time. Do not relabel the whole `app.agent`
as transport-only, tighten all model imports, or remove legitimate lazy optional
provider imports based on this first contract. Persistence/launch/result and the
remaining facade cycles need their own reviewed extractions.

## Deterministic checks

The baseline lives in `scripts/ci/package_boundaries.py`, beside its source-only
scanner. It pins exact application edges and per-module third-party dependencies,
not directory-wide ignores. Parent initializers, relative imports, conventional
aliased/literal dynamic imports and missing application modules are inspected;
computed dynamic imports fail closed for review. The checker does not execute
application code. It is an accidental-drift tripwire, not a security sandbox for
arbitrary Python code.

```sh
python -m scripts.ci.package_boundaries
python -m scripts.ci.run_offline
```

Use the tool-only hash-locked environment in [.ci/README](../.ci/README.md).
`tests/tooling/test_p11_t07_package_contract.py` is explicitly in the offline
manifest. Its fixture mutations prove direct/transitive/parent/relative/lazy/
type-only/dynamic adapter edges, unreviewed external dependencies and cycles
cannot silently widen this contract; unrelated lazy provider modules remain
outside the selected boundary. `# noqa` does not suppress the scanner.

`tests/wheel_runtime_smoke.py` exercises the **installed** preparation core and
compatibility exports before the ASGI graph is imported. The safe-import finder
forbids unreviewed app/CLI modules; database/network/subprocess/runtime/provider
tripwires remain enabled. It checks canonical selected/all/discovery plans,
scheduled/once triggers, payload context and conflict order. The package CI
matrix preserves real optional integration imports for their selected profiles;
the core profile must not acquire those integrations just to prepare a plan.
No DB, migration or live-provider check is needed for this move-only boundary.

## Evolving and rolling back the slice

For an extraction, first inspect the real edge/cycle and separate behavior changes
from moves. Add a narrow core root or edge only with a reviewed dependency and
matching negative/import-time checks; never add a blanket layer exemption or
`noqa`. Keep compatibility exports until all consumers migrate independently.
Re-run the offline and installed profile lanes, not the full DB suite by default.

Rollback: move the unchanged preparation definitions back into `task_creation`
and remove the re-exports, then revert this slice's scanner/tests/smoke additions.
No schema or stored payload changes are involved. Do not revert concurrent
`dev` task closures. Before integration fetch and merge **`origin/dev`**, never
an old local `dev`; task acceptance and the owner's merge decision remain separate.
