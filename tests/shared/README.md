# Shared test presets

Put reusable factories, fixtures, doubles and import helpers here, following
[tests/AGENTS.md](../AGENTS.md). Root `tests/conftest.py` owns pytest bootstrap.
Move legacy root helpers only with all affected consumers; preserve isolated
loaders and root-relative paths. Domain-specific helpers stay in their domain.

`fixtures/` holds reusable, non-secret JSON approval/evidence templates for the
LLM-budget runner. These examples grant no live execution or production approval.
Source-pinned historical Git fixtures keep their original blob paths.
