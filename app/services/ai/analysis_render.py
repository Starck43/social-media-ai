"""Pure stored-data rendering: missing metrics never masquerade as measured zero."""

from __future__ import annotations

import ipaddress
import math
import re
from typing import Any, Iterable
from urllib.parse import urlsplit

_MEDIA_TYPES = {"photo": "image", "image": "image", "video": "video", "video_file": "video"}
_MEDIA_LABELS = {"image": "Изображение", "video": "Видео", "unknown": "Вложение"}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def metric_number(value: Any) -> float | None:
    """Finite nonnegative counts/scores only; bool is not a measured number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _is_public_host(host: str | None) -> bool:
    if not host:
        return False
    cleaned = host.rstrip(".").lower()
    if not cleaned or cleaned == "api.telegram.org" or cleaned == "localhost":
        return False
    if cleaned.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        address = ipaddress.ip_address(cleaned)
    except ValueError:
        return "." in cleaned
    return bool(address.is_global)


def safe_original_url(value: Any) -> str | None:
    """Only an explicitly saved HTTP(S) original, never a synthesized post URL."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
        or "\\" in value
    ):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
            return None
        parsed.port
        if not _is_public_host(parsed.hostname):
            return None
    except ValueError:
        return None
    return value


def safe_media_url(value: Any) -> str | None:
    """Conservative HTTPS media URL validation without downloading or DNS resolution."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
        or "\\" in value
    ):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname.rstrip(".") if parsed.hostname else None
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 443)
        ):
            return None
        if not _is_public_host(host):
            return None
    except ValueError:
        return None
    return value


def extract_post_evidence(summary_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Project per-post permalink and media URL references with explicit fallback states."""
    data = _as_dict(summary_data)
    stats = _as_dict(data.get("content_statistics"))
    source_meta = _as_dict(data.get("source_metadata"))
    raw_items = _as_list(stats.get("post_evidence")) + _as_list(data.get("post_evidence"))
    out: list[dict[str, Any]] = []
    seen_keys: set[tuple[str | None, str | None]] = set()

    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        permalink = safe_original_url(raw.get("permalink") or raw.get("url"))
        parent_permalink = safe_original_url(raw.get("parent_permalink"))
        raw_ext_id = raw.get("external_id")
        ext_id = (
            str(raw_ext_id).strip()[:128]
            if isinstance(raw_ext_id, (str, int)) and not isinstance(raw_ext_id, bool) and str(raw_ext_id).strip()
            else None
        )
        attachments: list[dict[str, str | None]] = []
        media_urls: list[str] = []
        seen_media_urls: set[str] = set()

        raw_attachments = _as_list(raw.get("attachments"))
        if not raw_attachments and _as_list(raw.get("media_urls")):
            raw_attachments = [{"type": "image", "url": u} for u in _as_list(raw.get("media_urls"))]

        for att in raw_attachments:
            if not isinstance(att, dict):
                attachments.append(
                    {
                        "type": "unknown",
                        "url": None,
                        "status": "unsupported_attachment",
                        "label": _MEDIA_LABELS["unknown"],
                        "fallback_label": "Неподдерживаемое вложение",
                    }
                )
                continue
            raw_type = att.get("type")
            media_type = _MEDIA_TYPES.get(raw_type.lower(), "unknown") if isinstance(raw_type, str) else "unknown"
            url = safe_media_url(att.get("url")) if media_type != "unknown" else None
            if url:
                if url in seen_media_urls:
                    continue
                seen_media_urls.add(url)
                media_urls.append(url)
                attachments.append(
                    {
                        "type": media_type,
                        "url": url,
                        "status": "available",
                        "label": _MEDIA_LABELS[media_type],
                        "fallback_label": None,
                    }
                )
            else:
                status = "unsupported_attachment" if media_type == "unknown" else "missing_media_url"
                fallback = (
                    "Неподдерживаемое вложение"
                    if status == "unsupported_attachment"
                    else ("Изображение без прямой ссылки" if media_type == "image" else "Видео без прямой ссылки")
                )
                attachments.append(
                    {
                        "type": media_type,
                        "url": None,
                        "status": status,
                        "label": _MEDIA_LABELS[media_type],
                        "fallback_label": fallback,
                    }
                )

        if not permalink and not parent_permalink and not attachments and not ext_id:
            continue

        dedup_key = (ext_id, permalink)
        if dedup_key != (None, None) and dedup_key in seen_keys:
            continue
        if dedup_key != (None, None):
            seen_keys.add(dedup_key)

        content_type = "comment" if str(raw.get("content_type") or "").lower() == "comment" else "post"
        published_at = raw.get("published_at") if isinstance(raw.get("published_at"), str) else None
        source_id = (
            raw.get("source_id")
            if type(raw.get("source_id")) is int
            else (source_meta.get("source_id") if type(source_meta.get("source_id")) is int else None)
        )
        tenant_id = (
            raw.get("tenant_id")
            if type(raw.get("tenant_id")) is int
            else (source_meta.get("tenant_id") if type(source_meta.get("tenant_id")) is int else None)
        )
        entry: dict[str, Any] = {
            "external_id": ext_id,
            "content_type": content_type,
            "permalink": permalink,
            "media_urls": media_urls,
            "attachments": attachments,
        }
        if parent_permalink:
            entry["parent_permalink"] = parent_permalink
        if published_at:
            entry["published_at"] = published_at
        if source_id is not None:
            entry["source_id"] = source_id
        if tenant_id is not None:
            entry["tenant_id"] = tenant_id
        if isinstance(raw.get("content_hash"), str) and raw["content_hash"]:
            entry["content_hash"] = raw["content_hash"]
        out.append(entry)
        if len(out) >= 50:
            break

    return out


