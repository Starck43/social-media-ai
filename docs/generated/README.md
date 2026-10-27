# Generated reference tables

Only stable metadata is generated here:

- [OpenAPI routes](api-routes.md): HTTP method, path and operation ID for routes
  present in `app.openapi()`. Schema-hidden/web/admin routes are not claimed.
- [Typer commands](cli-commands.md): registered command paths and their declared help.
- [Typer parameters](cli-options.md): flags/arguments, types, requiredness and
  declared defaults; factories are never evaluated (`<dynamic>`).

[API policy/examples](../API.md) and [CLI policy/examples](../CLI.md) remain
handwritten. An OpenAPI security annotation, CLI flag or default is not evidence
of authorization, tenant isolation, readiness or permission to execute a command.
No audit history is regenerated or reattributed.

## Reproduce

Use a clean Python 3.12 environment with the pinned [core export](../../requirements.txt)
installed. From the checkout root:

```sh
python -I scripts/reference/probe.py --guard-check
python scripts/reference/generate.py
python scripts/reference/generate.py --check
```

The generator launches a fresh `-I` interpreter with a synthetic environment,
a private temporary working/log directory and dotenv disabled. The child imports
the actual ASGI and Typer declarations, requests OpenAPI and walks Click metadata;
it never starts ASGI lifespan or invokes commands/default factories. Tripwires
reject network/DNS, DB connections, subprocesses, threads, event loops, runtime
entry imports and provider construction, including swallowed attempts. This is a
metadata check, not runtime/DB/provider validation. Dependency installation is a
separate networked setup step; regeneration itself does not access the network.

Pass `--python /path/to/core-venv/bin/python` if the launcher interpreter has no
application dependencies. No owner credentials, real `.env` or live services are
needed. CI checks the tables in the separate reference lane, never the tool-only
offline lane. Review changes alongside the source declarations; generated output
must not replace handwritten policy or historical evidence.
