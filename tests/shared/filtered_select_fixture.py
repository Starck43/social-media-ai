"""Actual SQLAdmin templates/fields with synthetic mapped rows; no engine or app startup."""

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import Column, ForeignKey, Integer, String, Table
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from starlette.applications import Starlette
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("isolated_filtered_select", ROOT / "app/admin/filtered_select.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
FilteredSelectMultipleField = module.FilteredSelectMultipleField
Base = declarative_base()
links = Table(
    "synthetic_task_sources",
    Base.metadata,
    Column("task_id", ForeignKey("synthetic_tasks.id"), primary_key=True),
    Column("source_id", ForeignKey("synthetic_sources.id"), primary_key=True),
)


class Source(Base):
    __tablename__ = "synthetic_sources"
    id = Column(Integer, primary_key=True)
    name = Column(String)

    def __str__(self):
        return self.name


class AgentTask(Base):
    __tablename__ = "synthetic_tasks"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    sources = relationship(Source, secondary=links)


def source_configuration():
    """Execute only the real view's sources config, not app imports or methods."""
    tree = ast.parse((ROOT / "app/admin/views.py").read_text())
    view = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AgentTaskAdmin")
    values = {}
    for node in view.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        name = node.targets[0].id
        if name not in {"form_overrides", "form_args"}:
            continue
        for key, value in zip(node.value.keys, node.value.values):
            if isinstance(key, ast.Constant) and key.value == "sources":
                values[name] = eval(
                    compile(ast.Expression(value), "actual-source-config", "eval"),
                    {"FilteredSelectMultipleField": FilteredSelectMultipleField},
                )
    assert set(values) == {"form_overrides", "form_args"}
    return values


def task_console(count=60, allowed=True):
    config = source_configuration()

    class TaskView(ModelView, model=AgentTask):
        name = "Задача"
        form_columns = ["name", "sources"]
        form_overrides = {"sources": config["form_overrides"]}
        form_args = {"sources": config["form_args"]}
        column_list = ["id", "name"]

    class Auth(AuthenticationBackend):
        async def authenticate(self, request):
            return allowed

    app = Starlette()
    app.mount("/static", StaticFiles(directory=ROOT / "app/static"), name="static")
    admin = Admin(
        app,
        session_maker=sessionmaker(),
        authentication_backend=Auth("synthetic-key"),
        templates_dir=str(ROOT / "app/templates"),
    )
    # Explicit navigation hints stand in for the registered target's grants.
    admin.templates.env.globals["related_controls"] = lambda *args: [
        {
            "field": "sources",
            "label": "Источники",
            "identity": "source",
            "create": "https://fixture.invalid/admin/source/create",
            "edit": "https://fixture.invalid/admin/source/edit/__related_pk__",
        }
    ]
    admin.templates.env.globals["related_token"] = lambda *args: None
    admin.add_view(TaskView)
    view = admin._views[0]
    options = [Source(id=i, name=f"Источник {i:03} — VK.news / " + "длинное_имя_" * 4) for i in range(1, count + 1)]
    task = AgentTask(id=2027, name="Synthetic task", sources=[options[-1]] if options else [])
    view.get_object_for_edit = AsyncMock(return_value=task)
    view.update_model = AsyncMock(return_value=task)
    view.insert_model = AsyncMock(return_value=task)
    session = Mock()
    session.execute.return_value.scalars.return_value.unique.return_value.all.return_value = options
    factory = Mock(class_=object)
    factory.return_value.__enter__ = Mock(return_value=session)
    factory.return_value.__exit__ = Mock(return_value=False)
    view.session_maker = factory
    return NS(app=app, admin=admin, view=view, options=options, task=task)
