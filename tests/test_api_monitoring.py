import pytest
import uuid
import secrets
import re
from httpx import ASGITransport, AsyncClient

from app.main import create_application
from app.models import Platform, Source, User
from app.models.managers.tenant_manager import TenantUserManager
from app.types import PlatformType, SourceType


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


async def _csrf(client: AsyncClient, path: str) -> str:
    page = await client.get(path)
    assert page.status_code == 200
    match = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert match, f"no csrf input on {path}"
    return match.group(1)


async def _register(client: AsyncClient, prefix: str) -> tuple[User, int]:
    """Sign up: the new user owns a fresh workspace."""
    username = f"{prefix}{secrets.token_hex(3)}"
    token = await _csrf(client, "/app/register")
    resp = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.com",
            "password": "secret-password-1",
            "workspace": f"{prefix} workspace",
            "_csrf": token,
        },
    )
    assert resp.status_code == 200
    user = await User.objects.get(username=username)
    memberships = await TenantUserManager().web_memberships(user.id)
    assert user is not None and memberships
    return user, memberships[0].tenant_id


async def _get_jwt_token(client: AsyncClient, username: str, password: str = "secret-password-1") -> str:
    """Get JWT access token via the login endpoint."""
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_get_source_analytics_returns_new_fields():
    """Test that the analytics endpoint returns the new LLM tracking fields."""
    async with await _client() as client:
        # Register a user (owner of a workspace)
        user, tenant_id = await _register(client, "AnalyticsTest")
        try:
            # Get JWT token for API calls
            token = await _get_jwt_token(client, user.username)
            auth_headers = {"Authorization": f"Bearer {token}"}

            # Create a platform and source
            platform = await Platform.objects.create(
                name=f"tg_test_{uuid.uuid4().hex[:8]}",
                platform_type=PlatformType.TELEGRAM.db_value,
                base_url="https://t.me",
                params={}
            )
            src = await Source.objects.create(
                platform_id=platform.id,
                name="Test Channel",
                source_type=SourceType.CHANNEL.name,
                external_id="test",
                params={},
                is_active=True
            )

            # Call the endpoint via the test client (authenticated via JWT)
            resp = await client.get(f"/api/v1/monitoring/analytics/source/{src.id}", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["source_id"] == src.id
            assert isinstance(data["analytics"], list)
            # Check that the new fields are present in the response structure
            if data["analytics"]:
                analytics_item = data["analytics"][0]
                assert "topic_chain_id" in analytics_item
                assert "llm_model" in analytics_item
                assert "period_type" in analytics_item
        finally:
            await User.objects.delete_user(user.id)
            from app.models.managers.tenant_manager import tenants
            await tenants.delete_by_id(tenant_id)
