import logging
from datetime import datetime, date, time, timezone
from typing import Optional, Any

logger = logging.getLogger(__name__)


def universal_date_parser(date_input: Any, target_timezone: str = 'UTC+3') -> Optional[datetime]:
	"""
	Universal date parser that handles multiple input formats.

	Supported formats:
	- DD-MM-YYYY string (from CLI/forms)
	- ISO string (from APIs/database)
	- datetime objects
	- date objects
	- Unix timestamps (int/float)

	Args:
		date_input: Date in any supported format
		target_timezone: Target timezone ('UTC+3', 'UTC')

	Returns:
		datetime object with proper timezone or None if parsing fails
	"""
	if not date_input:
		return None

	try:
		# Handle None/empty
		if date_input is None:
			return None

		# Already a datetime object
		if isinstance(date_input, datetime):
			result = date_input
			# Ensure timezone
			if result.tzinfo is None:
				result = result.replace(tzinfo=timezone.utc)
			return result

		# Date object (no time)
		elif isinstance(date_input, date):
			result = datetime.combine(date_input, time(0, 0, 0))
			return result.replace(tzinfo=timezone.utc)

		# Unix timestamp
		elif isinstance(date_input, (int, float)):
			return datetime.fromtimestamp(date_input, tz=timezone.utc)

		# String input
		elif isinstance(date_input, str):
			# Try DD-MM-YYYY format first (from CLI/forms)
			try:
				date_obj = datetime.strptime(date_input, '%d-%m-%Y')
				# Apply timezone adjustment for Russia
				if target_timezone == 'UTC+3':
					# Set to beginning of day in UTC+3 = 21:00 previous day UTC
					result = datetime.combine(date_obj, time(0, 0, 0))
					result = result.replace(tzinfo=timezone.utc)  # This is actually 21:00 UTC for 00:00 MSK
					return result
				else:
					return datetime.combine(date_obj, time(0, 0, 0)).replace(tzinfo=timezone.utc)
			except ValueError:
				pass

			# Try ISO format (from APIs/database)
			try:
				# Handle both 'Z' and timezone formats
				iso_str = date_input.replace('Z', '+00:00')
				result = datetime.fromisoformat(iso_str)
				# Ensure timezone
				if result.tzinfo is None:
					result = result.replace(tzinfo=timezone.utc)
				return result
			except ValueError:
				pass

			logger.warning(f"Unsupported date string format: {date_input}")
			return None

		else:
			logger.warning(f"Unsupported date input type: {type(date_input)}")
			return None

	except Exception as e:
		logger.warning(f"Date parsing failed for {date_input}: {e}")
		return None


def to_unix_timestamp(date_input: Any, target_timezone: str = 'UTC+3') -> Optional[int]:
	"""
	Convert any date input to Unix timestamp.

	Args:
		date_input: Date in any supported format
		target_timezone: Target timezone for string parsing

	Returns:
		Unix timestamp or None if conversion fails
	"""
	date_obj = universal_date_parser(date_input, target_timezone)
	if not date_obj:
		return None

	try:
		return int(date_obj.timestamp())
	except Exception as e:
		logger.warning(f"Timestamp conversion failed: {e}")
		return None

