import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

eng = create_engine("postgresql+psycopg://admin@localhost:5432/social_manager")
def run(step):
    with eng.begin() as conn:
        cfg = Config("alembic.ini"); cfg.set_main_option("script_location", "migrations")
        cfg.attributes["connection"] = conn
        step(cfg)

def state(label):
    with eng.connect() as c:
        v = c.execute(text("select version_num from test_schema.alembic_version")).scalar()
        t = c.execute(text("select count(*) from information_schema.tables where table_schema='test_schema' and table_name='planner_tasks'")).scalar()
        r = c.execute(text("select count(*) from test_schema.model_types where resource_key='planner.task'")).scalar()
        trg = c.execute(text("select count(*) from information_schema.triggers where event_object_schema='test_schema' and event_object_table='planner_tasks'")).scalar()
        perms = c.execute(text("select count(*) from test_schema.workspace_role_permission w join test_schema.permissions p on p.id=w.permission_id join test_schema.model_types m on m.id=p.model_type_id where m.resource_key='planner.task'")).scalar()
    print(label, v, "table", t, "registry", r, "triggers", trg, "role_perms", perms)

state("start")
for name, step in (("UP", lambda cfg: command.upgrade(cfg, "0140")),
                   ("DOWN", lambda cfg: command.downgrade(cfg, "0139")),
                   ("UP2", lambda cfg: command.upgrade(cfg, "0140")),
                   ("DOWN2", lambda cfg: command.downgrade(cfg, "0139")),
                   ("UP3", lambda cfg: command.upgrade(cfg, "0140"))):
    run(step); state(name)
