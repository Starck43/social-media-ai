from app.types import ActionType


def generate_permission_codename(app_label: str, model_name: str, action: ActionType) -> str:
	"""
	Generating codename in format: <app_label>.<model_name>.<action>

	Usage:
	- app.post.view
	- app.user.edit
	- app.analytics.view
	"""
	return f"{app_label.lower()}.{model_name.lower()}.{action.value.lower()}"


def parse_permission_codename(codename: str) -> tuple[str, str, ActionType]:
	"""
	Parsing codename back into components
	"""
	parts = codename.split('.')
	if len(parts) != 3:
		raise ValueError(f"Invalid codename format: {codename}")

	app_label, model_name, action_value = parts
	try:
		action = ActionType(action_value)
	except ValueError:
		raise ValueError(f"Invalid action type: {action_value}")

	return app_label, model_name, action
