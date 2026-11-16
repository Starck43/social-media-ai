"""Repository-local pytest plugin (loaded by `tests/conftest.py`).

Holds the harness scopes that must wrap a test body rather than a fixture; see
each module's docstring for why a fixture generator cannot do it.
"""

from tests.shared.async_test_scope import strip_operator_grant_for_async_tests


def pytest_runtest_setup(item) -> None:
    strip_operator_grant_for_async_tests(item)
