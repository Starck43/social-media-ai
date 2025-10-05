from pathlib import Path

from sqladmin import Admin
from starlette.staticfiles import StaticFiles

from app.admin.csrf import CSRFTokenManager
from app.core.config import settings
from app.core.database import async_engine
from .auth import AdminAuthBackend
from .views import (
	UserAdmin, RoleAdmin, PermissionAdmin, SocialAccountAdmin,
	SocialGroupAdmin, PostAdmin, CommentAdmin, StatisticsAdmin,
	AIAnalysisResultAdmin, NotificationAdmin
)

# Get the project root directory
PROJECT_ROOT = Path(__file__).parent.parent.parent


# # Add 'sqladmin' prefix to template paths
# templates.env.globals['include_sqladmin'] = lambda name: f"sqladmin/{name}"


def setup_admin(app):
	"""Initialize SQLAdmin with all views."""

	# --- CSRF ---
	csrf_manager = CSRFTokenManager(secret_key=settings.SECRET_KEY)
	app.state.csrf_manager = csrf_manager

	from .endpoints import router as admin_router
	app.include_router(admin_router)

	# --- AdminAuthBackend ---
	authentication_backend = AdminAuthBackend(
		secret_key=settings.SECRET_KEY,
		csrf_manager=app.state.csrf_manager
	)

	# --- SQLAdmin instance ---
	admin = Admin(
		app=app,
		engine=async_engine,
		authentication_backend=authentication_backend,
		base_url="/admin",
		title="Social Media AI Admin",
		logo_url="/static/logo.png",
		templates_dir=str(PROJECT_ROOT / "app" / "templates")
	)

	app.state.admin = admin

	admin.templates.env.globals.update({
		"csrf_token": lambda: csrf_manager.generate_token(),
		"settings": settings,
		"debug": settings.DEBUG,
	})

	app.mount("/static", StaticFiles(directory="app/static"), name="static")

	# --- Views configs ---
	view_configs = [
		UserAdmin,
		RoleAdmin,
		PermissionAdmin,
		SocialAccountAdmin,
		SocialGroupAdmin,
		PostAdmin,
		CommentAdmin,
		StatisticsAdmin,
		AIAnalysisResultAdmin,
		NotificationAdmin,
	]

	for view_class in view_configs:
		admin.add_view(view_class)
