import pytest
import uuid

from app.api.v1.endpoints.monitoring import get_source_analytics
from app.models import Platform, Source
from app.types.models import PlatformType, SourceType


class DummyUser:
    is_superuser = True


@pytest.mark.asyncio
async def test_get_source_analytics_returns_new_fields():
    # Arrange
    platform = await Platform.objects.create(
        name=f"tg_test_{uuid.uuid4().hex[:8]}",
        platform_type=PlatformType.TELEGRAM.value,
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

    # No analytics yet -> empty list
    resp = await get_source_analytics(src.id, current_user=DummyUser())
    assert resp["source_id"] == src.id
    assert isinstance(resp["analytics"], list)
