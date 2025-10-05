from passlib.context import CryptContext

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
	"""Verify a password against a hash."""

	# guard if hashed_password is None
	if not hashed_password:
		return False

	try:
		return pwd_context.verify(plain_password, hashed_password)
	except Exception:
		return False


def get_password_hash(password: str) -> str:
	"""Generate a password hash."""
	return pwd_context.hash(password)


def generate_temporary_password() -> str:
	"""Generate a temporary password."""
	import secrets
	import string
	return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(6))
