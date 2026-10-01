from app.types import ActionType


def generate_permission_codename(app_label: str, model_name: str, action: ActionType) -> str:
	"""
	Generating codename in format: <app_label>.<model_name>.<action>

	Usage:
	- app.post.view
	- app.user.edit
	- app.analytics.view

	`action.db_value` is the stable lowercase token ("view", "create", ...).
	`action.value` is the display tuple and must never be used here — it used
	to be, which produced codenames like
	`social.AgentTask.('view', 'Просмотр', '👀')` in the permissions table.

	Codenames are a display/storage key only: nothing checks access against them
	any more (the broken rows make that impossible and the structured columns in
	`permissions` are the real source), so do not parse them back — ask
	`User.model_permissions()` instead.
	"""
	return f"{app_label.lower()}.{model_name.lower()}.{action.db_value}"
