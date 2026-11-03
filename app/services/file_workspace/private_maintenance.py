"""Trusted bounded cleanup for expired private file insights.

Called only inside the platform tenant bypass owned by the existing maintenance
loop. No content or key is selected; attempt deletion cascades encrypted results.
"""

from app.core.tenant_context import is_bypass
from app.models.managers.file_insight_manager import FileInsightManager, PrivateInsightUnavailable


async def purge_expired_private_insights(*, limit: int = 100) -> int:
    """Purge at most 100 expired rows under the established trusted bypass."""
    if not is_bypass() or type(limit) is not int or not 1 <= limit <= 100:
        raise PrivateInsightUnavailable()
    try:
        return await FileInsightManager._purge_expired_batch(limit)
    except PrivateInsightUnavailable:
        raise
    except Exception:
        raise PrivateInsightUnavailable() from None
