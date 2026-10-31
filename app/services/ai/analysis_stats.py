"""Content statistics and JSON projection, separated from provider orchestration."""

from datetime import date, datetime
from typing import Any, Optional

from app.utils.date_parsing import universal_date_parser


class AnalysisStatsMixin:
    """Pure input/result projection retained on AIAnalyzer for compatibility."""

    def _calculate_content_stats(self, content: list[dict], analysis_date: Optional[date] = None) -> dict[str, Any]:
        """
        Calculate content statistics including actual post date range.

        Args:
                content: List of content items
                analysis_date: Optional analysis date for event-based mode
        """
        if not content:
            return {}

        # Extract basic data
        texts = [item.get("text", "") for item in content]

        from app.services.ai.analysis_render import extract_post_evidence, metric_number, safe_original_url

        coverage = {}
        values = {}
        for field, key in (("reactions", "total_reactions"), ("comments", "total_comments"), ("views", "total_views")):
            observed = []
            known = 0
            for item in content:
                metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
                flags = item.get("metric_availability", metrics.get("metric_availability"))
                value = metric_number(item.get(field, metrics.get(field)))
                if value is not None:
                    observed.append(value)  # Preserve the legacy saved numeric sum.
                    if not isinstance(flags, dict) or flags.get(field) is True:
                        known += 1
            values[field] = sum(observed)
            coverage[key] = {"known": known, "total": len(content)}

        # Distinct authors: VK items carry from_id/owner_id; Telegram comments a
        # from_id. Any of these is enough to count an active user.
        authors: set[str] = set()
        for item in content:
            author = item.get("author") if isinstance(item.get("author"), dict) else {}
            author_id = next(
                (item[key] for key in ("from_id", "owner_id", "author_id", "user_id") if item.get(key) is not None),
                author.get("id"),
            )
            if author_id is not None:
                authors.add(str(author_id))

        total_posts = len(content)
        total_reactions = values["reactions"]
        total_comments = values["comments"]
        total_views = values["views"]

        known_authors = sum(
            any(item.get(key) is not None for key in ("from_id", "owner_id", "author_id", "user_id"))
            or (isinstance(item.get("author"), dict) and item["author"].get("id") is not None)
            for item in content
        )
        coverage["active_users"] = {"known": known_authors, "total": total_posts}
        for key in ("total_posts", "messages_count"):
            coverage[key] = {"known": total_posts, "total": total_posts}
        coverage["engagement_rate"] = {
            "known": min(coverage[key]["known"] for key in ("total_reactions", "total_comments", "total_views")),
            "total": total_posts,
        }

        originals = []
        raw_evidence = []
        for item in content:
            url = safe_original_url(item.get("permalink") or item.get("url"))
            if url and url not in originals:
                originals.append(url)
            parent = item.get("parent_context") if isinstance(item.get("parent_context"), dict) else {}
            parent_url = safe_original_url(parent.get("permalink") or parent.get("url"))
            ext_id = item.get("external_id") or item.get("id")
            attachments = item.get("attachments")
            if url or parent_url or isinstance(attachments, list):
                entry: dict[str, Any] = {
                    "external_id": str(ext_id) if ext_id is not None else None,
                    "content_type": str(item.get("content_type") or "post"),
                    "permalink": url,
                    "attachments": attachments if isinstance(attachments, list) else [],
                }
                if parent_url:
                    entry["parent_permalink"] = parent_url
                if isinstance(item.get("content_hash"), str) and item["content_hash"]:
                    entry["content_hash"] = item["content_hash"]
                if type(item.get("source_id")) is int:
                    entry["source_id"] = item["source_id"]
                if type(item.get("tenant_id")) is int:
                    entry["tenant_id"] = item["tenant_id"]
                raw_evidence.append(entry)
        post_evidence = extract_post_evidence({"post_evidence": raw_evidence})

        # Extract and parse post dates using existing utility
        post_dates = []
        for item in content:
            pub_date = item.get("published_at") or item.get("date") or item.get("created_at")
            if parsed_date := universal_date_parser(pub_date):
                if isinstance(parsed_date, (datetime, date)):
                    post_dates.append(parsed_date)

        # Calculate content date range
        content_date_range = {}
        if post_dates:
            min_date, max_date = min(post_dates), max(post_dates)
            content_date_range = {
                "earliest": min_date.isoformat(),
                "latest": max_date.isoformat(),
                "span_days": (max_date - min_date).days,
            }

        # Determine date range for context
        if analysis_date and post_dates:
            date_range_dict = {"first": analysis_date.isoformat(), "last": analysis_date.isoformat()}
        else:
            # Use actual dates from content (fallback to None if no dates)
            dates = [item.get("date") for item in content if item.get("date")]
            date_range_dict = {"first": min(dates) if dates else None, "last": max(dates) if dates else None}

        # Build user activity summary from available data
        user_activity_summary = ""
        if authors:
            user_activity_summary = (
                f"Активных пользователей: {len(authors)}. "
                f"Всего постов: {total_posts}, реакций: {total_reactions}, "
                f"комментариев: {total_comments}"
            )

        # Calculate all statistics
        stats_result = {
            "total_posts": total_posts,
            "metric_coverage": coverage,
            "original_links": originals[:50],
            "messages_count": total_posts,
            "active_users": len(authors),
            "avg_text_length": sum(len(t) for t in texts) / total_posts if texts else 0,
            "total_reactions": total_reactions,
            "total_comments": total_comments,
            "total_views": total_views,
            "avg_reactions_per_post": total_reactions / total_posts if total_posts else 0,
            "avg_comments_per_post": total_comments / total_posts if total_posts else 0,
            "engagement_rate": (total_reactions + total_comments + total_views) / total_posts if total_posts else 0,
            "date_range": date_range_dict,  # Context-aware date range
            "content_date_range": content_date_range,  # Actual post dates
            "user_activity_summary": user_activity_summary,
            "liked_posts_count": 0,  # Данные о лайках пользователя недоступны
            "comments_to_others_count": 0,  # Данные о комментариях к чужим постам недоступны
        }
        if post_evidence:
            stats_result["post_evidence"] = post_evidence
        return stats_result

    def _make_json_serializable(self, obj):
        """Recursively convert non-JSON serializable objects to strings/primitives."""
        from datetime import date, datetime
        from enum import Enum

        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, Enum):
            # return the database value or name; str(obj) also works if consistent
            return getattr(obj, "value", obj.name)
        if isinstance(obj, dict):
            return {k: self._make_json_serializable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [self._make_json_serializable(v) for v in obj]
        return obj
