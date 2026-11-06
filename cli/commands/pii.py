"""Rotate `PII_ENCRYPTION_KEYS` over registered personal-data columns (P13-T02).

Rotation is a three-step operator procedure, and this command is only the middle
step:

1. prepend the new Fernet key to `PII_ENCRYPTION_KEYS` (new key first, old keys
   after) and restart the runtime: new writes use the new key, old rows still
   decrypt;
2. run `cli.main pii rotate --yes` to re-encrypt existing rows with the active
   key;
3. only after every writer uses the new key and an independent full rerun
   reports zero rewrites and zero conflicts, back up and retire the old key.

Only `PII_ENCRYPTION_KEYS` is rotated. Blind indexes are keyed by `PII_INDEX_KEY`,
which D8 keeps stable; rebuilding an index is a separate approved operation, so
index columns are never written here.
"""

from __future__ import annotations

import asyncio

import typer
from rich import print as rprint
from sqlalchemy import MetaData, Table, select

from app.core.pii import PIIError

app = typer.Typer(name="pii", help="Rotate the personal-data encryption key")

# Tables registered for rotation. A business module adds its own entry when it
# owns encrypted columns (P13-T07 is the first); nothing is discovered by name
# guessing, because a wrong guess would re-encrypt the wrong column.
REGISTRY: tuple[tuple[str, str, str | None], ...] = (
    ("outbox_messages", "subject_encrypted", None),
    ("outbox_messages", "content_encrypted", None),
    ("outbox_targets", "recipient_encrypted", None),
    ("planner_tasks", "title_encrypted", None),
    ("planner_tasks", "description_encrypted", None),
    ("planner_tasks", "series_template_encrypted", None),
    ("contacts", "display_name_encrypted", None),
    ("contacts", "notes_encrypted", None),
    ("contacts", "birthday_encrypted", None),
    ("contacts", "anniversary_encrypted", None),
    ("contact_identities", "value_encrypted", None),
    ("contact_identities", "source_native_id_encrypted", None),
    ("contact_consents", "basis_encrypted", None),
    ("contact_consents", "source_evidence_encrypted", None),
    ("contact_merge_journal", "reason_encrypted", None),
    ("crm_entities", "title_encrypted", None),
    ("crm_entities", "personal_fields_encrypted", None),
    ("crm_entities", "custom_fields_encrypted", None),
    ("greeting_history", "subject_encrypted", None),
    ("greeting_history", "draft_text_encrypted", None),
    ("message_threads", "subject_encrypted", None),
    ("message_threads", "participants_encrypted", None),
    ("unified_messages", "subject_encrypted", None),
    ("unified_messages", "sender_encrypted", None),
    ("unified_messages", "recipient_encrypted", None),
)

# Every registered table carries this integer column and every writer bumps it on
# write. Rotation requires it: a ciphertext-only guard cannot see a concurrent
# write that stored byte-identical ciphertext, so "no conflicts" would not mean
# "nobody wrote during the run".
ROTATION_MARKER_COLUMN = "pii_updated_at"


def _run(coro):
    from app.core.tenant_context import tenant_scope

    with tenant_scope(bypass=True):
        return asyncio.run(coro)


