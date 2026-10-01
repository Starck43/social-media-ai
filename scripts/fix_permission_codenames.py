"""
Fix malformed permission codenames/names from id=49 onwards.

Bug: permissions created after id=49 embedded a Python tuple repr of the
ActionType as the action token, e.g.

    ai.LLMModel.('view', 'Просмотр', '👀')

instead of the lowercase db value used by the pre-49 records:

    ai.LLMModel.view

This script rewrites those to the correct dotted form (app.Model.action),
keeping the CamelCase model segment already present in the rows.
"""
import re
import sys
from pathlib import Path

project_root = str(Path(__file__).parent.parent)
sys.path.append(project_root)

from sqlalchemy import text

from app.core.config import settings
from app.core.database import SessionLocal

# Matches the trailing tuple repr in a codename/name action token.
# Group 1 is the action db_value (view/create/update/...).
TUPLE_RE = re.compile(r"\('([a-z]+)',\s*'[^']*',\s*'[^']*'\)")


def fix_permissions(dry_run: bool = True):
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                f"""
                SELECT id, codename, name, action_type
                FROM "{settings.DB_SCHEMA}".permissions
                WHERE id >= 49
                ORDER BY id
                """
            )
        ).fetchall()

        changes = []
        for pid, codename, name, action_type in rows:
            new_codename = codename
            new_name = name

            cm = TUPLE_RE.search(codename)
            nm = TUPLE_RE.search(name or "")

            if cm:
                action = cm.group(1)
                app_model, _sep, _rest = codename.rpartition(".")
                new_codename = f"{app_model}.{action}"

            if nm:
                action = nm.group(1)
                # name format: "Can ('view', ...) Llmmodel"
                m = re.match(r"^Can\s+\([^)]*\)\s+(.*)$", name)
                suffix = m.group(1) if m else ""
                new_name = f"Can {action} {suffix}".strip()

            if new_codename != codename or new_name != name:
                changes.append((pid, codename, new_codename, name, new_name, action_type))

        print(f"Found {len(changes)} permissions to fix\n")
        for pid, old_c, new_c, old_n, new_n, at in changes:
            print(f"ID {pid} [{at}]:")
            print(f"  codename: {old_c!r}")
            print(f"         -> {new_c!r}")
            print(f"  name:     {old_n!r}")
            print(f"         -> {new_n!r}")
            print()

        if dry_run:
            print("DRY RUN — no changes applied.")
            return

        for pid, _old_c, new_c, _old_n, new_n, _at in changes:
            db.execute(
                text(
                    f"""
                    UPDATE "{settings.DB_SCHEMA}".permissions
                    SET codename = :codename,
                        name = :name,
                        updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"codename": new_c, "name": new_n, "id": pid},
            )
        db.commit()
        print(f"✅ Applied fixes to {len(changes)} permissions")

    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    dry_run = "--apply" not in sys.argv
    fix_permissions(dry_run=dry_run)