def merge_parsed_analysis(value: Any) -> dict[str, Any]:
    """Overlay a stored container's parsed dict without mutating either input.

    Parsed fields win even when None/empty; merge only one level, not recursively.
    Malformed containers/parsed fields retain the existing conservative fallback.
    """
    container = _as_dict(value)
    return {**container, **_as_dict(container.get("parsed"))}


def first_analysis_value(containers: Iterable[Any], keys: tuple[str, ...]) -> Any:
    """Read containers in caller order, then field aliases in caller order.

    Ignore only None, empty string and empty list. Zero, False and empty dict
    remain stored values. Do not merge parsed data, coerce values or choose a
    universal precedence: rendering and aggregation deliberately differ.
    """
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            if value is not None and value != "" and value != []:
                return value
    return None


def extract_text_analysis(summary_data: dict[str, Any]) -> dict[str, Any]:
    multi = _as_dict(summary_data.get("multi_llm_analysis"))
    return merge_parsed_analysis(multi.get("text_analysis"))


def extract_content_statistics(summary_data: dict[str, Any]) -> dict[str, Any]:
    stats = _as_dict(summary_data.get("content_statistics"))
    coverage = _as_dict(stats.get("metric_coverage"))
    keys = (
        "total_posts",
        "total_reactions",
        "total_comments",
        "total_views",
        "active_users",
        "messages_count",
        "engagement_rate",
    )
    out = {}
    for key in keys:
        value = metric_number(stats.get(key))
        info = _as_dict(coverage.get(key))
        if info:
            if (
                type(info.get("known")) is not int
                or type(info.get("total")) is not int
                or info["total"] <= 0
                or info["known"] != info["total"]
            ):
                value = None
        elif value == 0:
            value = None
        out[key] = value
    for name, total_key in (
        ("avg_reactions_per_post", "total_reactions"),
        ("avg_comments_per_post", "total_comments"),
        ("avg_views_per_post", "total_views"),
    ):
        posts, total = out["total_posts"], out[total_key]
        out[name] = total / posts if posts is not None and posts > 0 and total is not None else None
    if _as_dict(summary_data.get("period_rollup")):
        out["active_users"] = None  # Summed daily authors are not unique period authors.
    return out


def _label_for_score(score: float) -> str:
    return "Позитивный" if score > 0.6 else "Негативный" if score < 0.4 else "Нейтральный"


def sentiment_summary(summary_data: dict[str, Any]) -> dict[str, Any]:
    text = extract_text_analysis(summary_data)
    unified = merge_parsed_analysis(summary_data.get("unified_summary"))
    score = None
    for container in (text, unified, summary_data):
        candidate = metric_number(container.get("sentiment_score"))
        if candidate is not None and candidate <= 1:
            score = candidate
            break
    return {"score": score, "label": _label_for_score(score) if score is not None else "—"}


def summary_display_heading(summary: Any, limit: int = 120) -> str | None:
    """A short stored-summary excerpt, not an invented title or execution status."""
    if not isinstance(summary, str):
        return None
    value = " ".join(summary.split())
    if not value:
        return None
    # Conservative sentence boundaries: keep decimals and common abbreviations
    # (e.g. ул. Ленина, г. Киров, т. д.) intact rather than cutting at every dot.
    abbreviations = {
        "г",
        "ул",
        "д",
        "им",
        "др",
        "т",
        "п",
        "рис",
        "стр",
        "см",
        "руб",
        "млн",
        "млрд",
        "mr",
        "mrs",
        "ms",
        "dr",
        "vs",
        "т.д",
        "т.п",
        "т.е",
        "e.g",
        "i.e",
    }
    for match in re.finditer(r"[.!?…]+(?=\s|$)", value):
        prefix = value[: match.start()]
        token = re.search(r"([^\s]+)$", prefix)
        word = token.group(1) if token else ""
        if match.group() == "." and (
            word.casefold().rstrip(".") in abbreviations or (len(word) == 1 and word.isupper())
        ):
            continue
        value = value[: match.end()]
        break
    if len(value) <= limit:
        return value
    shortened = value[: limit - 1].rstrip()
    boundary = shortened.rfind(" ")
    if boundary >= limit // 2:
        shortened = shortened[:boundary]
    return shortened.rstrip(" .,;:!?…") + "…"


