"""Q-5 probe: bounded verdicts from VK response envelopes, no network.

The probe exists so the support answer can be measured in one run; these tests
pin its classification, request shape and secret handling without a live call.
"""

import io
import json

import pytest

from scripts.check_vk_search_capabilities import classify, main, method_specs, probe, run

__source_only__ = True


class Transport:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, params):
        self.calls.append((url, dict(params)))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


def test_profile_restriction_and_rate_limits_get_bounded_verdicts():
    assert classify({"error": {"error_code": 1051, "error_msg": "raw provider text"}}) == (
        False,
        "profile_restriction: app profile cannot call global search",
        0,
    )
    assert classify({"error": {"error_code": 29}}) == (False, "rate_limited: method rate limit reached", 0)
    assert classify({"error": {"error_code": 77}}) == (False, "provider_error: code 77", 0)
    assert classify({"error": {"error_code": "x"}}) == (False, "malformed_error", 0)
    assert classify({"error": None, "response": {"count": 3, "items": [{"id": 1}]}}) == (True, "ok", 1)
    assert classify({"response": []}) == (True, "ok", 0)
    assert classify({"response": {"count": 2}}) == (True, "ok", 2)
    assert classify({}) == (False, "empty_response", 0)
    assert classify("nope") == (False, "malformed_response", 0)


def test_non_json_body_is_reported_without_echoing_it():
    ok, verdict, items = probe(
        method_specs()[0], token="t", api_version="5.199", transport=Transport(Response(ValueError()))
    )
    assert (ok, verdict, items) == (False, "http_200: non-JSON body", 0)


def test_requests_carry_one_page_and_never_fan_out():
    specs = {spec["method"]: spec["params"] for spec in method_specs(owner_id=-7)}
    assert set(specs) == {"users.search", "video.search", "apps.getCatalog", "market.search"}
    assert all(params["count"] == 1 for params in specs.values())
    assert specs["apps.getCatalog"]["platform"] == "web"
    assert specs["market.search"]["owner_id"] == -7
    # Without an owner scope the market call is not attempted at all.
    assert "market.search" not in {spec["method"] for spec in method_specs()}


def test_report_marks_failures_and_token_stays_out_of_the_output():
    transport = Transport(Response({"response": {"count": 1, "items": [{"id": 1}]}}))
    buffer = io.StringIO()
    assert run(method_specs(), token="secret-token", api_version="5.199", transport=transport, out=buffer) is True
    text = buffer.getvalue()
    assert "secret-token" not in text
    assert text.count("items=1") == 3
    assert all(call[1]["access_token"] == "secret-token" for call in transport.calls)


def test_missing_token_and_positive_owner_are_refused_before_any_request(monkeypatch, capsys):
    monkeypatch.delenv("VK_ACCESS_TOKEN", raising=False)
    assert main([]) == 2
    assert "no token" in capsys.readouterr().err

    monkeypatch.setenv("VK_ACCESS_TOKEN", "token")
    assert main(["--owner-id", "12345"]) == 2
    assert "negative" in capsys.readouterr().err


def test_main_returns_one_when_a_method_is_refused(monkeypatch, capsys):
    monkeypatch.setenv("VK_ACCESS_TOKEN", "probe-secret-value")

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, params):
            return Response({"error": {"error_code": 15}} if url.endswith("video.search") else {"response": []})

    monkeypatch.setattr("httpx.Client", Client)
    assert main([]) == 1
    out = capsys.readouterr().out
    assert "access_denied: method not allowed for this token/app" in out
    assert "probe-secret-value" not in out


def test_main_returns_zero_when_every_method_answers(monkeypatch):
    monkeypatch.setenv("VK_ACCESS_TOKEN", "token")

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, params):
            return Response({"response": {"count": 1, "items": [{"id": 1}]}})

    monkeypatch.setattr("httpx.Client", Client)
    assert main([]) == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
