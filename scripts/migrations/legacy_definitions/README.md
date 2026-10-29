# Frozen historical schema definitions

These source modules preserve narrowly scoped DDL contracts used by existing
offline verifiers, fixture tooling and migration behavior tests. They are copied
from source commit `045150db19fc402de5b51c18b4fe037388f0846d` and are not
located under Alembic's configured `migrations/versions` directory, so Alembic
cannot discover or apply them.

Do not add new consumers here. New schema work belongs in the current initial
revision and shared runtime model/guard definitions. This folder is a temporary
compatibility seam; Git history remains the archive for all other migration
revisions. Preserve module contents unless a consumer migration is explicitly
assigned and its behavioral checks are moved to the canonical schema contract.
