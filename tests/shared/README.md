# Shared test presets

Put reusable factories, fixtures, doubles and import helpers here, following
[tests/AGENTS.md](../AGENTS.md). Root `tests/conftest.py` owns pytest bootstrap.
Move legacy root helpers only with all affected consumers; preserve isolated
loaders and root-relative paths. Domain-specific helpers stay in their domain.

`fixtures/` holds reusable, non-secret JSON approval/evidence templates for the
LLM-budget runner. These examples grant no live execution or production approval.
Source-pinned historical Git fixtures keep their original blob paths.

[Settings layout](settings_layout_fixtures.py) and [LLM catalog](settings_llm_fixtures.py)
renderers share synthetic Jinja context without app imports. See the
[resource index and relocation map](../README.md) for execution routes and historical paths.
