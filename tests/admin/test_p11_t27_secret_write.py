"""P11-T27 S3: a console secret is sealed before the write, never after it.

The regression this file pins: the old contract encrypted in
``after_model_change``, i.e. *after* SQLAdmin had already applied the accepted
plaintext to the row and committed it. The row was rewritten only on the second
pass, so a plaintext secret reached the database once. The console now seals
``data`` inside ``on_model_change``, which SQLAdmin runs before
``_set_attributes``/``commit``.

Two more rules come with it: a blank field keeps the stored secret (an operator
editing only the label must not retype a token), and the edit form never renders
stored ciphertext back to the browser.

Source-only: no database, no console mount, no HTTP server.
"""

from __future__ import annotations

# `tests/conftest.py` skips the suite database when only source-only modules are selected.
__source_only__ = True

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app.admin.secrets import ENCRYPTED_PREFIX, hide_form_secret, is_sealed, seal_form_secret
from app.admin.views import LLMProviderAdmin, UserCredentialAdmin

SUPERUSER = SimpleNamespace(id=2, username="starck", is_superuser=True, is_active=True)
PLAIN = "sk-live-0123456789"


def _request(actor) -> SimpleNamespace:
    request = SimpleNamespace()
    request.state = SimpleNamespace(admin_user=actor)
    return request


def _run(coro):
    return asyncio.run(coro)


def _view(cls):
    """The real class with sqladmin's constructor skipped.

    The overrides call ``super()``, which needs an instance of the class they
    name, while sqladmin's constructor needs a session maker and an admin
    instance these rules never read.
    """
    instance = object.__new__(type(f"_Double{cls.__name__}", (cls,), {"__init__": lambda self: None}))
    instance.model = cls.model
    instance._form_prop_names = list(getattr(cls, "form_columns", []) or [])
    instance.session_maker = None
    return instance


# --- the helper contract --------------------------------------------------


def test_plaintext_is_replaced_with_ciphertext_in_place():
    from app.utils.crypto import decrypt_secret

    data = {"encrypted_api_key": PLAIN}
    seal_form_secret(data, "encrypted_api_key", None)

    sealed = data["encrypted_api_key"]
    assert sealed != PLAIN
    assert sealed.startswith(ENCRYPTED_PREFIX)
    assert is_sealed(sealed) is True
    assert decrypt_secret(sealed) == PLAIN


def test_an_already_sealed_value_is_not_encrypted_twice():
    from app.utils.crypto import encrypt_secret

    sealed = encrypt_secret(PLAIN)
    data = {"secret_encrypted": sealed}
    seal_form_secret(data, "secret_encrypted", sealed)

    assert data["secret_encrypted"] == sealed


def test_a_blank_field_keeps_the_stored_secret():
    from app.utils.crypto import encrypt_secret

    stored = encrypt_secret(PLAIN)
    data = {"secret_encrypted": "   "}
    seal_form_secret(data, "secret_encrypted", stored)

    # Dropped from the accepted data, so SQLAdmin leaves the column untouched.
    assert "secret_encrypted" not in data


def test_a_blank_field_on_create_writes_no_secret():
    data = {"secret_encrypted": ""}
    seal_form_secret(data, "secret_encrypted", None)

    assert data["secret_encrypted"] is None


def test_a_non_string_value_is_dropped_instead_of_stored():
    data = {"secret_encrypted": ["not", "a", "secret"]}
    seal_form_secret(data, "secret_encrypted", None)

    assert "secret_encrypted" not in data


def test_an_absent_field_is_left_absent():
    data = {"label": "vk"}
    seal_form_secret(data, "secret_encrypted", None)

    assert data == {"label": "vk"}


def test_the_edit_form_value_is_blanked():
    from app.utils.crypto import encrypt_secret

    data = {"secret_encrypted": encrypt_secret(PLAIN)}
    hide_form_secret(data, "secret_encrypted")

    assert data["secret_encrypted"] == ""


# --- wiring: the views seal through the same helper -----------------------


def test_provider_view_seals_before_the_write():
    from app.utils.crypto import decrypt_secret

    view = _view(LLMProviderAdmin)
    model = SimpleNamespace(encrypted_api_key=None)
    data = {"encrypted_api_key": PLAIN}

    _run(view.on_model_change(data, model, True, _request(SUPERUSER)))

    assert is_sealed(data["encrypted_api_key"])
    assert decrypt_secret(data["encrypted_api_key"]) == PLAIN


def test_provider_view_keeps_the_stored_key_when_the_field_is_blank():
    from app.utils.crypto import encrypt_secret

    stored = encrypt_secret(PLAIN)
    view = _view(LLMProviderAdmin)
    model = SimpleNamespace(encrypted_api_key=stored)
    data = {"encrypted_api_key": ""}

    _run(view.on_model_change(data, model, False, _request(SUPERUSER)))

    assert "encrypted_api_key" not in data
    assert model.encrypted_api_key == stored


def test_credential_view_seals_before_the_write():
    from app.utils.crypto import decrypt_secret

    view = _view(UserCredentialAdmin)
    model = SimpleNamespace(secret_encrypted=None)
    data = {"secret_encrypted": PLAIN}

    _run(view.on_model_change(data, model, True, _request(SUPERUSER)))

    assert is_sealed(data["secret_encrypted"])
    assert decrypt_secret(data["secret_encrypted"]) == PLAIN


def test_credential_view_keeps_the_stored_secret_when_the_field_is_blank():
    from app.utils.crypto import encrypt_secret

    stored = encrypt_secret(PLAIN)
    view = _view(UserCredentialAdmin)
    model = SimpleNamespace(secret_encrypted=stored)
    data = {"secret_encrypted": ""}

    _run(view.on_model_change(data, model, False, _request(SUPERUSER)))

    assert "secret_encrypted" not in data
    assert model.secret_encrypted == stored


@pytest.mark.parametrize("view_cls", [LLMProviderAdmin, UserCredentialAdmin])
def test_both_views_blank_the_ciphertext_on_the_edit_form(view_cls):
    """The override exists on the view itself, not inherited from sqladmin."""
    assert "get_form_data_for_edit" in inspect.getsource(view_cls)


@pytest.mark.parametrize("view_cls", [LLMProviderAdmin, UserCredentialAdmin])
def test_neither_view_encrypts_after_the_commit(view_cls):
    """`after_model_change` must not carry a second, post-commit encryption."""
    source = inspect.getsource(view_cls.after_model_change) if "after_model_change" in view_cls.__dict__ else ""
    assert "encrypt_secret" not in source
