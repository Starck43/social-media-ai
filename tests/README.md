# Test resource navigation

[Conventions](AGENTS.md) own naming, DB isolation and reporting. This index routes
resources; it does not authorize execution or infer isolation from filenames.

| Resource | Classification by actual resource use | Execution route |
| --- | --- | --- |
| [Reviewed Python offline manifest](../.ci/offline-tests.json) | Explicit tool-only imports; no app/root fixtures | `python -m scripts.ci.run_offline`; the reviewed tool-only execution route |
| [Confirmation JS contract](js/web_confirm.test.cjs) | Node built-ins, VM and template reads; no HTTP/app/DB/browser | `node --test tests/js/web_confirm.test.cjs` |
| [Notification JS contract](js/web_shell_state.cjs) | Node built-ins, VM and mocked fetch/context/CSRF receipts | `node tests/js/web_shell_state.cjs` |
| [Native confirmation smoke](browser/web_confirm_smoke.cjs) | Local Chromium subprocess and synthetic file HTML | `node tests/browser/web_confirm_smoke.cjs` |
| [Keyboard/backdrop confirmation](browser/web_confirm_keyboard.cjs) | Local Chromium DevTools pipe and synthetic file HTML | `node tests/browser/web_confirm_keyboard.cjs` |
| [Shared settings renderer](shared/settings_layout_fixtures.py) | Jinja/StrictUndefined, repository templates and inert context; no app imports | `python tests/shared/settings_layout_fixtures.py --out /tmp/settings-fixtures` |
| [Shared LLM catalog renderer](shared/settings_llm_fixtures.py) | Shared Jinja renderer, inert metadata; no client/provider/app imports | `python tests/shared/settings_llm_fixtures.py /tmp/llm-fixtures` |
| [Standalone renderer regressions](tooling/test_p11_t16_fixture_rendering.py) | Core dependencies, import tripwire forbids app; not a pytest suite | `python -I tests/tooling/test_p11_t16_fixture_rendering.py` |
| [Installed wheel smoke](wheel_runtime_smoke.py) | Real installed native/ASGI imports under DB/network/runtime tripwires | Use the package-profile verifier; see [profiles](../requirements/README.md) |
| [Guarded selected discovery](tooling/test_p11_t16_discovery.py) | Five explicitly reviewed suites use Jinja, AST/isolated sources and mocked services; real app imports/network/thread starts are forbidden | Dev interpreter: `python -I tests/tooling/test_p11_t16_discovery.py`; **collection only**, never a generic test runner |
| Domain pytest suites and remaining legacy helpers | Root [conftest](conftest.py) is DB-dependent regardless of a suite's name or mocks | Normal pytest requires the validated DB target/exclusive ownership; do not add `--noconftest` based on filenames |
| Other root `.cjs` fixtures | Inspect each script: Playwright/browser/vendor assets or Node VM; not admitted by this index | Retained deliberately; select/validate separately before moving or running |
| Live/DB external resources | Explicit target, schema, synthetic cleanup and authorization required | [Database procedure](../docs/COLLABORATION.md#database); not covered by metadata/browser checks |

`CHROMIUM_BIN` selects the native executable for the two browser scripts. These
selected scripts use generated file fixtures and contain no application server
startup or live requests. Both JS contracts resolve assets relative to their
own file, so they also run from outside the checkout.

## Relocation and historical evidence

P11-T16 moved only the resources below. New task tests retain task IDs; existing
moved filenames remain stable. Loaders in unit-web suites and the admin fixture
renderer use the new shared paths; their normal pytest/DB classification did not
change. No assertions or suites were deleted to shorten discovery.

| Previous path under `tests/` | Current path |
| --- | --- |
| `settings_layout_fixtures.py` | [shared/settings_layout_fixtures.py](shared/settings_layout_fixtures.py) |
| `settings_llm_fixtures.py` | [shared/settings_llm_fixtures.py](shared/settings_llm_fixtures.py) |
| `web_confirm.test.cjs` | [js/web_confirm.test.cjs](js/web_confirm.test.cjs) |
| `web_shell_state.cjs` | [js/web_shell_state.cjs](js/web_shell_state.cjs) |
| `web_confirm_smoke.cjs` | [browser/web_confirm_smoke.cjs](browser/web_confirm_smoke.cjs) |
| `web_confirm_keyboard.cjs` | [browser/web_confirm_keyboard.cjs](browser/web_confirm_keyboard.cjs) |

Accepted [audit history](../docs/audit/README.md) keeps original commands, dates,
counts and source attribution; do not rewrite historical evidence to current
paths/results. For an old command, use the relocation map above, or inspect the
[pre-move source tree](https://github.com/Starck43/social-media-ai/tree/251d26ca87eeb826dee1ce50be07f6e2893c5138/tests).
History is lazy navigation, not required reading for every test task.
