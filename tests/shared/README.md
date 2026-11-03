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

## Business-module test kit (P13-T06)

`provider_http.mock_transport` accepts an explicit mapping of `(METHOD, exact URL)`
keys to `ResponseSpec`; unmatched requests raise, and the transport never opens a
connection. Use `httpx.Client(transport=mock_transport(routes))` or `AsyncClient`
and inject it into the adapter under test. Synthetic bodies are examples, not
recordings or evidence of a real provider contract. `MailDouble` is an in-memory
scripted command double, not an IMAP/SMTP protocol implementation. Use
`deny_network()` around provider tests as an additional socket/DNS tripwire; it is
not an OS sandbox and cannot guard clients using pre-existing sockets or other
processes. Do not use real credentials or external providers.

Register `tests.shared.business_tenancy` as a pytest plugin for `business_world`
(two synthetic actor/tenant pairs) and `assert_cross_tenant_not_found`. For real
manager tests also register `tests.shared.fixtures.personal_workspace` and use
`business_db_world`; this reuses the existing isolated manager-backed fixture.
Run manager tests with root conftest, `@pytest.mark.tenancy`, and the normal
serialized `test_schema` safety and cleanup procedure; fake identities do not
prove database isolation.

The coverage gate requires focused test paths and measures each target
independently, so one high-coverage package cannot hide another. For example:

```sh
python scripts/ci/package_coverage.py app/services/example --min 80 --tests tests/unit_services/test_example.py
```

A source file is also accepted as a target. `--no-conftest` is only for proven
DB-exempt tests. Add delivered business packages and their focused tests to
`scripts/ci/coverage_packages.toml`; `--changed-from <ref>` selects registered
paths changed relative to that Git ref (including uncommitted tracked changes).
An empty selection means **no coverage was measured**, not a coverage pass for
unregistered packages. The manifest starts empty because the packages are not yet
implemented; CI must invoke this script explicitly when packages are registered.

The bundled `fixtures/business_providers.json` contains synthetic examples for
Bitrix24, Google Calendar, CalDAV, 1C OData, IMAP and SMTP. They are intentionally
not recorded provider responses and do not establish protocol compatibility.
