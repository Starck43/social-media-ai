"""The standalone route must fail before accidentally importing runtime or networking."""

import importlib
import socket
import sys
import unittest

from scripts.check_p8_t41 import BLOCKED, source_only_guard

_PROJECT_BOOTSTRAP_LOADED = any(name.split(".")[0] in BLOCKED for name in sys.modules)


@unittest.skipIf(
    _PROJECT_BOOTSTRAP_LOADED,
    "source-only guard checks must run before project runtime imports; use scripts.check_p8_t41",
)
class SourceOnlyRunnerTests(unittest.TestCase):
    def test_runtime_and_db_bootstrap_imports_are_forbidden(self):
        for name in ("app", "cli", "sqlalchemy", "httpx", "tests.conftest", "scripts.setup_test_db"):
            with self.subTest(name=name), source_only_guard(), self.assertRaises(ImportError):
                importlib.import_module(name)

    def test_dns_and_socket_connections_are_forbidden(self):
        with source_only_guard():
            with self.assertRaisesRegex(RuntimeError, "forbids network"):
                socket.getaddrinfo("example.invalid", 443)
            with socket.socket() as connection:
                with self.assertRaisesRegex(RuntimeError, "forbids network"):
                    connection.connect(("127.0.0.1", 1))
                with self.assertRaisesRegex(RuntimeError, "forbids network"):
                    connection.connect_ex(("127.0.0.1", 1))
            with self.assertRaisesRegex(RuntimeError, "forbids network"):
                socket.create_connection(("127.0.0.1", 1))


if __name__ == "__main__":
    unittest.main()
