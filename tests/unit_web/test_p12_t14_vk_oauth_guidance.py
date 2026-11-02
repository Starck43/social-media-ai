"""Source-level tests for safe, accurate VK authorization guidance.

Only the route function is extracted: the platform registry, OAuth builder and
responses are doubles. ASGI, template and provider integration are not tested.
"""

import ast
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

__source_only__ = True
ROOT = Path(__file__).resolve().parents[2]


class FakeRedirect:
    def __init__(self, url, status_code):
        self.url = url
        self.status_code = status_code


class PlatformConfigurationError(Exception):
    pass


class ConnectionAuthorizeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse((ROOT / 'app/web/connections.py').read_text())
        function = next(node for node in tree.body if getattr(node, 'name', None) == 'connection_authorize')
        function.decorator_list = []
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
        self.flashes = []
        self.oauth = AsyncMock()
        self.scope = {
            'Request': object,
            'CONNECTIONS_BY_PLATFORM': {'vk': SimpleNamespace(is_oauth=True, title='VK')},
            'BACK': '/app/settings?tab=connections',
            'safe_next': lambda value: value if value.startswith('/app/') else None,
            'add_flash': lambda _req, level, text: self.flashes.append((level, text)),
            'RedirectResponse': FakeRedirect,
        }
        exec(compile(module, 'app/web/connections.py', 'exec'), self.scope)
        config = types.ModuleType('app.services.social.platform_config')
        config.PlatformConfigurationError = PlatformConfigurationError
        oauth = types.ModuleType('app.services.social.vk_oauth')
        oauth.build_authorize_url = self.oauth
        self.modules = patch.dict(sys.modules, {
            'app.services.social.platform_config': config,
            'app.services.social.vk_oauth': oauth,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.request = SimpleNamespace(state=SimpleNamespace(tenant_id=42, web_user=SimpleNamespace(id=7)))

    async def test_runtime_error_is_safe_and_does_not_suggest_client_secret(self):
        self.oauth.side_effect = RuntimeError('synthetic-sensitive-detail')
        response = await self.scope['connection_authorize'](self.request, 'vk')
        self.assertEqual((response.url, response.status_code), ('/app/settings?tab=connections', 302))
        self.assertEqual(len(self.flashes), 1)
        level, message = self.flashes[0]
        self.assertEqual(level, 'error')
        self.assertIn('VK_APP_ID', message)
        self.assertNotIn('VK_CLIENT_ACCESS_KEY', message)
        self.assertNotIn('synthetic-sensitive-detail', message)
        self.oauth.assert_awaited_once_with(42, user_id=7)

    async def test_success_redirects_to_oauth_url(self):
        self.oauth.return_value = 'https://example.invalid/authorize'
        response = await self.scope['connection_authorize'](self.request, 'vk')
        self.assertEqual((response.url, response.status_code), ('https://example.invalid/authorize', 302))
        self.assertEqual(self.flashes, [])

    async def test_shared_configuration_error_retains_existing_path(self):
        self.oauth.side_effect = PlatformConfigurationError('missing OAuth endpoint')
        response = await self.scope['connection_authorize'](self.request, 'vk')
        self.assertEqual(response.status_code, 302)
        self.assertIn('Общая конфигурация', self.flashes[0][1])

    async def test_missing_user_cannot_start_oauth(self):
        self.request.state.web_user = None
        response = await self.scope['connection_authorize'](self.request, 'vk')
        self.assertEqual(response.status_code, 302)
        self.oauth.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
