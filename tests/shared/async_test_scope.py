"""Run an async test body without the bootstrapping operator grant.

`tests/conftest.py::_platform_scope` installs the operator grant so a legacy
*arrange* may write without minting an actor. pytest-asyncio starts the body
with `contextvars.copy_context()` of its runtest step, so the coroutine — and
every task it starts, including an ASGI transport that outlives the test —
inherited that grant. A refused tool call then answered `{"ok": true}`: the
refusal was untestable, and the grant leaked to whatever was still running
after teardown.

The test coroutine is wrapped right before the item runs. The wrapper unsets
the grant at call time — inside the context the plugin copied, before any
`await` — so the fixture that yielded the arrange's grant (and restores it on
teardown) stays untouched, while the body and everything it starts run clean.
"""

from __future__ import annotations

import inspect


async def _call_without_operator_grant(func, args, kwargs):
    from app.core.permissions import _operator_grant

    _operator_grant.set(False)
    return await func(*args, **kwargs)


def strip_operator_grant_for_async_tests(item) -> None:
    """Wrap an async test coroutine; leave synchronous items alone."""
    original = getattr(item, "obj", None)
    if original is None or inspect.iscoroutinefunction(original):
        return
    if not inspect.iscoroutinefunction(getattr(original, "__func__", None)):
        return
    # `unittest.IsolatedAsyncioTestCase` methods are bound non-coroutine
    # wrappers that `self._callAsync(self._asyncioTest(...))` the real body, so
    # only the plugin's own context can be replaced there; pytest would also
    # warn about a bound method returning the coroutine.
    if getattr(original, "__self__", None) is not None:
        return
    if getattr(original, "_without_operator_grant", False):
        return

    def body(*args, **kwargs):
        return _call_without_operator_grant(original, args, kwargs)

    body._without_operator_grant = True  # type: ignore[attr-defined]
    item.obj = body
