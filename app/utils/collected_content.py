"""Canonical raw staging rows shared by collection and push admission."""
from __future__ import annotations
from typing import Any

def _build_permalink(source: Any, external_id: str | int | None) -> str | None:
	"""A public link to one item, or None when the platform has no known shape.

	Kept deliberately dumb: a wrong link is worse than no link, so anything not
	recognised returns None and the UI simply shows the text without a link.
	"""
	if external_id is None:
		return None
	eid = str(external_id).strip()
	if not eid:
		return None
	params = source.params or {}
	try:
		if "vk" in (params.get("platform") or getattr(source.platform, "code", "") or "").lower():
			# VK post ids arrive as "{owner_id}_{post_id}".
			owner = params.get("owner_id") or params.get("group_id") or ""
			return f"https://vk.com/wall{owner}_{eid}" if owner else f"https://vk.com/wall{eid}"
		if params.get("mode") == "push" or "telegram" in (params.get("platform") or "").lower():
			return f"https://t.me/{eid}" if eid.lstrip("-").isdigit() else None
	except Exception:  # noqa: BLE001 — a link is a nicety, not a reason to fail
		return None
	return None


def build_staged_rows(content: list[dict], source: Any, run_id: int | None) -> list[dict]:
	from app.services.ai.dedup import item_hash
	from app.utils.date_parsing import universal_date_parser
	from app.utils.content_attachments import normalize_attachments
	hashes = [item_hash(item) for item in content]
	rows: list[dict] = []
	for item, h in zip(content, hashes):
		# Platforms hand the publication date over in several shapes (datetime,
		# unix seconds, "2026-10-02T10:00:00Z"); normalise once here so the
		# column, the ordering and the UI all agree on what a date is.
		raw_published = item.get("published_at") or item.get("date") or item.get("created_at")
		published = universal_date_parser(raw_published) if raw_published is not None else None
		external_id = item.get("external_id") or item.get("id")
		permalink = item.get("permalink") or item.get("url")
		if not permalink and external_id:
			permalink = _build_permalink(source, external_id)
		if permalink:
			item.setdefault("permalink", permalink)  # Same saved URL reaches immediate analysis.
		rows.append(
			{
				"run_id": run_id,
				"source_id": source.id,
				"external_id": str(external_id) if external_id is not None else None,
				"content_hash": h,
				"platform": item.get("platform"),
				"published_at": published,
				"media_type": item.get("media_type") or item.get("type"),
				"attachments": normalize_attachments(item.get("attachments")),
				"text": item.get("text"),
				"metrics": {
					**(item.get("metrics") if isinstance(item.get("metrics"), dict) else {}),
					**{
						key: item[key]
						for key in ("reactions", "comments", "views", "metric_availability")
						if key in item
					},
				},
				"author": (
					item.get("author")
					if isinstance(item.get("author"), dict)
					else (
						{
							"id": next(
								item[k]
								for k in ("from_id", "owner_id", "author_id", "user_id")
								if item.get(k) is not None
							)
						}
						if any(item.get(k) is not None for k in ("from_id", "owner_id", "author_id", "user_id"))
						else None
					)
				),
				"permalink": permalink,
			}
		)
	return rows
