"""JWT backend: PyJWT round trip with the app's token helpers; HMAC-only config (no DB)."""

import jwt
import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.utils.token import create_access_token, create_refresh_token


def test_access_token_round_trip_keeps_claims():
    token, _ = create_access_token(42)
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    assert payload["sub"] == "42" and payload["type"] == "access"


def test_refresh_token_is_not_an_access_token():
    token, _ = create_refresh_token(42)
    assert jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])["type"] == "refresh"


def test_wrong_key_and_tampered_token_are_rejected():
    token, _ = create_access_token(42)
    with pytest.raises(jwt.PyJWTError):
        jwt.decode(token, settings.SECRET_KEY + "-other", algorithms=[settings.ALGORITHM])
    with pytest.raises(jwt.PyJWTError):
        jwt.decode(token[:-2] + "xx", settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def test_unsigned_token_is_rejected():
    forged = jwt.encode({"sub": "42", "type": "access"}, None, algorithm="none")
    with pytest.raises(jwt.PyJWTError):
        jwt.decode(forged, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


@pytest.mark.parametrize("algorithm", ["RS256", "ES256", "none", "hs256"])
def test_only_hmac_algorithms_are_configurable(algorithm):
    with pytest.raises(ValidationError):
        Settings(SECRET_KEY="k" * 40, POSTGRES_URL="postgresql://u@h/db", ALGORITHM=algorithm)


def test_python_jose_is_no_longer_imported():
    import pathlib

    for path in pathlib.Path("app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from jose" not in text and "import jose" not in text, path
