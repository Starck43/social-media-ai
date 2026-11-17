"""Source-only token accounting tests; these cases require no database."""

from types import SimpleNamespace

from app.services.ai.settlement import _tokens

__source_only__ = True


def _event(prompt: int = 100, completion: int = 20):
    return SimpleNamespace(
        dispatch_state="response_received",
        prompt_tokens=prompt,
        completion_tokens=completion,
        cache_usage=None,
    )


def test_openai_cache_counts_are_inclusive_in_prompt_tokens():
    assert (
        _tokens(
            _event(),
            {
                "format": "openai",
                "cache_read_tokens": 25,
                "cache_write_tokens": 0,
                "cache_write_5m_tokens": None,
                "cache_write_1h_tokens": None,
            },
        )
        == 120
    )


def test_anthropic_cache_read_and_write_tokens_are_additive():
    assert (
        _tokens(
            _event(),
            {
                "format": "anthropic",
                "cache_read_tokens": 25,
                "cache_write_tokens": 10,
                "cache_write_5m_tokens": 10,
                "cache_write_1h_tokens": 0,
            },
        )
        == 155
    )


def test_anthropic_missing_cache_counts_remain_unknown():
    assert (
        _tokens(
            _event(),
            {
                "format": "anthropic",
                "cache_read_tokens": None,
                "cache_write_tokens": 0,
                "cache_write_5m_tokens": None,
                "cache_write_1h_tokens": None,
            },
        )
        is None
    )


def test_unknown_cache_format_and_no_dispatch_are_distinct():
    assert (
        _tokens(
            _event(),
            {
                "format": "unknown",
                "cache_read_tokens": None,
                "cache_write_tokens": None,
                "cache_write_5m_tokens": None,
                "cache_write_1h_tokens": None,
            },
        )
        is None
    )
    assert _tokens({"dispatch_state": "not_dispatched"}) == 0
