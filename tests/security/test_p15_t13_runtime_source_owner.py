"""Runtime source views retain the owner without weakening explicit pins."""

import pytest

from app.jobs.handlers import _runtime_source
from app.models import Source
from app.services.social.connector_authorizations import AuthorizationUnavailable
from app.services.social.telegram_grant_access import source_grant_selection
from app.types import SourceType

__source_only__ = True


def _row():
    return Source(
        id=11,
        tenant_id=12,
        platform_id=13,
        external_id="synthetic-chat",
        name="Synthetic source",
        source_type=SourceType.CHANNEL,
        owner_user_id=14,
        params={"mode": "user", "telegram_authorization": {"grant_id": 15, "authorization_revision": 2}},
    )


def _view(row, *, task_id=None, owner_user_id=None):
    return _runtime_source(
        row,
        task_id=task_id,
        task_plan_enabled=task_id is not None,
        owner_user_id=owner_user_id,
        cli_dates=None,
        force_refresh=False,
    )


def test_source_owner_is_retained_for_grant_selection():
    row = _row()
    view = _view(row)
    selection = source_grant_selection(view)
    assert selection == source_grant_selection(row)
    assert selection.owner_user_id == row.owner_user_id
    assert "token_owner" not in view.params


@pytest.mark.parametrize("owner", [42, None])
def test_generated_source_keeps_explicit_owner_pin(owner):
    row = _row()
    row.owner_task_id = 7
    view = _view(row, task_id=7, owner_user_id=owner)
    assert view.params["token_owner"] == (owner if owner is not None else 0)
    if owner is None:
        with pytest.raises(AuthorizationUnavailable):
            source_grant_selection(view)
    else:
        assert source_grant_selection(view).owner_user_id == owner
    assert "token_owner" not in row.params
