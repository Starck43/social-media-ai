# cli/commands/roles.py
import typer
from rich.console import Console
from rich.table import Table

from app.models import Role
from app.services.user.permissions import RolePermissionService

app = typer.Typer(name="roles", help="Manage roles and permissions")
console = Console()

SHOW_LIST_COUNT = 5


def _platform(coro):
	"""Run an async CLI body: the CLI is developer mode, so the tenant guard is
	bypassed and the coroutine gets its own event loop."""
	import asyncio

	from app.core.tenant_context import tenant_scope

	with tenant_scope(bypass=True):
		return asyncio.run(coro)


def _codename(role: Role) -> str:
	"""`Role.codename` is loaded as its string value ("ADMIN"), not as the enum."""
	codename = role.codename
	return codename.name if hasattr(codename, "name") else str(codename)


@app.command("list")
def list_roles():
	"""List all roles with their permissions"""
	async def _run() -> list[Role]:
		return list(await Role.objects.prefetch_related("permissions").order_by(Role.id))

	roles = _platform(_run())

	table = Table(title="Roles and Permissions", show_lines=True)
	table.add_column("ID", style="cyan")
	table.add_column("ROLE", style="yellow")
	table.add_column("TABLE PERMISSIONS", style="magenta")

	for role in roles:
		perm_list = ", ".join([p.codename for p in role.permissions[:SHOW_LIST_COUNT]])
		if len(role.permissions) > SHOW_LIST_COUNT:
			perm_list += f"\n... and {len(role.permissions) - SHOW_LIST_COUNT} more"

		table.add_row(
			str(role.id),
			_codename(role),
			perm_list or "No permissions"
		)

	console.print(table)
	console.print(f"Total: {len(roles)} roles\n")


@app.command("show")
def show_role(role_id: int):
	"""Show detailed information about a role"""
	async def _run():
		return await Role.objects.filter(id=role_id).prefetch_related("permissions").first()

	role = _platform(_run())
	if not role:
		console.print(f"❌ [red]Role with ID {role_id} not found[/red]")
		raise typer.Exit(code=1)

	table = Table(title=f"Role: {_codename(role)}")
	table.add_column("Property", style="cyan")
	table.add_column("Value", style="green")

	table.add_row("ID", str(role.id))
	table.add_row("Name", role.name)
	table.add_row("Code", _codename(role))
	table.add_row("Description", role.description or "No description")
	table.add_row("Permission Count", str(len(role.permissions)))
	table.add_row("Permission List", "\n".join([p.codename for p in role.permissions]), style="yellow")

	console.print(table)


@app.command("preset")
def assign_default_permissions():
	"""Assign default permissions to all roles based on hierarchy"""
	try:
		console.print("🔄 [yellow]Assigning default permissions...[/yellow]")
		_platform(RolePermissionService.assign_default_permissions())
		console.print("✅ [green]Done![/green]\n")
	except Exception as e:
		console.print(f"❌ [red]Error: {str(e)}[/red]")
		raise typer.Exit(code=1)


@app.command("update")
def update_role_permissions(
		role: str = typer.Argument(..., help="Role to update (case-insensitive)"),
		permissions: list[str] = typer.Argument(
			...,
			help="""Permission patterns to assign. Supports wildcards and exclusions.
		
			Examples:
			- social.post.view    # Specific permission
			- posts.*            # All permissions for posts
			- *.view             # All view permissions
			- !posts.delete      # Exclude delete permission
			- posts.* !posts.delete  # All post permissions except delete
			"""
		),
		strategy: str = typer.Option(
			"replace",
			"--strategy", "-s",
			help="""Update strategy:
		- replace: Replace all permissions (default)
		- merge: Add new permissions to existing ones
		- synchronize: Sync to match exactly these permissions
		- update_actions: Update actions for resources"""
		),
		dry_run: bool = typer.Option(
			False,
			"--dry-run",
			help="Show what would be changed without making changes"
		)
):
	"""
	Update permissions for a role using patterns and specified strategy.

	The command supports powerful pattern matching with wildcards (*) and exclusions (!).
	"""
	try:
		console.print(f"🔄 [yellow]Updating permissions for role: {role}[/yellow]")
		console.print(f"📋 [bold]Strategy:[/bold] {strategy}")

		if dry_run:
			console.print("\n🔍 [yellow]DRY RUN - No changes will be made[/yellow]")

		# Show expanded permissions for better UX
		expanded = _platform(RolePermissionService.expand_permission_patterns(permissions))
		console.print("\n[bold]Permission patterns:[/bold]")
		for p in permissions:
			console.print(f"  • {p}")

		console.print("\n[bold]Expanded to permissions:[/bold]")
		for p in expanded or ["(no matching permissions)"]:
			console.print(f"  • {p}")

		if dry_run:
			console.print("\n✅ [green]Dry run completed. No changes were made.[/green]")
			return

		# Update role permissions
		result = _platform(RolePermissionService.update_role_permissions(
			role_codename=role.lower(),
			permission_codenames=permissions,
			strategy=strategy
		))

		if not any(result.values()):
			console.print("\nℹ️  [yellow]No changes were made to the role permissions[/yellow]")
			return

		# Display changes in a nice format
		console.print("\n✅ [bold green]Role permissions updated successfully![/bold green]")

		def print_section(title: str, items: list, icon: str, color: str):
			if items:
				console.print(f"\n[bold {color}]{title}:[/bold {color}]")
				for item in sorted(items):
					console.print(f"  {icon} [{color}]{item}[/{color}]")

		print_section("Added permissions", result["added"], "+", "green")
		print_section("Removed permissions", result["removed"], "-", "red")
		print_section("Updated permissions", result["updated"], "↻", "yellow")

		# Show summary
		console.print("\n📊 [bold]Summary:[/bold]")
		console.print(f"  • Specified patterns: {len(permissions)}")
		console.print(f"  • Expanded permissions: {len(expanded)}")
		console.print(f"  • Added: {len(result['added'])}")
		console.print(f"  • Removed: {len(result['removed'])}")
		console.print(f"  • Updated: {len(result['updated'])}")
		console.print(f"  • Unchanged: {len(result['unchanged'])}")

	except ValueError as e:
		console.print(f"\n❌ [red]Error: {str(e)}[/red]")
		raise typer.Exit(code=1)
	except Exception as e:
		import traceback
		console.print(f"\n❌ [red]Unexpected error: {str(e)}[/red]")
		if typer.confirm("\nShow full error details?"):
			console.print("\n" + traceback.format_exc())
		raise typer.Exit(code=1)