def _rotate_table(session, table_name: str, column: str, *, batch: int, limit: int | None) -> tuple[int, int, int]:
    """Re-encrypt using compare-and-set. Returns (examined, rewritten, conflicts).

    The guard is the ciphertext *and* the row's rotation marker (see
    `ROTATION_MARKER_COLUMN`). Ciphertext alone is not enough: a concurrent write
    that stores a different value usually produces different ciphertext, but a
    write of the *same* value re-encrypts to different bytes too (Fernet is not
    deterministic), and a write that reproduces the very same bytes - the same
    plaintext under the same active key - would be invisible. The marker makes
    every conditional write either match or count as a conflict.
    """
    from app.core.pii import decrypt_pii, encrypt_pii, is_active_ciphertext

    if column.endswith("_index"):
        raise PIIError("blind-index columns must not be rotated")
    table = _table(session, table_name, column)
    if ROTATION_MARKER_COLUMN not in table.c:
        raise PIIError(
            f"{ROTATION_MARKER_COLUMN!r} is missing on {table_name!r}; rotation needs a monotonic "
            "marker the writer bumps, otherwise a concurrent write can go undetected"
        )
    examined = rewritten = conflicts = 0
    last_id = 0
    while True:
        rows = session.execute(
            select(table.c.id, table.c[column], table.c[ROTATION_MARKER_COLUMN])
            .where(table.c[column].is_not(None), table.c[column] != "", table.c.id > last_id)
            .order_by(table.c.id)
            .limit(batch)
        ).all()
        if not rows:
            break
        for row_id, value, marker in rows:
            examined += 1
            if is_active_ciphertext(value):
                continue
            plaintext = decrypt_pii(value) or ""
            result = session.execute(
                table.update()
                .where(
                    table.c.id == row_id,
                    table.c[column] == value,
                    table.c[ROTATION_MARKER_COLUMN] == marker,
                )
                .values(
                    **{
                        column: encrypt_pii(plaintext),
                        ROTATION_MARKER_COLUMN: table.c[ROTATION_MARKER_COLUMN] + 1,
                    }
                )
            )
            if result.rowcount == 0:
                conflicts += 1
            elif result.rowcount == 1:
                rewritten += 1
            else:
                raise PIIError("rotation could not verify its conditional update")
            if limit is not None and rewritten >= limit:
                session.commit()
                return examined, rewritten, conflicts
        session.commit()
        last_id = rows[-1][0]
    return examined, rewritten, conflicts


def _table(session, table_name: str, column: str):
    from app.core.config import settings

    metadata = MetaData()
    try:
        table = Table(table_name, metadata, schema=settings.DB_SCHEMA, autoload_with=session.get_bind())
    except Exception as e:  # noqa: BLE001 - surfaced as a safe operator message
        raise PIIError(f"table {table_name!r} is not available in schema {settings.DB_SCHEMA!r}") from e
    if column not in table.c:
        raise PIIError(f"column {column!r} not found on {table_name!r}")
    return table


@app.command("rotate")
def rotate(
    table: str = typer.Option(None, "--table", help="Limit rotation to one registered table"),
    batch: int = typer.Option(200, "--batch", min=1, max=5000, help="Rows per transaction"),
    limit: int = typer.Option(None, "--limit", min=1, help="Stop after rewriting this many rows (resumable)"),
    dry_run: bool = typer.Option(True, "--dry-run/--yes", help="Dry run by default: counts only"),
) -> None:
    """Re-encrypt registered personal-data columns with the active key."""
    try:
        targets = [entry for entry in REGISTRY if table is None or entry[0] == table]
        if not targets:
            rprint("[yellow]No registered personal-data columns to rotate[/yellow]")
            return
        if dry_run:
            rprint(f"[yellow]dry run[/yellow]: {len(targets)} table(s); pass --yes to rewrite rows")
            for table_name, column, _kind in targets:
                rprint(f"  {table_name}.{column} (marker: {ROTATION_MARKER_COLUMN})")
            return
        from app.core.database import SessionLocal

        session = SessionLocal()
        try:
            conflicts = 0
            for table_name, column, _kind in targets:
                examined, rewritten, table_conflicts = _rotate_table(
                    session, table_name, column, batch=batch, limit=limit
                )
                conflicts += table_conflicts
                rprint(
                    f"{table_name}.{column}: examined {examined}, rewritten {rewritten}, conflicts {table_conflicts}"
                )
            if conflicts:
                raise PIIError("rotation encountered concurrent changes; rerun and verify before removing old keys")
        finally:
            session.close()
    except PIIError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1) from e
