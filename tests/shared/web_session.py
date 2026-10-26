"""Authenticated ASGI sessions for owned synthetic users; no production secrets."""

import base64
import json
from datetime import datetime, timedelta, timezone

import jwt
from itsdangerous import TimestampSigner

from app.core.config import settings


def access_token(user):
    return jwt.encode(
        {"sub": str(user.id), "type": "access", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def session_cookie(user, workspace_id):
    return (
        TimestampSigner(settings.SECRET_KEY)
        .sign(base64.b64encode(json.dumps({"token": access_token(user), "app_tenant_id": workspace_id}).encode()))
        .decode()
    )
