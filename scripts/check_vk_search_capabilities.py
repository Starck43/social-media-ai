#!/usr/bin/env python3
"""Probe the four VK search methods Q-5 still gates, with one read-only request each.

The category registry (``app/services/collection/search_categories.py``) keeps
`people`, `video`, `apps` and `market` at ``mocked`` because no live
method/profile evidence exists yet. This script turns the support answer into a
single measured run: it calls each official method once with ``count=1`` and
prints one bounded verdict per method.

Safety:

- read-only GET requests, ``count=1``, one attempt per method, no retry/fan-out;
- the token comes from an environment variable (default ``VK_ACCESS_TOKEN``) and
  is never printed, logged or written to disk;
- no database access, no credential lookup, no category status change.

Exit codes: ``0`` every method answered, ``1`` at least one method refused,
``2`` no token supplied.
"""

import argparse
import json
import os
import sys

BASE_URL = "https://api.vk.com/method"
DEFAULT_API_VERSION = "5.199"
DEFAULT_TOKEN_ENV = "VK_ACCESS_TOKEN"

# Bounded VK error codes this probe can explain without quoting provider text.
ERROR_VERDICTS = {
    5: "auth_failed: token rejected or expired",
    6: "rate_limited: too many requests per second",
    9: "rate_limited: too many requests of this type per minute",
    15: "access_denied: method not allowed for this token/app",
    29: "rate_limited: method rate limit reached",
    100: "invalid_parameters: the documented parameter set was rejected",
    1051: "profile_restriction: app profile cannot call global search",
}


def method_specs(owner_id=None):
    """The four gated methods with the smallest legal request for each."""
    specs = [
        {"method": "users.search", "params": {"q": "test", "count": 1}},
        {"method": "video.search", "params": {"q": "test", "count": 1, "extended": 1}},
        {"method": "apps.getCatalog", "params": {"q": "test", "count": 1, "extended": 1, "platform": "web"}},
    ]
    if owner_id is not None:
        specs.append(
            {
                "method": "market.search",
                "params": {"q": "test", "count": 1, "offset": 0, "extended": 1, "owner_id": owner_id},
            }
        )
    return specs


def classify(payload):
    """Map one VK JSON envelope to ``(ok, verdict, items)`` without provider text."""
    if not isinstance(payload, dict):
        return False, "malformed_response", 0
    error = payload.get("error")
    if isinstance(error, dict):
        try:
            code = int(error.get("error_code"))
        except (TypeError, ValueError):
            return False, "malformed_error", 0
        return False, ERROR_VERDICTS.get(code, f"provider_error: code {code}"), 0
    response = payload.get("response")
    if isinstance(response, list):
        return True, "ok", len(response)
    if isinstance(response, dict):
        items = response.get("items")
        count = response.get("count")
        if isinstance(items, list):
            return True, "ok", len(items)
        if isinstance(count, int):
            return True, "ok", count
        return True, "ok", 0
    return False, "empty_response", 0


def probe(spec, *, token, api_version, transport, base_url=BASE_URL):
    """Call one method once and classify the answer. ``transport`` is an httpx-like client."""
    response = transport.get(
        f"{base_url}/{spec['method']}",
        params={**spec["params"], "access_token": token, "v": api_version},
    )
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return False, f"http_{response.status_code}: non-JSON body", 0
    return classify(payload)


def run(specs, *, token, api_version, transport, out=sys.stdout):
    """Probe every spec and print a fixed-width report; returns True when all answered."""
    rows = []
    ok_all = True
    for spec in specs:
        ok, verdict, items = probe(spec, token=token, api_version=api_version, transport=transport)
        ok_all = ok_all and ok
        rows.append((spec["method"], ok, verdict, items))
        out.write(f"{spec['method']:<18} {'ok' if ok else 'no':<3} {verdict:<58} items={items}\n")
    return ok_all


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--token-env",
        default=DEFAULT_TOKEN_ENV,
        help=f"environment variable holding the token (default {DEFAULT_TOKEN_ENV})",
    )
    parser.add_argument("--api-version", default=DEFAULT_API_VERSION)
    parser.add_argument("--owner-id", type=int, default=None, help="community owner id for market.search (negative)")
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args(argv)

    token = os.environ.get(args.token_env, "").strip()
    if not token:
        print(f"no token: set {args.token_env}", file=sys.stderr)
        return 2
    if args.owner_id is not None and args.owner_id >= 0:
        print("owner id for a community market is negative, e.g. --owner-id -12345", file=sys.stderr)
        return 2

    import httpx

    print(f"api_version={args.api_version} methods={len(method_specs(args.owner_id))} (token hidden)")
    with httpx.Client(timeout=15.0) as transport:
        ok = run(
            method_specs(args.owner_id),
            token=token,
            api_version=args.api_version,
            transport=transport,
            out=sys.stdout,
        )
    if args.owner_id is None:
        print("market.search skipped: pass --owner-id <negative community id> to probe it")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
