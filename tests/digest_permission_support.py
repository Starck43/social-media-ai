"""Private-source harness for digest permission tests: no app bootstrap or DB.

Actual policy, identity, confirmation, runtime, web guards and CSRF are loaded
with module-local dependency bindings. Only storage, HTTP plumbing, model
registry classification, LLM and publisher are doubles. Never imports conftest.
"""

import asyncio
import copy
import importlib.util
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("_digest_source_loader", ROOT / "tests/source_import_isolation.py")
_loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_loader)


def module_with(**values):
    module = ModuleType("digest_test_dependency")
    module.__dict__.update(values)
    return module


def source(name, path, imports=None):
    return _loader.load_isolated_source(name, ROOT / path, imports)


class DigestEnvironment:
    """Each case owns fresh registry/context/session instances and mock rows."""

    def __init__(self):
        class DatabaseEnum(Enum):
            pass

        self.enums = source(
            "_digest_enums",
            "app/types/enums/user_types.py",
            {
                "app.utils.db_enums": module_with(DatabaseEnum=DatabaseEnum, database_enum=lambda cls: cls),
            },
        )
        self.action = self.enums.ActionType
        self.tenant = source(
            "_digest_tenant",
            "app/core/tenant_context.py",
            {
                "starlette.types": module_with(ASGIApp=object, Receive=object, Scope=object, Send=object),
            },
        )
        self.perms = source(
            "_digest_policy",
            "app/core/permissions.py",
            {
                "app.types": self.enums,
                "app.core.tenant_context": self.tenant,
            },
        )
        # ORM registry classification only; actual allow/deny predicates stay real.
        self.perms._workspace_model_names = lambda: frozenset({"source", "agenttask", "agentscenario", "digestrun"})
        self.user = SimpleNamespace(
            id=12,
            role_id=2,
            role=SimpleNamespace(id=2, codename="VIEWER"),
            is_active=True,
            is_superuser=False,
            has_perm_for=lambda *args: False,
            _is_superuser_role=lambda: False,
        )
        self.grants = frozenset({("digestrun", self.action.CREATE)})
        self.membership = SimpleNamespace(
            id=41,
            tenant_id=31,
            user_id=12,
            channel="telegram",
            external_user_id="7",
            is_active=True,
            is_owner=False,
            role_id=3,
            role=SimpleNamespace(id=3, codename="ANALYST"),
        )
        self.workspace = SimpleNamespace(id=31, is_active=True)
        self.binding = SimpleNamespace(tenant_id=31, channel="telegram", chat_id="7", is_active=True)
        self.users = SimpleNamespace(get=AsyncMock(return_value=self.user))
        self.users.prefetch_related = lambda *args: self.users
        self.members = SimpleNamespace(get=AsyncMock(return_value=self.membership), member_grants=AsyncMock())
        self.members.prefetch_related = lambda *args: self.members
        self.members.member_grants.side_effect = lambda membership: self.grants
        self.tenants = SimpleNamespace(get=AsyncMock(return_value=self.workspace))
        self.channels = SimpleNamespace(get=AsyncMock(return_value=self.binding))
        self.models = module_with(
            User=SimpleNamespace(objects=self.users), Tenant=SimpleNamespace(objects=self.tenants)
        )
        self.members_module = module_with(
            tenant_users=self.members, tenant_channels=self.channels, tenants=self.tenants
        )
        self.identity = source(
            "_digest_identity",
            "app/agent/identity.py",
            {
                "app.core.tenant_context": self.tenant,
                "app.models": self.models,
                "app.models.managers.tenant_manager": self.members_module,
            },
        )
        self.confirmation = source(
            "_digest_confirmation",
            "app/agent/confirmation.py",
            {
                "app.agent.identity": self.identity,
                "app.core.permissions": self.perms,
                "app.core.tenant_context": self.tenant,
            },
        )
        self.tools = source(
            "_digest_tools",
            "app/agent/tools.py",
            {
                "app.agent.confirmation": self.confirmation,
                "app.core.permissions": self.perms,
                "app.agent": module_with(toolset=module_with()),
            },
        )
        self.publisher = AsyncMock(return_value={"status": "sent"})
        self.builder = module_with(
            build_and_publish=self.publisher, DigestDeliveryError=type("DigestDeliveryError", (RuntimeError,), {})
        )
        self.reports = source(
            "_digest_reports",
            "app/agent/toolset/reports.py",
            {
                "app.agent.tools": self.tools,
                "app.services.ai.reporting": module_with(ReportAggregator=object),
                "app.services.digest.builder": self.builder,
            },
        )
        self.spec = self.tools.get_tool("digest_send_now")
        self.actor = self.identity.RuntimeIdentity(
            tenant_id=31,
            user_id=12,
            membership_id=41,
            channel="telegram",
            chat_id="7",
            external_user_id="7",
            membership_role_id=3,
            user_role_id=2,
            is_owner=False,
            user=self.user,
        )
        self.session = SimpleNamespace(
            id=51,
            tenant_id=31,
            channel="telegram",
            chat_id="7",
            is_active=True,
            state={"unrelated": "preserve"},
            append=AsyncMock(),
            touch=AsyncMock(),
        )
        self.consume_lock = asyncio.Lock()
        self.consume_error = False
        self.after_consume = None

        async def patch_state(session_id, updates):
            if session_id != self.session.id:
                return None
            state = copy.deepcopy(self.session.state)
            for key, value in updates.items():
                if value is None:
                    state.pop(key, None)
                else:
                    state[key] = copy.deepcopy(value)
            self.session.state = state
            return copy.deepcopy(state)

        async def consume(session_id, pending):
            # In-memory exact CAS only: not PostgreSQL transaction/commit evidence.
            async with self.consume_lock:
                if session_id != self.session.id or self.session.state.get("pending_confirmation") != pending:
                    return None
                self.session.state.pop("pending_confirmation")
                if self.after_consume:
                    self.after_consume()
                if self.consume_error:
                    raise RuntimeError("synthetic acknowledgement failure")
                return copy.deepcopy(self.session.state)

        self.session_manager = SimpleNamespace(
            get_or_create=AsyncMock(return_value=self.session),
            patch_state=AsyncMock(side_effect=patch_state),
            consume_pending_confirmation=AsyncMock(side_effect=consume),
        )
        self.resolution = SimpleNamespace(tenant_id=31, channel="telegram", chat_id="7", user_id="7", onboarded=False)
        self.inbound = SimpleNamespace(channel="telegram", chat_id="7", user_id="7", text="да", is_channel_post=False)

    def runtime(self):
        runtime = source(
            "_digest_runtime",
            "app/agent/runtime.py",
            {
                "app.agent.confirmation": self.confirmation,
                "app.agent.identity": self.identity,
                "app.agent.prompts": module_with(DEFAULT_SYSTEM_PROMPT="test"),
                "app.agent.tools": self.tools,
                "app.channels.base": module_with(Inbound=SimpleNamespace),
                "app.core.config": module_with(settings=SimpleNamespace(AGENT_MAX_ITERATIONS=1)),
                "app.core.permissions": self.perms,
                "app.core.tenant_context": self.tenant,
                "app.services.tenancy.resolver": module_with(
                    Resolution=SimpleNamespace,
                    is_platform_owner=lambda *args: False,
                    resolve_inbound=AsyncMock(return_value=self.resolution),
                    tenant_daily_cost_limit=AsyncMock(return_value=0),
                ),
                "app.models.managers.agent_session_manager": module_with(agent_sessions=self.session_manager),
            },
        )
        runtime._build_messages = AsyncMock(return_value=[])
        runtime._write_tool_result = AsyncMock()
        runtime._run_tool_loop = AsyncMock(return_value="model continuation mocked")
        runtime._human_confirmation = lambda name, args: "confirm digest"
        return runtime

    async def resolve(self):
        with self.tenant.tenant_scope(31):
            return await self.identity.resolve_runtime_identity(self.resolution)

    async def turn(self, runtime, text="да"):
        self.inbound.text = text
        with self.tenant.tenant_scope(31):
            return await runtime._handle_in_tenant(self.inbound, self.resolution)

    def web(self):
        """Actual web handler/WebPerms/guard/CSRF, mocked FastAPI/HTTP plumbing.

        Does not execute ASGI or middleware membership lookup; that remains
        owner-local acceptance. itsdangerous/Jinja2 are real local libraries.
        """

        class Router:
            def __init__(self, *args, **kwargs):
                pass

            def __getattr__(self, name):
                return lambda *args, **kwargs: lambda fn: fn

        class Response:
            def __init__(self, url, status_code=302):
                self.status_code = status_code
                self.headers = {"location": url}

        fastapi = module_with(
            APIRouter=Router,
            Form=lambda default=None, **kwargs: default,
            Request=SimpleNamespace,
            HTTPException=type("HTTPException", (Exception,), {}),
        )
        response_module = module_with(RedirectResponse=Response)
        access = source("_digest_web_access", "app/services/tenancy/web_access.py")
        self.webperms = source(
            "_digest_web_perms",
            "app/web/perms.py",
            {
                "app.core.permissions": self.perms,
                "app.types": self.enums,
                "app.services.tenancy.web_access": access,
            },
        )
        nav = source("_digest_web_nav", "app/web/nav.py")
        self.webdeps = source(
            "app.web._digest_deps",
            "app/web/deps.py",
            {
                "fastapi": fastapi,
                "fastapi.responses": response_module,
                "fastapi.templating": module_with(Jinja2Templates=lambda env: SimpleNamespace(env=env)),
                "app.models": self.models,
                "app.types": self.enums,
                "app.web.nav": nav,
            },
        )
        csrf = source(
            "_digest_csrf",
            "app/admin/csrf.py",
            {
                "fastapi": fastapi,
                "starlette.middleware.base": module_with(BaseHTTPMiddleware=object),
            },
        )
        self.csrf = csrf.CSRFTokenManager("synthetic-digest-test-key-only")
        self.webhandler = source(
            "app.web._digest_handler",
            "app/web/digests.py",
            {
                "fastapi": fastapi,
                "fastapi.responses": response_module,
                "app.models.digest_run": module_with(DigestRun=object),
                "app.services.digest.builder": self.builder,
                "app.services.digest.render": module_with(render_digest=lambda *args: "mock rendering"),
                "app.web.deps": self.webdeps,
                "app.core.tenant_context": self.tenant,
            },
        )
        return self.webhandler

    def web_request(self, grants=None):
        member = copy.copy(self.membership)
        member.channel = "web"
        perms = self.webperms.WebPerms(
            self.user,
            [member],
            tenant_id=31,
            member_grants=self.grants if grants is None else grants,
        )
        return SimpleNamespace(
            state=SimpleNamespace(web_user=self.user, tenant_id=31, web_perms=perms),
            app=SimpleNamespace(state=SimpleNamespace(csrf_manager=self.csrf)),
            session={},
        )
