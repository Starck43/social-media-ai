"""Real PostgreSQL two-connection check for `cli.main pii rotate` (P13-T02).

The SQLite tests prove the contract; this proves it on PostgreSQL, where the
interleaving is genuine rather than simulated. It needs no project schema: it
creates, rotates and drops a disposable schema of its own.

    POSTGRES_URL=postgresql+psycopg://user@host:5432/db python scripts/ci/pii_rotation_pg.py

Exit code 0 means all checks passed. The script never touches a project table and
drops its schema even when a check fails.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cryptography.fernet import Fernet  # noqa: E402
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core import pii  # noqa: E402
from app.core.config import settings  # noqa: E402
from cli.commands import pii as pii_cli  # noqa: E402

SCHEMA = "pii_rotation_check"


def _setup(url: str):
    engine = create_engine(url)
    metadata = MetaData()
    probe = Table(
        "probe",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email_encrypted", String(512)),
        Column(pii_cli.ROTATION_MARKER_COLUMN, Integer, nullable=False),
        schema=SCHEMA,
    )
    with engine.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    metadata.create_all(engine)
    return engine, probe


def _reset(engine, probe, value: str, *, key: int = 0) -> None:
    """Seed one row on the given key index (0 = active, 1 = retired)."""
    token = pii.encryption_keys()[key].encrypt(value.encode()).decode()
    with engine.begin() as connection:
        connection.execute(probe.delete())
        connection.execute(
            probe.insert().values(
                id=1,
                email_encrypted=f"{pii.ENCRYPTION_PREFIX}{token}",
                **{pii_cli.ROTATION_MARKER_COLUMN: 1},
            )
        )


def _stored(engine, probe):
    with engine.connect() as connection:
        row = connection.execute(
            text(f"SELECT email_encrypted, {pii_cli.ROTATION_MARKER_COLUMN} FROM {SCHEMA}.probe WHERE id = 1")
        ).first()
    return row[0], row[1]


def _rotate_against_committed_write(engine, probe, *, write_ciphertext):
    """Run rotation while a second connection commits a write between read and update."""

    class Interleaving(Session):
        def execute(self, statement, *args, **kwargs):
            result = super().execute(statement, *args, **kwargs)
            if self.info.get("fired") or not str(statement).lstrip().upper().startswith("SELECT"):
                return result
            self.info["fired"] = True
            with engine.begin() as other:
                other.execute(
                    probe.update()
                    .where(probe.c.id == 1)
                    .values(
                        email_encrypted=write_ciphertext,
                        **{pii_cli.ROTATION_MARKER_COLUMN: probe.c[pii_cli.ROTATION_MARKER_COLUMN] + 1},
                    )
                )
            return result

    with Interleaving(engine) as session:
        return pii_cli._rotate_table(session, "probe", "email_encrypted", batch=10, limit=None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="sync SQLAlchemy URL; defaults to POSTGRES_URL or local postgres")
    args = parser.parse_args(argv)

    settings.PII_INDEX_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()
    settings.CREDENTIALS_KEY = None
    old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    settings.PII_ENCRYPTION_KEYS = old_key

    url = args.url or os.environ.get("POSTGRES_URL") or "postgresql+psycopg://admin@localhost:5432/social_manager"
    url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://").replace(
        "postgresql://", "postgresql+psycopg://"
    )
    engine, probe = _setup(url)
    pii_cli._table = lambda session, name, column: probe  # type: ignore[assignment]
    failures: list[str] = []
    try:
        # 1. Clean rotation, then a rerun after the old key is retired.
        _reset(engine, probe, "anna@example.com", key=0)
        settings.PII_ENCRYPTION_KEYS = f"{new_key},{old_key}"
        with Session(engine) as session:
            first = pii_cli._rotate_table(session, "probe", "email_encrypted", batch=10, limit=None)
        value_before, _marker = _stored(engine, probe)
        settings.PII_ENCRYPTION_KEYS = new_key
        with Session(engine) as session:
            rerun = pii_cli._rotate_table(session, "probe", "email_encrypted", batch=10, limit=None)
        value_after, _marker = _stored(engine, probe)
        if first != (1, 1, 0):
            failures.append(f"clean rotation counts {first}, expected (1, 1, 0)")
        if rerun != (1, 0, 0):
            failures.append(f"rerun after retirement counts {rerun}, expected (1, 0, 0)")
        if value_before != value_after:
            failures.append("rerun after retirement rewrote the row")
        if pii.decrypt_pii(value_after) != "anna@example.com":
            failures.append("rotation produced an undecryptable value")

        # 2. A concurrent write of a different value is preserved and counted.
        settings.PII_ENCRYPTION_KEYS = f"{new_key},{old_key}"
        legacy = f"{pii.ENCRYPTION_PREFIX}{pii.encryption_keys()[1].encrypt(b'anna@example.com').decode()}"
        with engine.begin() as connection:
            connection.execute(probe.delete())
            connection.execute(
                probe.insert().values(id=1, email_encrypted=legacy, **{pii_cli.ROTATION_MARKER_COLUMN: 1})
            )
        winner = pii.encrypt_pii("boris@example.com")
        counts = _rotate_against_committed_write(engine, probe, write_ciphertext=winner)
        stored, _marker = _stored(engine, probe)
        if counts != (1, 0, 1):
            failures.append(f"different-value concurrency counts {counts}, expected (1, 0, 1)")
        if stored != winner:
            failures.append("a concurrent write was overwritten")

        # 3. A concurrent write that keeps the ciphertext identical is still a conflict.
        with engine.begin() as connection:
            connection.execute(probe.delete())
            connection.execute(
                probe.insert().values(id=1, email_encrypted=legacy, **{pii_cli.ROTATION_MARKER_COLUMN: 1})
            )
        counts = _rotate_against_committed_write(engine, probe, write_ciphertext=legacy)
        stored, marker = _stored(engine, probe)
        if counts != (1, 0, 1):
            failures.append(f"same-ciphertext concurrency counts {counts}, expected (1, 0, 1)")
        if stored != legacy or marker != 2:
            failures.append("a concurrent write storing identical ciphertext was overwritten")

        # 4. The command refuses a table without the marker.
        with engine.begin() as connection:
            connection.execute(text(f"CREATE TABLE {SCHEMA}.no_marker (id integer primary key, email text)"))
        no_marker = Table("no_marker", MetaData(), autoload_with=engine, schema=SCHEMA)
        pii_cli._table = lambda session, name, column: no_marker  # type: ignore[assignment]
        try:
            with Session(engine) as session:
                pii_cli._rotate_table(session, "no_marker", "email", batch=10, limit=None)
            failures.append("a table without the marker was accepted")
        except pii.PIIError:
            pass
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        engine.dispose()

    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        "PASS: rotation guard verified on PostgreSQL (clean run, rerun after retirement, "
        "different-value and same-ciphertext concurrency, missing marker refused)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
