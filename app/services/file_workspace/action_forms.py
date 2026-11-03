"""Session/actor/tenant/object-bound POST tokens and bounded strict form parsing."""

import hashlib
import hmac
import json
import re
import secrets
from urllib.parse import parse_qsl

MAX_FORM_BYTES = 2048


def action_token(session, key, *, tenant_id, actor_id, connection_id, object_id):
    if (
        type(key) is not str
        or not key
        or any(type(v) is not int or v <= 0 for v in (tenant_id, actor_id, connection_id, object_id))
    ):
        raise ValueError
    nonce = session.get("_file_actions_nonce")
    if type(nonce) is not str or re.fullmatch(r"[0-9a-f]{64}", nonce) is None:
        nonce = secrets.token_hex(32)
        session["_file_actions_nonce"] = nonce
    message = json.dumps(
        ["file-actions-v1", nonce, tenant_id, actor_id, connection_id, object_id], separators=(",", ":")
    ).encode()
    return hmac.new(key.encode(), message, hashlib.sha256).hexdigest()


async def action_form(request, allowed, expected_token):
    content_type = request.headers.get("content-type", "").lower()
    if content_type not in (
        "application/x-www-form-urlencoded",
        "application/x-www-form-urlencoded; charset=utf-8",
    ) or request.headers.get("content-encoding"):
        raise ValueError
    length = request.headers.get("content-length")
    if length is not None and (not length.isascii() or not length.isdigit() or int(length) > MAX_FORM_BYTES):
        raise ValueError
    body = bytearray()
    async for chunk in request.stream():
        if type(chunk) is not bytes or len(body) + len(chunk) > MAX_FORM_BYTES:
            raise ValueError
        body.extend(chunk)
    if length is not None and len(body) != int(length):
        raise ValueError
    if re.search(rb"%(?![0-9a-fA-F]{2})", body):
        raise ValueError
    pairs = parse_qsl(
        bytes(body).decode("utf-8", errors="strict"),
        keep_blank_values=True,
        strict_parsing=True,
        max_num_fields=8,
        errors="strict",
    )
    if len(pairs) != len(allowed) or any(type(k) is not str or k not in allowed for k, v in pairs):
        raise ValueError
    result = dict(pairs)
    if set(result) != set(allowed) or len(result) != len(pairs):
        raise ValueError
    token = result.get("_csrf", "")
    if not token.isascii() or len(token) != 64 or not hmac.compare_digest(token, expected_token):
        raise ValueError
    return result
