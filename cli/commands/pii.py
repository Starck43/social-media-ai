"""Rotate `PII_ENCRYPTION_KEYS` over registered personal-data columns (P13-T02).

Rotation is a three-step operator procedure, and this command is only the middle
step:

1. prepend the new Fernet key to `PII_ENCRYPTION_KEYS` (new key first, old keys
   after) and restart the runtime: new writes use the new key, old rows still
   decrypt;
2. run `cli.main pii rotate --yes` to re-encrypt existing rows with the active
   key;
3. drop the retired key from `PII_ENCRYPTION_KEYS`.

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
REGISTRY: tuple[tuple[str, str, str], ...] = ()


def _run(coro):
    from app.core.tenant_context import tenant_scope

    with tenant_scope(bypass=True):
        return asyncio.run(coro)


def _rotate_table(session, table_name: str, column: str, *, batch: int, limit: int | None) -> tuple[int, int]:
    """Re-encrypt one column in bounded batches. Returns (examined, rewritten)."""
    from app.core.pii import decrypt_pii, encrypt_pii, is_active_ciphertext

    table = _table(session, table_name, column)
    examined = rewritten = 0
    last_id = 0
    while True:
        rows = session.execute(
            select(table.c.id, table.c[column])
            .where(table.c[column].is_not(None), table.c.id > last_id)
            .order_by(table.c.id)
            .limit(batch)
        ).all()
        if not rows:
            break
        for row_id, value in rows:
            examined += 1
            if is_active_ciphertext(value):
                continue
            plaintext = decrypt_pii(value) or ""
            session.execute(table.update().where(table.c.id == row_id).values(**{column: encrypt_pii(plaintext)}))
            rewritten += 1
            if limit is not None and rewritten >= limit:
                session.commit()
                return examined, rewritten
        session.commit()
        last_id = rows[-1][0]
    return examined, rewritten


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
                rprint(f"  {table_name}.{column}")
            return
        from app.core.database import SessionLocal

        session = SessionLocal()
        try:
            for table_name, column, _kind in targets:
                examined, rewritten = _rotate_table(session, table_name, column, batch=batch, limit=limit)
                rprint(f"{table_name}.{column}: examined {examined}, rewritten {rewritten}")
        finally:
            session.close()
    except PIIError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1) from e
