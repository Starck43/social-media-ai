from datetime import datetime
from fastapi import Request, HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from starlette.middleware.base import BaseHTTPMiddleware


class CSRFTokenManager:
	"""CSRF-token manager with expiration."""

	def __init__(self, secret_key: str, expire_minutes: int = 30):
		self.serializer = URLSafeTimedSerializer(secret_key, salt="csrf-token")
		self.expire_seconds = expire_minutes * 60

	def generate_token(self) -> str:
		payload = {"csrf": True, "ts": datetime.now().isoformat()}
		return self.serializer.dumps(payload, salt="csrf-token")

	def verify_token(self, token: str) -> bool:
		if not token:
			return False
		try:
			payload = self.serializer.loads(
				token,
				salt="csrf-token",
				max_age=self.expire_seconds,
			)
			return isinstance(payload, dict) and payload.get("csrf") is True
		except (BadSignature, SignatureExpired):
			return False


def get_csrf_manager(request: Request) -> CSRFTokenManager:
	csrf_manager = getattr(request.app.state, "csrf_manager", None)
	if not csrf_manager:
		raise HTTPException(500, "CSRF protection not configured")
	return csrf_manager


class CSRFMiddleware(BaseHTTPMiddleware):
	"""Middleware for CSRF-token validation."""

	async def dispatch(self, request: Request, call_next):
		if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
			csrf_manager = get_csrf_manager(request)
			token = request.headers.get("X-CSRF-Token") or request.cookies.get("csrf_token")
			if not csrf_manager.verify_token(token):
				raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")
		return await call_next(request)
