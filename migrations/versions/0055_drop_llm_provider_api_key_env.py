"""drop llm_providers.api_key_env (deprecated env fallback)

`api_key_env` held the *name* of an environment variable, and `get_api_key()`
read the secret from `os.getenv` when no `encrypted_api_key` was stored. Every
provider now stores an encrypted key, so the fallback is dead weight that also
let a key live outside the vault.

The upgrade refuses to drop the column while a provider still relies on the
env fallback (name set, no encrypted key) — set the key in the admin first,
then re-run. This keeps a working provider from silently losing its key.

Revision ID: 0055
Revises: 0054
Create Date: 2026-09-25 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0055"
down_revision: Union[str, Sequence[str], None] = "0054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "social_manager"


def upgrade() -> None:
    connection = op.get_bind()
    query = sa.text(
        f"SELECT name, api_key_env FROM {SCHEMA}.llm_providers "
        "WHERE api_key_env IS NOT NULL AND encrypted_api_key IS NULL ORDER BY name"
    )
    relying_on_env = connection.execute(query).fetchall()

    if relying_on_env:
        details = ", ".join(f"{name} (env: {env_name})" for name, env_name in relying_on_env)
        raise RuntimeError(
            "Cannot drop llm_providers.api_key_env: these providers have no "
            f"encrypted_api_key and would lose access to their API key — {details}. "
            "Store the key in the admin (Провайдеры LLM → API ключ) and re-run."
        )

    op.drop_column("llm_providers", "api_key_env", schema=SCHEMA)


def downgrade() -> None:
    op.add_column(
        "llm_providers",
        sa.Column("api_key_env", sa.String(100), nullable=True),
        schema=SCHEMA,
    )
