"""Pure ranking: no DB, provider, LLM or external network access."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

from app.services.communications.recommend import ChannelSignals, rank_channels


@pytest.fixture(autouse=True)
def no_db():
    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden in ranking tests")

    event.listen(Dialect, "do_connect", forbidden)
    yield
    event.remove(Dialect, "do_connect", forbidden)


NOW = datetime(2026, 11, 7, tzinfo=timezone.utc)


def candidate(channel, *, consent=True, reachable=True, inbound=0, outbound=0, last=None):
    return ChannelSignals(channel, consent, reachable, inbound, outbound, last)


def test_no_eligible_candidate_never_falls_back_to_preference():
    result = rank_channels(
        [candidate("email", consent=False), candidate("telegram", reachable=False)], preferred_channel="email", now=NOW
    )
    assert result.channel is None and result.confidence == 0
    assert result.reasons == ("no_consented_reachable_channel",)


def test_manual_preference_cannot_override_consent_or_reachability():
    items = [candidate("email", consent=False, inbound=50, last=NOW), candidate("telegram", inbound=1, last=NOW)]
    result = rank_channels(items, preferred_channel="email", now=NOW)
    assert result.channel == "telegram" and "manual_preference" not in result.reasons
    items[0] = candidate("email", inbound=0)
    result = rank_channels(items, preferred_channel="email", now=NOW)
    assert result.channel == "email" and "manual_preference" in result.reasons


def test_recent_inbound_and_frequency_rank_deterministically():
    a = candidate("email", inbound=20, last=NOW - timedelta(days=50))
    b = candidate("telegram", inbound=2, last=NOW - timedelta(days=1))
    assert rank_channels([a, b], now=NOW).channel == "telegram"
    old_very_frequent = candidate("email", inbound=10000, last=NOW - timedelta(days=10))
    new_sparse = candidate("telegram", inbound=0, last=NOW)
    assert rank_channels([old_very_frequent, new_sparse], now=NOW).channel == "telegram"
    tie = [candidate("telegram", inbound=2, last=NOW), candidate("email", inbound=2, last=NOW)]
    assert rank_channels(tie, now=NOW).channel == rank_channels(list(reversed(tie)), now=NOW).channel == "email"


def test_unobserved_channel_has_low_confidence_and_future_activity_ignored():
    sparse = rank_channels([candidate("email", last=NOW + timedelta(days=1))], now=NOW)
    assert sparse.confidence == 0.2 and "recent_inbound_activity" not in sparse.reasons
    full = rank_channels([candidate("email", inbound=20, last=NOW)], now=NOW)
    assert sparse.confidence < full.confidence < 1


@pytest.mark.parametrize(
    "items",
    [
        [candidate("email"), candidate("email")],
        [candidate("unknown")],
        [candidate("email", inbound=-1)],
        [candidate("email", outbound=True)],
    ],
)
def test_invalid_inputs_are_rejected(items):
    with pytest.raises(ValueError):
        rank_channels(items, now=NOW)


def test_naive_clock_rejected():
    with pytest.raises(ValueError):
        rank_channels([], now=datetime(2026, 11, 7))
