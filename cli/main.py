# cli/main.py
import typer
from .commands import roles

app = typer.Typer(
	name="SMM Admin CLI",
	help="Social Media Manager CLI",
	no_args_is_help=True,
)

app.add_typer(roles.app, name="roles", help="Manage roles and permissions")
# app.add_typer(permissions.app, name="permissions", help="Manage permissions")


@app.callback()
def callback():
	"""Social Media Manager Administration CLI"""


if __name__ == "__main__":
	app()
