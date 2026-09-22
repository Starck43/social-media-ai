import os
import sys
from pathlib import Path
from typing import Iterable, Collection, Any, Mapping

from alembic import context
from alembic.operations import MigrationScript
from alembic.runtime.migration import MigrationContext, MigrationInfo
from dotenv import load_dotenv
from sqlalchemy import engine_from_config

project_root = str(Path(__file__).resolve().parent.parent)
sys.path.append(project_root)

load_dotenv(os.path.join(project_root, '.env'))

try:
	from app.core.config import settings
	from app.models import Base

except ImportError as e:
	print(f"Error importing modules: {e}")
	raise

# Set the target metadata for autogenerate support
target_metadata = Base.metadata
config = context.config

# Configure migration settings
config.set_main_option('sqlalchemy.url', settings.POSTGRES_URL)
config.set_main_option('version_path_separator', '_')


def include_object(_, __, type_, reflected, compare_to):
	"""Don’t generate any DROP TABLE directives with autogenerate."""

	if type_ == "table" and reflected and compare_to is None:
		return False
	else:
		return True


def process_revision_directives(
		ctx: MigrationContext,
		revision: str | Iterable[str | None] | Iterable[str],
		directives: list[MigrationScript],
):
	"""
	A function that will be passed a structure representing the result of
	an autogenerate or plain “revision” operation.
	"""
	script = directives[0]

	from alembic.script import ScriptDirectory
	script_dir = ScriptDirectory.from_config(config)

	revs = []

	for rev in script_dir.walk_revisions():
		if rev and rev.revision and rev.revision.isdigit():
			revs.append(int(rev.revision))

	script.rev_id = '0001' if not revs else f"{max(revs) + 1:04d}"


def on_version_apply(
		ctx: MigrationContext,
		step: MigrationInfo,
		heads: Collection[Any],
		run_args: Mapping[str, Any]
) -> None:
	"""Handle model type registration after migrations."""
	from scripts.migrations.register_model_types import register_model_types

	print('Applied revision: ', ctx.get_current_revision())

	# Check if this is an upgrade or downgrade
	is_upgrade = step.is_upgrade

	register_model_types(ctx.connection, is_upgrade=is_upgrade)


def run_migrations_online():
	engine = engine_from_config(
		config.get_section(config.config_ini_section)
	)

	with engine.connect() as connection:
		context.configure(
			connection=connection,
			target_metadata=target_metadata,
			dialect_opts={"paramstyle": "named"},
			include_schemas=True,
			compare_type=True,
			compare_server_default=True,
			process_revision_directives=process_revision_directives,
			on_version_apply=on_version_apply,
		)

		with context.begin_transaction():
			context.run_migrations()


def run_migrations_offline() -> None:
	"""Run migrations in 'offline' mode.

	This configures the context with just a URL
	and not an Engine, though an Engine is acceptable
	here as well. By skipping the Engine creation
	we don't even need a DBAPI to be available.

	Calls to context.execute() here emit the given string to the
	script output.
	"""
	context.configure(
		url=settings.POSTGRES_URL,
		target_metadata=target_metadata,
		literal_binds=True,
		dialect_opts={"paramstyle": "named"},
		version_path_separator='_',
		include_schemas=True,
		compare_type=True,
		compare_server_default=True,
		process_revision_directives=process_revision_directives,
	)

	with context.begin_transaction():
		context.run_migrations()


if context.is_offline_mode():
	run_migrations_offline()
else:
	run_migrations_online()
