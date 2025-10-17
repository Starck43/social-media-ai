import re
import json


def decode_unicode_json(json_str: str) -> str:
	return re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), json_str)


def format_json_with_unicode_decode(data, indent=2, ensure_ascii=False):
	json_str = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
	return decode_unicode_json(json_str)