# Codes written by the analyzer next to `analysis_complete = False`.
COVERAGE_LIMIT_LABELS = {
    "text_sampled": "часть текстов не вошла в выборку для анализа",
    "media_uncovered": "часть вложений (изображения, видео, файлы) не проанализирована",
    "stage_failed": "один из этапов анализа завершился ошибкой",
}


def analysis_coverage(summary_data: Any) -> dict[str, Any]:
    """Explicit incomplete-coverage marker for display.

    Only a stored `analysis_complete is False` counts as incomplete. A missing
    marker is unknown coverage, never proof of a complete analysis, so callers
    must not render it as "complete". Unknown reason codes are ignored.
    """
    meta = _as_dict(_as_dict(summary_data).get("analysis_metadata"))
    incomplete = meta.get("analysis_complete") is False
    reasons: list[str] = []
    if incomplete:
        for code in _as_list(meta.get("coverage_limits")):
            label = COVERAGE_LIMIT_LABELS.get(code) if isinstance(code, str) else None
            if label and label not in reasons:
                reasons.append(label)
    return {"incomplete": incomplete, "reasons": reasons}


def render_analysis(summary_data: dict[str, Any], *, source_name: str | None = None) -> dict[str, Any]:
    data = _as_dict(summary_data)
    text = extract_text_analysis(data)
    unified = merge_parsed_analysis(data.get("unified_summary"))
    legacy = merge_parsed_analysis(data.get("ai_analysis"))
    containers = (data, text, unified, legacy)

    def first(*keys):
        return first_analysis_value(containers, keys)

    raw_topics = _as_list(first("main_topics", "topics", "key_topics"))
    topics = []
    for topic in raw_topics:
        value = (
            topic
            if isinstance(topic, str)
            else (_as_dict(topic).get("topic") or _as_dict(topic).get("name") or _as_dict(topic).get("key"))
        )
        if isinstance(value, str) and value.strip() and value.strip() not in topics:
            topics.append(value.strip())
    title = next(
        (
            c["analysis_title"].strip()
            for c in containers
            if isinstance(c.get("analysis_title"), str) and c["analysis_title"].strip()
        ),
        None,
    )
    summary = next(
        (
            c[key].strip()
            for c in containers
            for key in ("analysis_summary", "summary")
            if isinstance(c.get(key), str) and c[key].strip()
        ),
        None,
    )
    # One stored-data cascade shared by every web analysis entry point.
    display_title = title or summary_display_heading(summary) or (topics[0] if topics else None)
    title = title or (topics[0] if topics else None)
    stats = _as_dict(data.get("content_statistics"))
    source_meta = _as_dict(data.get("source_metadata"))
    name = source_name or source_meta.get("source_name")
    display_title = display_title or (
        f"Материалы источника «{name.strip()}»" if isinstance(name, str) and name.strip() else "Материалы источника"
    )
    post_evidence = extract_post_evidence(data)
    originals = []
    candidates = _as_list(stats.get("original_links")) + _as_list(data.get("original_links"))
    for key in ("original_url", "post_url", "permalink"):
        candidates += [data.get(key), source_meta.get(key)]
    for item in post_evidence:
        if item.get("permalink"):
            candidates.append(item["permalink"])
    for candidate in candidates:
        url = safe_original_url(candidate.get("url") if isinstance(candidate, dict) else candidate)
        if url and url not in originals:
            originals.append(url)
    window = _as_dict(stats.get("content_date_range"))
    date_range = _as_dict(stats.get("date_range"))
    rollup = _as_dict(data.get("period_rollup"))
    return {
        "analysis_title": title,
        "display_title": display_title,
        "analysis_summary": summary,
        "main_topics": topics,
        "overall_mood": first("overall_mood"),
        "highlights": _as_list(first("highlights")),
        "highlights_available": any(isinstance(c.get("highlights"), list) for c in containers),
        "sentiment": sentiment_summary(data),
        "toxicity_score": first("toxicity_score"),
        "toxicity_category": first("toxicity_category"),
        "content_statistics": extract_content_statistics(data),
        "metric_coverage": _as_dict(stats.get("metric_coverage")),
        "metrics_legacy": not bool(stats.get("metric_coverage")),
        "source_metadata": source_meta,
        "scenario_metadata": _as_dict(data.get("scenario_metadata")),
        "analysis_metadata": _as_dict(data.get("analysis_metadata")),
        "coverage": analysis_coverage(data),
        "unified_summary": unified,
        "original_links": originals[:50],
        "post_evidence": post_evidence,
        "content_window_start": window.get("earliest") or rollup.get("start") or date_range.get("first"),
        "content_window_end": window.get("latest") or rollup.get("end") or date_range.get("last"),
    }
