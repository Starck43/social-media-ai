"""
Aggregation and reporting service for AI analytics.

Provides aggregated metrics for dashboard and admin panels:
- Sentiment trends over time
- Top topics and categories
- LLM provider efficiency and costs
- Content mix (text/image/video)
- Engagement metrics
"""

import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional

from app.models import AIAnalytics
from app.services.ai.chain_resolver import human_chain_label
from app.types import MediaType, PeriodType
from app.utils.enum_helpers import get_enum_value

logger = logging.getLogger(__name__)


class ReportAggregator:
    """
    Service for aggregating AI analytics data into actionable reports.

    Supports:
    - Sentiment trends over time
    - Topic discovery and tracking
    - LLM cost and efficiency analysis
    - Content type distribution
    - Engagement metrics

    Every read goes through `AIAnalytics.objects`, so the tenant guard in
    `BaseManager` applies: inside `tenant_scope(...)` a report covers exactly
    one workspace, and only an explicit operator bypass is global. These
    aggregations used to build `select()` statements on a session of their
    own, which is why they needed a hand-written tenant clause to stay safe.
    """

    # Every specialized aggregation is keyed by the field names the current
    # `JSONSchemaBuilder` contract asks for. Rows analysed before that contract
    # landed carry the previous names instead, so each logical field maps to the
    # ordered list of keys to try, newest first.
    #
    # This is the single place a field rename has to be recorded: without it a
    # rename would silently empty the matching aggregation, since the LLM would
    # start writing the new name and the reader would only look for the old one.
    FIELD_ALIASES: dict[str, tuple[str, ...]] = {
        "toxicity_level": ("toxicity_level", "toxicity_category"),
        "brand": ("brand", "brand_name"),
        "sentiment": ("sentiment", "mention_sentiment"),
        "viral_score": ("viral_score", "viral_potential"),
        "trend_name": ("trend_name", "trending_topics"),
        "engagement_rate": ("engagement_rate", "engagement_score"),
        "intent_type": ("intent_type", "primary_intent"),
        "influencer_name": ("influencer_name", "name"),
        "impact_score": ("impact_score", "impact"),
        "competitor": ("competitor", "name"),
    }

    # Bands that mean "not toxic". `toxicity_level` is a severity, so "low" is
    # clean — the opposite of the older `toxicity_category`, where anything that
    # was not literally "чистый" meant the row had flagged something.
    CLEAN_LEVELS = frozenset({"low", "чистый", "clean", "0", "нет", "ноль", "none"})

    @staticmethod
    async def _analytics_query(
        days: Optional[int],
        source_id: Optional[int] = None,
        tenant_id: Optional[int] = None,
        source_ids: Optional[list[int]] = None,
        scenario_id: Optional[int] = None,
    ):
        """Analytics rows for the report, scoped to the ambient tenant.

        `days` is a look-back window; `None` means no date cutoff ("всё").

        `tenant_id` narrows further (a superuser previewing another workspace);
        it never widens what the ambient scope already allows. `source_ids`
        filters to a task's linked sources; the whole workspace when None.

        `scenario_id` filters through the JSON document in Python: `ai_analytics`
        has no scenario column, the id is recorded in
        `summary_data.scenario_metadata` (see the analyzer), and the column is
        `postgresql.JSON` rather than JSONB, so it cannot be filtered with a
        jsonb operator. Rows written before that metadata existed match no
        scenario, which is correct — they were not produced by one.
        """
        qs = AIAnalytics.objects.all()
        if days is not None:
            qs = qs.filter(analysis_date__gte=date.today() - timedelta(days=days))

        if source_id:
            qs = qs.filter(source_id=source_id)
        elif source_ids:
            qs = qs.filter(source_id__in=source_ids)

        if scenario_id is not None:
            # Filtered in Python, not in SQL. `summary_data` is declared as
            # `postgresql.JSON`, so the document is stored as text and the jsonb
            # operators (`->>`, containment) do not apply to it — see migration
            # 0009. Upcasting the column just to reach one nested id would cost
            # more than it saves, and the rows are already narrowed to the period
            # and to the tenant by the time this runs.
            wanted = str(scenario_id)
            rows = [
                row
                for row in await qs
                if str(((row.summary_data or {}).get("scenario_metadata") or {}).get("scenario_id", "")) == wanted
            ]
            return rows

        if tenant_id is not None:
            qs = qs.filter(tenant_id=tenant_id)

        # Awaited here rather than by the caller: this is an `async def`, so a
        # returned QuerySet would reach them unresolved and iterating it would
        # raise. Returns a list in both branches so the callers iterate a list
        # either way.
        return list(await qs.order_by(AIAnalytics.analysis_date.asc()))

    async def get_sentiment_trends(
        self, source_id: Optional[int] = None, days: int = 7, group_by: str = "day"
    ) -> list[dict[str, Any]]:
        """
        Get sentiment trends over time period.

        Args:
                source_id: Filter by specific source
                days: Number of days to look back
                group_by: Grouping interval ('day', 'week')

        Returns:
                List of trend points with date, avg sentiment, counts
        """
        try:
            analytics = await self._analytics_query(days, source_id)

            # Aggregate by date
            trends = []
            by_date = defaultdict(list)

            for a in analytics:
                # Extract sentiment from summary_data
                sentiment_data = self._extract_sentiment(a.summary_data)
                if sentiment_data:
                    by_date[a.analysis_date].append(sentiment_data)

            # Calculate averages per date
            for analysis_date, sentiments in sorted(by_date.items()):
                avg_score = sum(s["score"] for s in sentiments) / len(sentiments) if sentiments else 0

                # Count sentiment distribution
                positive = sum(1 for s in sentiments if s["label"] == "positive")
                neutral = sum(1 for s in sentiments if s["label"] == "neutral")
                negative = sum(1 for s in sentiments if s["label"] == "negative")

                trends.append(
                    {
                        "date": analysis_date.isoformat(),
                        "avg_sentiment_score": round(avg_score, 2),
                        "total_analyses": len(sentiments),
                        "distribution": {
                            "positive": positive,
                            "neutral": neutral,
                            "negative": negative,
                        },
                    }
                )

            return trends

        except Exception as e:
            logger.error(f"Error getting sentiment trends: {e}", exc_info=True)
            return []

    async def get_top_topics(
        self,
        source_id: Optional[int] = None,
        days: int = 7,
        limit: int = 10,
        tenant_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """
        Get top topics/keywords from analyses.

        Args:
                source_id: Filter by specific source
                days: Amount days to look back
                limit: Max amount topics to return
                tenant_id: Scope to a tenant (via AIAnalytics.tenant_id)

        Returns:
                List of topics with counts, sentiment, example posts
        """
        try:
            analytics = await self._analytics_query(days, source_id, tenant_id)

            # Extract and count topics
            topic_counter = Counter()
            topic_sentiments = defaultdict(list)
            topic_examples = defaultdict(list)

            for a in analytics:
                topics = self._extract_topics(a.summary_data)
                sentiment = self._extract_sentiment(a.summary_data)

                for topic in topics:
                    topic_counter[topic] += 1

                    if sentiment:
                        topic_sentiments[topic].append(sentiment["score"])

                    # Store example (limit to 2 per topic)
                    if len(topic_examples[topic]) < 2:
                        example = self._extract_example_text(a.summary_data)
                        if example:
                            topic_examples[topic].append(example)

            # Build result
            top_topics = []
            for topic, count in topic_counter.most_common(limit):
                avg_sentiment = (
                    sum(topic_sentiments[topic]) / len(topic_sentiments[topic]) if topic_sentiments[topic] else 0
                )

                top_topics.append(
                    {
                        "topic": topic,
                        "count": count,
                        "avg_sentiment": round(avg_sentiment, 2),
                        "examples": topic_examples[topic][:2],
                    }
                )

            return top_topics

        except Exception as e:
            logger.error(f"Error getting top topics: {e}", exc_info=True)
            return []

    async def get_llm_provider_stats(self, source_id: Optional[int] = None, days: int = 30) -> dict[str, Any]:
        """
        Get LLM provider usage statistics and costs.

        Args:
                source_id: Filter by specific source
                days: Number of days to look back

        Returns:
                Dict with provider stats, costs, token usage
        """
        try:
            analytics = await self._analytics_query(days, source_id)

            # Aggregate by provider
            provider_stats = defaultdict(
                lambda: {
                    "requests": 0,
                    "total_tokens": 0,
                    "request_tokens": 0,
                    "response_tokens": 0,
                    "estimated_cost": 0.0,
                    "models": Counter(),
                }
            )

            for a in analytics:
                provider = a.provider_type or "unknown"
                stats = provider_stats[provider]

                stats["requests"] += 1
                stats["request_tokens"] += a.request_tokens or 0
                stats["response_tokens"] += a.response_tokens or 0
                stats["total_tokens"] += (a.request_tokens or 0) + (a.response_tokens or 0)
                # estimated_cost is NUMERIC(14,6) cents → keep the aggregates plain floats
                stats["estimated_cost"] += float(a.estimated_cost or 0)

                if a.llm_model:
                    stats["models"][a.llm_model] += 1

            # Convert to serializable format
            result_stats = {}
            total_cost = 0
            total_requests = 0

            for provider, stats in provider_stats.items():
                total_cost += stats["estimated_cost"]
                total_requests += stats["requests"]

                result_stats[provider] = {
                    "requests": stats["requests"],
                    "total_tokens": stats["total_tokens"],
                    "request_tokens": stats["request_tokens"],
                    "response_tokens": stats["response_tokens"],
                    "estimated_cost_usd": round(stats["estimated_cost"] / 100, 4),  # cents to USD
                    "avg_tokens_per_request": (
                        round(stats["total_tokens"] / stats["requests"], 1) if stats["requests"] > 0 else 0
                    ),
                    "models": dict(stats["models"].most_common(5)),
                }

            return {
                "providers": result_stats,
                "summary": {
                    "total_requests": total_requests,
                    "total_cost_usd": round(total_cost / 100, 2),
                    "period_days": days,
                },
            }

        except Exception as e:
            logger.error(f"Error getting LLM provider stats: {e}", exc_info=True)
            return {"providers": {}, "summary": {}}

    async def get_content_mix(
        self,
        source_id: Optional[int] = None,
        days: int = 7,
        tenant_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Get content type distribution (text/image/video).

        Args:
                source_id: Filter by specific source
                days: Number of days to look back
                tenant_id: Scope to a tenant (via AIAnalytics.tenant_id)

        Returns:
                Dict with counts and percentages per media type
        """
        try:
            analytics = await self._analytics_query(days, source_id, tenant_id)

            # Count media types
            media_counts = Counter()
            total = 0

            for a in analytics:
                if a.media_types:
                    for media_type in a.media_types:
                        if isinstance(media_type, dict):
                            media_type = media_type.get("type") or media_type.get("name") or media_type.get("media_type")
                        if isinstance(media_type, str) and media_type.strip():
                            media_counts[media_type.strip()] += 1
                            total += 1

            # Calculate percentages
            media_mix = {}
            for media_type, count in media_counts.items():
                percentage = round((count / total * 100), 1) if total > 0 else 0
                media_mix[media_type] = {"count": count, "percentage": percentage}

            return {"media_types": media_mix, "total_analyses": len(analytics), "total_media_items": total}

        except Exception as e:
            logger.error(f"Error getting content mix: {e}", exc_info=True)
            return {"media_types": {}, "total_analyses": 0, "total_media_items": 0}

    async def get_engagement_metrics(self, source_id: Optional[int] = None, days: int = 7) -> dict[str, Any]:
        """
        Get engagement metrics (reactions, comments).

        Args:
                source_id: Filter by specific source
                days: Number of days to look back

        Returns:
                Dict with average engagement rates per post
        """
        try:
            analytics = await self._analytics_query(days, source_id)

            # Extract engagement data
            total_reactions = 0
            total_comments = 0
            total_posts = 0

            for a in analytics:
                engagement = self._extract_engagement(a.summary_data)
                if engagement:
                    total_reactions += engagement.get("reactions", 0)
                    total_comments += engagement.get("comments", 0)
                    total_posts += engagement.get("posts", 1)

            avg_reactions = round(total_reactions / total_posts, 1) if total_posts > 0 else 0
            avg_comments = round(total_comments / total_posts, 1) if total_posts > 0 else 0

            return {
                "avg_reactions_per_post": avg_reactions,
                "avg_comments_per_post": avg_comments,
                "total_reactions": total_reactions,
                "total_comments": total_comments,
                "total_posts_analyzed": total_posts,
            }

        except Exception as e:
            logger.error(f"Error getting engagement metrics: {e}", exc_info=True)
            return {}

    async def get_activity_trend(
        self,
        source_id: Optional[int] = None,
        days: int = 7,
        tenant_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Per-day user activity (messages, active users, engagement) for the report.

        Reads the real per-day stats the analyzer writes under
        `content_statistics` (`active_users`, `messages_count`,
        `total_posts`, engagement), so the trend reflects actual user activity
        instead of being empty.
        """
        analytics = await self._analytics_query(days, source_id, tenant_id)

        by_date: dict[date, list] = defaultdict(list)
        for a in analytics:
            stats = (a.summary_data or {}).get("content_statistics") or {}
            if stats:
                by_date[a.analysis_date].append(stats)

        trend: list[dict[str, Any]] = []
        for d, items in sorted(by_date.items()):
            posts = sum((i.get("total_posts") or 0) for i in items)
            messages = sum((i.get("messages_count") or 0) for i in items)
            users = sum((i.get("active_users") or 0) for i in items)
            reactions = sum((i.get("total_reactions") or 0) for i in items)
            comments = sum((i.get("total_comments") or 0) for i in items)
            views = sum((i.get("total_views") or 0) for i in items)
            rate = (reactions + comments + views) / posts if posts else 0
            trend.append(
                {
                    "date": d.isoformat(),
                    "total_posts": posts,
                    "messages_count": messages,
                    "active_users": users,
                    "engagement_rate": round(rate, 2),
                }
            )
        return trend

    async def get_sentiment_by_user(
        self,
        source_id: Optional[int] = None,
        days: int = 7,
        limit: int = 20,
        tenant_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Per-user sentiment over the period.

        Only the `monitored_users` mode builds per-person chains
        (`topic_chain_id = src_{source}_user_{author}`); rows from other modes
        have no author and are skipped. Each user's analyses are averaged into
        one sentiment score, sorted by volume.
        """
        try:
            analytics = await self._analytics_query(days, source_id, tenant_id)

            by_user: dict[str, list[float]] = defaultdict(list)
            counts: Counter = Counter()
            for a in analytics:
                author = self._chain_author(a.topic_chain_id)
                if author is None:
                    continue
                sentiment = self._extract_sentiment(a.summary_data)
                if sentiment and sentiment.get("score") is not None:
                    by_user[author].append(sentiment["score"])
                counts[author] += 1

            result = []
            for author, scores in by_user.items():
                result.append(
                    {
                        "user": author,
                        "count": counts[author],
                        "avg_sentiment": round(sum(scores) / len(scores), 2) if scores else None,
                    }
                )
            result.sort(key=lambda x: x["count"], reverse=True)
            return result[:limit]
        except Exception as e:
            logger.error(f"Error getting sentiment by user: {e}", exc_info=True)
            return []

    async def get_activity_by_user(
        self,
        source_id: Optional[int] = None,
        days: int = 7,
        limit: int = 20,
        tenant_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Per-user activity over the period.

        Only the `monitored_users` mode builds per-person chains; rows from other
        modes are skipped. Each user's `content_statistics` are summed into one
        row, sorted by message volume.
        """
        try:
            analytics = await self._analytics_query(days, source_id, tenant_id)

            by_user: dict[str, dict[str, Any]] = defaultdict(
                lambda: {"messages": 0, "posts": 0, "reactions": 0, "comments": 0, "views": 0, "days": 0}
            )
            for a in analytics:
                author = self._chain_author(a.topic_chain_id)
                if author is None:
                    continue
                stats = (a.summary_data or {}).get("content_statistics") or {}
                entry = by_user[author]
                entry["messages"] += int(stats.get("messages_count") or 0)
                entry["posts"] += int(stats.get("total_posts") or 0)
                entry["reactions"] += int(stats.get("total_reactions") or 0)
                entry["comments"] += int(stats.get("total_comments") or 0)
                entry["views"] += int(stats.get("total_views") or 0)
                entry["days"] += 1

            result = []
            for author, entry in by_user.items():
                posts = entry["posts"]
                rate = (entry["reactions"] + entry["comments"] + entry["views"]) / posts if posts else 0
                result.append(
                    {
                        "user": author,
                        "days": entry["days"],
                        "messages": entry["messages"],
                        "posts": posts,
                        "engagement_rate": round(rate, 2),
                    }
                )
            result.sort(key=lambda x: x["messages"], reverse=True)
            return result[:limit]
        except Exception as e:
            logger.error(f"Error getting activity by user: {e}", exc_info=True)
            return []

    # ── specialized aggregations (one per new analysis_type) ────────────────
    # Each reads the period's `summary_data` JSONB and returns plain dicts /
    # lists, so the digest brief can render them without an LLM call. A row that
    # carries none of the keys for a type is simply skipped, which keeps the
    # methods safe on data written by any pipeline version.

    # The old `toxicity_category` values were words like "Чистый"/"Слегка
    # токсичный"; the new contract's `toxicity_level` is one of low/medium/high.
    # Map the new levels to scores so one threshold drives the toxic flag.
    TOXICITY_LEVEL_SCORES = {"low": 0.2, "medium": 0.5, "high": 0.9}

    async def get_toxicity_summary(
        self,
        source_id: Optional[int] = None,
        days: int = 7,
        tenant_id: Optional[int] = None,
        source_ids: Optional[list[int]] = None,
        threshold: float = 0.7,
        scenario_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Toxicity stats over the period: share, average score, categories.

        Reads both the score and the level/category: a row with either counts. The
        new contract's `toxicity_level` (low/medium/high) takes precedence and is
        mapped to a score, because a row can carry a stale `toxicity_score` next
        to a freshly-read level and the score alone would misreport it. Legacy
        `toxicity_category` words fall back to the category check.
        """
        try:
            analytics = await self._analytics_query(days, source_id, tenant_id, source_ids, scenario_id)
            total = 0
            toxic = 0
            scores: list[float] = []
            categories: Counter = Counter()

            for a in analytics:
                text = self._text_analysis(a.summary_data)
                score = text.get("toxicity_score")
                level = self._field(text, "toxicity_level")
                if level is not None:
                    # The level wins over the score. Both describe the same
                    # judgment, and a row can carry a stale score next to a
                    # freshly-read level (the fields are separate prompts), so
                    # trusting the score would report a "low" row as toxic. The
                    # level is mapped to a score so the average stays numeric.
                    # An unmappable level (a legacy free-text category like
                    # "Очень токсичный") keeps the row's own score, since
                    # dropping it would lose the only numeric reading there is.
                    mapped = self.TOXICITY_LEVEL_SCORES.get(str(level).lower())
                    if mapped is not None:
                        score = mapped
                if score is None and level is None:
                    continue
                total += 1
                if score is not None:
                    try:
                        score_f = float(score)
                    except (TypeError, ValueError):
                        score_f = None
                else:
                    score_f = None
                if score_f is not None:
                    scores.append(score_f)
                    if score_f >= threshold:
                        toxic += 1
                elif str(level).lower() not in self.CLEAN_LEVELS:
                    # No numeric score to go by: fall back to the band. A level
                    # that is not clean counts as toxic.
                    toxic += 1
                if level:
                    categories[str(level)] += 1

            return {
                "analyzed": total,
                "toxic": toxic,
                "toxic_percent": round((toxic / total * 100), 1) if total else 0.0,
                "avg_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
                "categories": dict(categories.most_common(6)),
            }
        except Exception as e:
            logger.error(f"Error getting toxicity summary: {e}", exc_info=True)
            return {}

    async def get_top_hashtags(
        self,
        source_id: Optional[int] = None,
        days: int = 7,
        tenant_id: Optional[int] = None,
        source_ids: Optional[list[int]] = None,
        limit: int = 10,
        scenario_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Top hashtags by occurrence count over the period."""
        try:
            analytics = await self._analytics_query(days, source_id, tenant_id, source_ids, scenario_id)
            counter: Counter = Counter()
            for a in analytics:
                for tag in self._extract_hashtags(a.summary_data):
                    counter[tag.lower()] += 1
            return [{"hashtag": tag, "count": count} for tag, count in counter.most_common(limit)]
        except Exception as e:
            logger.error(f"Error getting top hashtags: {e}", exc_info=True)
            return []

    async def get_brand_mention_stats(
        self,
        source_id: Optional[int] = None,
        days: int = 7,
        tenant_id: Optional[int] = None,
        source_ids: Optional[list[int]] = None,
        scenario_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Brand mention volume and its sentiment split.

        Two shapes reach here. The current contract emits one `brand`/`context`/
        `sentiment` per analysis — a name plus a 0..1 score — while rows written
        before it carry `mention_count` (an integer) and `mention_sentiment`
        (a category label). Counting mentions means summing the old integer and
        simply counting the new rows, so both are read here rather than assuming
        one of them.
        """
        try:
            analytics = await self._analytics_query(days, source_id, tenant_id, source_ids, scenario_id)
            rows_with_mentions = 0
            total_mentions = 0
            sentiment: Counter = Counter()
            positive = neutral = negative = 0

            for a in analytics:
                text = self._text_analysis(a.summary_data)
                legacy_count = text.get("mention_count")
                brand = self._field(text, "brand")

                if legacy_count is not None:
                    try:
                        total_mentions += int(legacy_count)
                        rows_with_mentions += 1
                    except (TypeError, ValueError):
                        continue
                    label = self._field(text, "sentiment")
                    if label:
                        sentiment[str(label)] += 1
                    continue

                if not brand:
                    continue
                rows_with_mentions += 1
                total_mentions += 1
                score = self._field(text, "sentiment")
                if isinstance(score, (int, float)):
                    if score > 0.6:
                        positive += 1
                    elif score < 0.4:
                        negative += 1
                    else:
                        neutral += 1
                elif score:
                    sentiment[str(score)] += 1

            result: dict[str, Any] = {
                "rows_with_mentions": rows_with_mentions,
                "total_mentions": total_mentions,
                "avg_per_analysis": round(total_mentions / rows_with_mentions, 1) if rows_with_mentions else 0.0,
                "sentiment": dict(sentiment.most_common(5)),
            }
            if positive or neutral or negative:
                result["sentiment_split"] = {"positive": positive, "neutral": neutral, "negative": negative}
            return result
        except Exception as e:
            logger.error(f"Error getting brand mention stats: {e}", exc_info=True)
            return {}

    async def get_viral_content(
        self,
        source_id: Optional[int] = None,
        days: int = 7,
        tenant_id: Optional[int] = None,
        source_ids: Optional[list[int]] = None,
        limit: int = 5,
        scenario_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Rows flagged viral: potential, growth rate, sorted by rank."""
        try:
            analytics = await self._analytics_query(days, source_id, tenant_id, source_ids, scenario_id)
            items = []
            for a in analytics:
                text = self._text_analysis(a.summary_data)
                potential = self._field(text, "viral_score")
                growth = text.get("growth_rate")
                if potential is None and growth is None:
                    reach = text.get("predicted_reach")
                    if reach is not None:
                        potential = reach
                if potential is None and growth is None:
                    legacy = (a.summary_data or {}).get("ai_analysis") or {}
                    potential = (legacy.get("engagement_analysis") or {}).get("viral_potential")
                if potential is None and growth is None:
                    continue
                try:
                    growth_f = float(growth) if growth is not None else 0.0
                except (TypeError, ValueError):
                    growth_f = 0.0
                # A numeric viral_score is a score, not a label, so rank on its
                # own value; a label (the older contract) maps to a rank.
                if isinstance(potential, (int, float)) and not isinstance(potential, bool):
                    rank = 3 if potential >= 0.7 else 2 if potential >= 0.4 else 1
                    sort_key = float(potential)
                else:
                    rank = {
                        "высокий": 3,
                        "high": 3,
                        "средний": 2,
                        "medium": 2,
                        "низкий": 1,
                        "low": 1,
                    }.get(str(potential).lower(), 0)
                    sort_key = float(rank)
                items.append(
                    {
                        "analysis_title": (a.summary_data or {}).get("analysis_title") or "",
                        "date": a.analysis_date.isoformat() if a.analysis_date else "",
                        "source_id": a.source_id,
                        "viral_potential": potential,
                        "growth_rate": growth_f,
                        "predicted_reach": text.get("predicted_reach"),
                        "viral_factors": text.get("viral_factors") or [],
                        "rank": rank,
                        "_sort": sort_key,
                    }
                )
            items.sort(key=lambda x: (x["_sort"], x["growth_rate"]), reverse=True)
            for item in items:
                item.pop("_sort", None)
            return items[:limit]
        except Exception as e:
            logger.error(f"Error getting viral content: {e}", exc_info=True)
            return []

    async def get_influencer_impact(
        self,
        source_id: Optional[int] = None,
        days: int = 7,
        tenant_id: Optional[int] = None,
        source_ids: Optional[list[int]] = None,
        limit: int = 5,
        scenario_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Influencer activity captured by the analysis, if the pipeline wrote it."""
        try:
            analytics = await self._analytics_query(days, source_id, tenant_id, source_ids, scenario_id)
            items: dict[str, dict[str, Any]] = {}
            for a in analytics:
                text = self._text_analysis(a.summary_data)
                # The current contract writes the influencer flat on the text
                # analysis; earlier ones nested a list under `influencers`.
                flat_name = self._field(text, "influencer_name")
                flat_impact = self._field(text, "impact_score")
                candidates: list[tuple[Any, Any]] = []
                if flat_name:
                    candidates.append((flat_name, flat_impact))
                raw = self._digest_value(a.summary_data, ("influencers", "influencer_impact"))
                if raw:
                    entries = raw if isinstance(raw, list) else [raw]
                    for entry in entries:
                        if isinstance(entry, dict):
                            candidates.append(
                                (
                                    entry.get("name") or entry.get("author") or entry.get("username"),
                                    entry.get("impact") or entry.get("engagement_rate") or entry.get("score"),
                                )
                            )
                        else:
                            candidates.append((entry, None))
                for name, impact in candidates:
                    if not name:
                        continue
                    key = str(name)
                    current = items.setdefault(
                        key,
                        {"name": key, "impact": "", "source_id": a.source_id, "_score": 0.0, "_label": ""},
                    )
                    # A numeric impact is a score to sum; a word ("высокое") is a
                    # label and must survive as-is, since there is nothing to add.
                    try:
                        current["_score"] += float(impact) if impact is not None else 0.0
                    except (TypeError, ValueError):
                        if impact:
                            current["_label"] = str(impact)
            for item in items.values():
                score = item.pop("_score")
                label = item.pop("_label")
                item["impact"] = round(score, 2) if score else label
                # `impact` is a number on current rows and a word on older ones,
                # so it cannot be compared across rows as-is; rank by the number
                # it sums to, which is 0 for a label.
                item["_rank"] = score
            ranked = sorted(items.values(), key=lambda x: x["_rank"], reverse=True)
            for item in ranked:
                item.pop("_rank", None)
            return ranked[:limit]
        except Exception as e:
            logger.error(f"Error getting influencer impact: {e}", exc_info=True)
            return []

    async def get_competitor_activity(
        self,
        source_id: Optional[int] = None,
        days: int = 7,
        tenant_id: Optional[int] = None,
        source_ids: Optional[list[int]] = None,
        scenario_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Competitor mentions: names and how often they surfaced."""
        try:
            analytics = await self._analytics_query(days, source_id, tenant_id, source_ids, scenario_id)
            counter: Counter = Counter()
            threats: Counter = Counter()
            for a in analytics:
                text = self._text_analysis(a.summary_data)
                flat = self._field(text, "competitor")
                if flat:
                    counter[str(flat)] += 1
                    threat = self._field(text, "threat_level")
                    if threat:
                        threats[str(threat)] += 1
                raw = self._digest_value(a.summary_data, ("competitors", "competitor_activity"))
                if isinstance(raw, list):
                    names = [
                        str(x) if isinstance(x, str) else (x.get("name") if isinstance(x, dict) else "") for x in raw
                    ]
                elif isinstance(raw, dict):
                    names = [str(raw["name"])] if raw.get("name") else []
                else:
                    names = []
                for name in names:
                    if name:
                        counter[name] += 1
            result: dict[str, Any] = {
                "competitors": [{"name": name, "mentions": count} for name, count in counter.most_common(8)],
                "total": sum(counter.values()),
            }
            if threats:
                result["threat_levels"] = dict(threats.most_common(6))
            return result
        except Exception as e:
            logger.error(f"Error getting competitor activity: {e}", exc_info=True)
            return {}

    async def get_intent_distribution(
        self,
        source_id: Optional[int] = None,
        days: int = 7,
        tenant_id: Optional[int] = None,
        source_ids: Optional[list[int]] = None,
        scenario_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Customer-intent distribution (primary + secondary intents)."""
        try:
            analytics = await self._analytics_query(days, source_id, tenant_id, source_ids, scenario_id)
            counter: Counter = Counter()
            confidences: list[float] = []
            for a in analytics:
                text = self._text_analysis(a.summary_data)
                # Keyed on the exact name, not an alias: a legacy row also has
                # `primary_intent`, and treating that as the current shape would
                # skip its `secondary_intents`.
                flat = text.get("intent_type")
                if flat:
                    counter[str(flat).lower()] += 1
                    try:
                        confidence = float(text.get("confidence"))  # type: ignore[arg-type]
                    except (TypeError, ValueError):
                        confidence = None
                    if confidence is not None:
                        confidences.append(confidence)
                    continue
                primary = self._digest_value(a.summary_data, ("primary_intent", "user_intent"))
                if primary:
                    counter[str(primary).lower()] += 1
                secondary = self._digest_value(a.summary_data, ("secondary_intents",))
                if isinstance(secondary, list):
                    for s in secondary:
                        if s:
                            counter[str(s).lower()] += 1
            result: dict[str, Any] = {"distribution": dict(counter.most_common(8)), "total": sum(counter.values())}
            if confidences:
                result["avg_confidence"] = round(sum(confidences) / len(confidences), 2)
            return result
        except Exception as e:
            logger.error(f"Error getting intent distribution: {e}", exc_info=True)
            return {}

    async def get_demographics_breakdown(
        self,
        source_id: Optional[int] = None,
        days: int = 7,
        tenant_id: Optional[int] = None,
        source_ids: Optional[list[int]] = None,
        scenario_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Audience demographics the analysis captured (age / locations / interests)."""
        try:
            analytics = await self._analytics_query(days, source_id, tenant_id, source_ids, scenario_id)
            age: Counter = Counter()
            locations: Counter = Counter()
            interests: Counter = Counter()
            for a in analytics:
                text = self._text_analysis(a.summary_data)
                # Current contract: flat age_range / gender_dist / top_locations.
                flat_age = text.get("age_range")
                if flat_age:
                    age[str(flat_age)] += 1
                flat_locations = text.get("top_locations") or []
                if isinstance(flat_locations, list):
                    for loc in flat_locations:
                        if isinstance(loc, str):
                            locations[loc] += 1
                raw = self._digest_value(a.summary_data, ("demographics", "demographic"))
                if not isinstance(raw, dict):
                    continue
                for group in raw.get("age_groups") or raw.get("age") or []:
                    if isinstance(group, str):
                        age[group] += 1
                for loc in raw.get("locations") or raw.get("location") or []:
                    if isinstance(loc, str):
                        locations[loc] += 1
                for interest in raw.get("interests") or raw.get("interest") or []:
                    if isinstance(interest, str):
                        interests[interest] += 1
            return {
                "age_groups": dict(age.most_common(6)),
                "locations": dict(locations.most_common(6)),
                "interests": dict(interests.most_common(8)),
            }
        except Exception as e:
            logger.error(f"Error getting demographics breakdown: {e}", exc_info=True)
            return {}

    # ── hybrid digest brief ─────────────────────────────────────────────────

    _DIGEST_METHOD_MAP: dict[str, tuple[str, str]] = {
        "toxicity": ("Токсичность", "get_toxicity_summary"),
        "hashtag_analysis": ("Хэштеги", "get_top_hashtags"),
        "brand_mentions": ("Упоминания бренда", "get_brand_mention_stats"),
        "viral_detection": ("Вирусный контент", "get_viral_content"),
        "influencer": ("Инфлюенсеры", "get_influencer_impact"),
        "competitor": ("Конкуренты", "get_competitor_activity"),
        "intent": ("Намерения пользователей", "get_intent_distribution"),
        "demographics": ("Демография", "get_demographics_breakdown"),
    }

    # Aliased rather than a @property: both `_specialized_sections()` (a
    # classmethod, reached as `cls.DIGEST_SPECIALIZED`) and the contract test
    # (`ReportAggregator.DIGEST_SPECIALIZED`) read it off the class, and a
    # property does not resolve through `cls`/`Class` — it would surface the
    # descriptor, not the dict. A plain class attribute serves both.
    DIGEST_SPECIALIZED: dict[str, tuple[str, str]] = _DIGEST_METHOD_MAP

    async def generate_digest_brief(
        self,
        period: str = "day",  # "day" | "week"
        source_ids: Optional[list[int]] = None,
        group_by: str = "themes",  # GroupingAxis value: themes | sources | entities | sentiment | content_type | intent
        time_breakdown: bool = False,
        scenario_id: Optional[int] = None,
    ) -> str:
        """Structured Markdown brief for the digest period — pure algorithm, no LLM.

        Hybrid digest step 1. It aggregates the period's `ai_analytics` rows into
        a human-readable brief, groups them by the `group_by` axis (themes → top
        themes, sources → per source, entities → per entity, sentiment → by
        sentiment bucket, content_type → by media type, intent → by intent) and
        appends a section for each `analysis_types` the scenario enables. The
        narrative step turns this brief into the final digest text.

        Returns an empty string when there is nothing to report; the digest
        still ships, the LLM step just gets a sparse context.
        """
        from datetime import timedelta

        days = {"day": 1, "week": 7, "month": 30}.get(period, 1)
        end = date.today()
        start = end - timedelta(days=days - 1)

        analytics = await self._analytics_query(days=days, source_ids=source_ids, scenario_id=scenario_id)
        if not analytics:
            return ""

        enabled: Optional[set[str]] = None
        if scenario_id:
            from app.models import AgentScenario

            scenario = await AgentScenario.objects.get(id=scenario_id)
            enabled = {str(t) for t in (scenario.analysis_types or [])}

        lines: list[str] = []
        title = {"day": "Дайджест за день", "week": "Дайджест за неделю", "month": "Дайджест за месяц"}.get(
            period, "Дайджест"
        )
        lines.append(f"## {title}")
        lines.append(f"**Период:** {start.isoformat()} — {end.isoformat()}")

        mode = group_by or "themes"
        section = await self._brief_base_group(analytics, mode, time_breakdown)
        if section:
            lines.extend(section)

        # Theme chains — an independent cross-mode grouping. A chain (one
        # `topic_chain_id`) groups the analyses of one ongoing theme/source/user
        # over time, so it answers "what kept happening across the period" in a
        # way the mode grouping (which re-reads topics from each row) does not.
        chains_section = self._group_by_chains(analytics)
        if chains_section:
            lines.extend(chains_section)

        for name, label, method_name in self._specialized_sections():
            if enabled is not None and name not in enabled:
                continue
            method = getattr(self, method_name)
            # Same scenario filter as the base group: a row written by another
            # scenario's analysis_types must not leak into this brief's sections.
            result = await method(days=days, source_ids=source_ids, scenario_id=scenario_id)
            section = self._format_specialized_section(label, name, result)
            if section:
                lines.extend(section)

        # Movement against the preceding window. The sections above each describe
        # the period in isolation — "12 toxic posts" says nothing about whether
        # that is worse than usual, which is the part a reader acts on.
        dynamics = await self._brief_dynamics(days, source_ids, scenario_id)
        if dynamics:
            lines.extend(dynamics)

        # Chain movement against the preceding window: which chains are new and
        # which continued with more entries.
        chain_dynamics = await self._chain_dynamics(days, source_ids, scenario_id)
        if chain_dynamics:
            lines.extend(chain_dynamics)

        return "\n".join(lines)

    async def _brief_dynamics(
        self,
        days: int,
        source_ids: Optional[list[int]] = None,
        scenario_id: Optional[int] = None,
    ) -> list[str]:
        """Compare the period against the one before it.

        Reads the previous window directly rather than through the reporting
        methods: those take a look-back `days`, not a date range, so reusing them
        would compare "the last 7 days" against "the 7 days before now" — an
        overlapping window that dilutes every delta by half and would report no
        change in a period that moved a lot.
        """
        end = date.today() - timedelta(days=days)
        start = end - timedelta(days=days - 1)

        current = await self._window_sentiment_toxicity(date.today() - timedelta(days=days - 1), date.today())
        previous = await self._window_sentiment_toxicity(start, end)
        if not current or not previous:
            return []

        lines = ["", "## Динамика к прошлому периоду"]
        for line in self._sentiment_delta(current, previous):
            lines.append(line)
        for line in self._toxicity_delta(current, previous):
            lines.append(line)
        return lines if len(lines) > 2 else []

    async def _window_sentiment_toxicity(self, start: date, end: date) -> Optional[dict[str, Any]]:
        """Average sentiment and toxicity share for one date range."""
        try:
            qs = AIAnalytics.objects.filter(analysis_date__gte=start, analysis_date__lte=end)
            analytics = await qs
            if not analytics:
                return None

            scores: list[float] = []
            toxic = 0
            scored = 0
            for a in analytics:
                sentiment = self._extract_sentiment(a.summary_data)
                if sentiment and sentiment.get("score") is not None:
                    try:
                        scores.append(float(sentiment["score"]))
                    except (TypeError, ValueError):
                        pass

                text = self._text_analysis(a.summary_data)
                score = text.get("toxicity_score")
                level = self._field(text, "toxicity_level")
                if level is not None:
                    mapped = self.TOXICITY_LEVEL_SCORES.get(str(level).lower())
                    if mapped is not None:
                        score = mapped
                if score is None and level is None:
                    continue
                scored += 1
                try:
                    if float(score) >= 0.7:
                        toxic += 1
                except (TypeError, ValueError):
                    pass

            if not scores and not scored:
                return None
            return {
                "avg_sentiment": sum(scores) / len(scores) if scores else None,
                "toxic_percent": (toxic / scored * 100) if scored else None,
            }
        except Exception as e:
            logger.warning(f"Dynamics window {start}..{end} unavailable: {e}")
            return None

    def _sentiment_delta(self, current: dict, previous: dict) -> list[str]:
        """The sentiment movement, if both windows carry a score."""
        cur, prev = current.get("avg_sentiment"), previous.get("avg_sentiment")
        if cur is None or prev is None:
            return []
        diff = cur - prev
        if abs(diff) < 0.02:
            return [f"- Тональность без изменений (средняя {cur:.2f})"]
        direction = "упала" if diff < 0 else "выросла"
        return [
            f"- Тональность {direction}: {prev:.2f} → {cur:.2f} ({diff:+.2f})",
        ]

    def _toxicity_delta(self, current: dict, previous: dict) -> list[str]:
        """The toxicity movement, if both windows carry a share.

        A share is only compared when the previous window had enough rows to mean
        anything: one toxic post out of one is a 100% share that would read as a
        catastrophe and send the reader after a non-event.
        """
        cur, prev = current.get("toxic_percent"), previous.get("toxic_percent")
        if cur is None or prev is None:
            return []
        diff = cur - prev
        if abs(diff) < 1.0:
            return [f"- Токсичность без изменений ({cur:.1f}%)"]
        direction = "выросла" if diff > 0 else "снизилась"
        return [
            f"- Токсичность {direction}: {prev:.1f}% → {cur:.1f}% ({diff:+.1f} п.п.)",
        ]

    @classmethod
    def _specialized_sections(cls):
        return [(name, label, method) for name, (label, method) in cls.DIGEST_SPECIALIZED.items()]

    async def _brief_base_group(self, analytics, axis: str = "themes", time_breakdown: bool = False) -> list[str]:
        """Group the period's analytics by axis into markdown lines."""
        from app.services.ai.grouping import group_analytics
        from app.types.enums.bot_types import GroupingAxis

        try:
            result = await group_analytics(analytics, axis, time_breakdown=time_breakdown)
        except ValueError:
            # Invalid axis — fall back to themes
            result = await group_analytics(analytics, GroupingAxis.THEMES, time_breakdown=time_breakdown)

        return self._render_grouped_result(result)

    def _render_grouped_result(self, result: dict[str, Any]) -> list[str]:
        """Convert group_analytics() result to markdown lines."""
        from app.types.enums.bot_types import GroupingAxis

        axis = result.get("axis", "themes")
        groups = result.get("groups", [])
        if not groups:
            return []

        try:
            label = GroupingAxis(axis).display_name
        except ValueError:
            label = axis

        lines = ["", f"## {label}"]
        for i, group in enumerate(groups, 1):
            key = group["key"]
            count = group["count"]
            avg_sent = group.get("avg_sentiment")
            sent_str = f" (sent: {avg_sent})" if avg_sent is not None else ""

            # No extras — the old DAYS and MONITORED_USERS axes are gone.
            # Grouping is now pure axis-based; posts/messages/users are
            # available in the per-date entries when time_breakdown=True.
            extras = []

            extra_str = ""
            if extras:
                extra_str = f" ({', '.join(extras)})"

            if result.get("time_breakdown") and group.get("entries"):
                lines.append(f"{i}. **{key}** — {count} упом.{extra_str}{sent_str}")
                for entry in group["entries"]:
                    day = entry["date"]
                    day_count = entry["count"]
                    day_sent = entry.get("avg_sentiment")
                    day_sent_str = f" (sent: {day_sent})" if day_sent is not None else ""
                    lines.append(f"  - {day}: {day_count} упом.{day_sent_str}")
            else:
                lines.append(f"{i}. **{key}** — {count} упом.{extra_str}{sent_str}")

        return lines

    def _group_by_chains(self, analytics, limit: int = 8) -> list[str]:
        """Top chains by entry count — the "Цепочки" section of the brief.

        Groups the period's analytics by `topic_chain_id` (the only cross-row
        link, per the analytics-chains contract). The label comes from the row's
        `chain_label` (falls back to the raw chain id); each entry reports the
        date range and average sentiment of the chain in the period.
        """
        by_chain: dict[str, list[Any]] = defaultdict(list)
        for a in analytics:
            if a.topic_chain_id:
                by_chain[a.topic_chain_id].append(a)
        if not by_chain:
            return []

        lines = ["", "## Цепочки"]
        for chain_id, rows in sorted(by_chain.items(), key=lambda kv: -len(kv[1]))[:limit]:
            label = next(
                (r.chain_label for r in rows if getattr(r, "chain_label", None)),
                None,
            )
            if not label:
                label = next(
                    (human_chain_label(r.summary_data) for r in rows if human_chain_label(r.summary_data)),
                    chain_id,
                )
            dates = [r.analysis_date for r in rows if r.analysis_date]
            scores = []
            for r in rows:
                sent = self._extract_sentiment(r.summary_data)
                if sent and sent.get("score") is not None:
                    scores.append(float(sent["score"]))
            span = ""
            if dates:
                span = f" ({min(dates).isoformat()} — {max(dates).isoformat()})"
            mood = f", ср. тональность {sum(scores) / len(scores):.2f}" if scores else ""
            lines.append(f"- **{label}** — {len(rows)} записей{span}{mood}")
        return lines

    async def _chain_dynamics(
        self,
        days: int,
        source_ids: Optional[list[int]] = None,
        scenario_id: Optional[int] = None,
    ) -> list[str]:
        """New vs continued chains, compared against the preceding window.

        A chain that has entries in the current period but none in the one
        before is "new"; a chain present in both windows whose current count is
        higher is "continued" (with the delta). Mirrors the dynamics section's
        non-overlapping windows.
        """
        from datetime import timedelta

        end = date.today()
        start = end - timedelta(days=days - 1)
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=days - 1)

        current = await self._chain_stats(start, end, source_ids, scenario_id)
        previous = await self._chain_stats(prev_start, prev_end, source_ids, scenario_id)
        if not current:
            return []

        def _label(cid: str) -> str:
            return current[cid]["label"] or cid

        lines = ["", "## Динамика цепочек"]
        wrote = False

        new_chains = sorted(
            ((cid, n["count"]) for cid, n in current.items() if cid not in previous),
            key=lambda kv: -kv[1],
        )[:5]
        for cid, n in new_chains:
            lines.append(f"- Новая цепочка: **{_label(cid)}** ({n} записей)")
            wrote = True

        continued = sorted(
            (
                (cid, n["count"])
                for cid, n in current.items()
                if cid in previous and n["count"] > previous[cid]["count"]
            ),
            key=lambda kv: kv[1] - previous[kv[0]]["count"],
            reverse=True,
        )[:5]
        for cid, n in continued:
            lines.append(f"- Продолжение: **{_label(cid)}** (+{n - previous[cid]['count']} записей)")
            wrote = True

        return lines if wrote else []

    async def _chain_stats(
        self,
        start: date,
        end: date,
        source_ids: Optional[list[int]],
        scenario_id: Optional[int],
    ) -> dict[str, dict[str, Any]]:
        """Chain id → {count, label} for one date range (tenant-scoped read)."""
        qs = AIAnalytics.objects.filter(
            AIAnalytics.topic_chain_id.isnot(None),
            analysis_date__gte=start,
            analysis_date__lte=end,
        )
        if source_ids:
            qs = qs.filter(source_id__in=source_ids)
        rows = await qs
        stats: dict[str, dict[str, Any]] = {}
        for r in rows:
            label = r.chain_label or human_chain_label(r.summary_data) or r.topic_chain_id
            entry = stats.setdefault(r.topic_chain_id, {"count": 0, "label": label})
            entry["count"] += 1
            if label:
                entry["label"] = label
        return stats

    @staticmethod
    def _chain_author(chain_id: Optional[str]) -> Optional[str]:
        """Extract the tracked user from a chain id like `src_5_user_123`.

        Only the `monitored_users` mode builds per-person chains; rows produced
        by the other modes return None and are skipped by the grouping.
        """
        if not chain_id:
            return None
        marker = "_user_"
        if marker in chain_id:
            return chain_id.split(marker, 1)[1]
        return None

    async def _source_name_map(self, source_ids: list[int]) -> dict[int, str]:
        if not source_ids:
            return {}
        from app.models import Source

        rows = await Source.objects.filter(id__in=source_ids)
        return {s.id: s.name for s in rows}

    def _format_specialized_section(self, label: str, name: str, result: Any) -> list[str]:
        """Turn an aggregation result into markdown lines ([] when empty)."""
        if not result:
            return []
        lines = ["", f"## {label}"]
        if name == "toxicity":
            lines.append(f"**Проанализировано:** {result.get('analyzed', 0)}")
            lines.append(f"**Токсичных:** {result.get('toxic', 0)} ({result.get('toxic_percent', 0)}%)")
            lines.append(f"**Средний балл:** {result.get('avg_score', 0)}")
            categories = result.get("categories") or {}
            if categories:
                lines.append("**Категории:** " + ", ".join(f"{k} — {v}" for k, v in list(categories.items())[:5]))
        elif name == "hashtag_analysis":
            for i, item in enumerate(result[:10], 1):
                lines.append(f"{i}. **{item.get('hashtag', '?')}** — {item.get('count', 0)}")
        elif name == "brand_mentions":
            lines.append(f"**Всего упоминаний:** {result.get('total_mentions', 0)}")
            lines.append(f"**Строк с упоминаниями:** {result.get('rows_with_mentions', 0)}")
            lines.append(f"**В среднем на анализ:** {result.get('avg_per_analysis', 0)}")
            sentiment = result.get("sentiment") or {}
            if sentiment:
                lines.append("**Тональность:** " + ", ".join(f"{k} — {v}" for k, v in list(sentiment.items())[:5]))
            split = result.get("sentiment_split") or {}
            if split:
                lines.append("**Распределение:** " + ", ".join(f"{k} — {v}" for k, v in list(split.items())[:5]))
        elif name == "viral_detection":
            for i, item in enumerate(result[:5], 1):
                title = item.get("analysis_title") or item.get("date") or "?"
                potential = item.get("viral_potential") or "?"
                growth = item.get("growth_rate") or 0
                reach = item.get("predicted_reach")
                suffix = f", охват {reach}" if reach is not None else ""
                lines.append(f"{i}. **{title}** — потенциал {potential}, рост ×{growth}{suffix}")
        elif name == "influencer":
            for i, item in enumerate(result[:5], 1):
                impact = f", влияние {item['impact']}" if item.get("impact") else ""
                lines.append(f"{i}. **{item.get('name', '?')}**{impact}")
        elif name == "competitor":
            lines.append(f"**Всего упоминаний:** {result.get('total', 0)}")
            for item in result.get("competitors", [])[:8]:
                lines.append(f"- **{item.get('name', '?')}** — {item.get('mentions', 0)}")
            threat_levels = result.get("threat_levels") or {}
            if threat_levels:
                lines.append(
                    "**Уровни угрозы:** " + ", ".join(f"{k} — {v}" for k, v in list(threat_levels.items())[:5])
                )
        elif name == "intent":
            lines.append(f"**Всего:** {result.get('total', 0)}")
            if result.get("avg_confidence") is not None:
                lines.append(f"**Средняя уверенность:** {result.get('avg_confidence')}")
            for intent, count in (result.get("distribution") or {}).items():
                lines.append(f"- **{intent}** — {count}")
        elif name == "demographics":
            for key in ("age_groups", "locations", "interests"):
                values = result.get(key) or {}
                if values:
                    lines.append(f"**{key}:** " + ", ".join(f"{k} — {v}" for k, v in list(values.items())[:6]))
        return lines

    # Helper methods for data extraction

    def _field(self, nest: dict, logical_name: str):
        """One logical field of a nest, tolerating the previous field names.

        Tries every alias in `FIELD_ALIASES` (newest first) and returns the first
        value that is actually present, so a row written before a rename keeps
        counting instead of dropping out of the aggregation.
        """
        if not isinstance(nest, dict):
            return None
        for key in self.FIELD_ALIASES.get(logical_name, (logical_name,)):
            value = nest.get(key)
            if value is not None and value != "":
                return value
        return None

    def _text_analysis(self, summary_data: dict) -> dict:
        """Parsed text-analysis payload (the LLM's JSON for the text content)."""
        return ((summary_data or {}).get("multi_llm_analysis") or {}).get("text_analysis") or {}

    def _digest_value(self, summary_data: dict, keys: tuple[str, ...]):
        """First non-empty value for `keys` across the known JSONB shapes.

        The same logical field lands in different nests depending on the analysis
        version: the v3 `multi_llm_analysis.text_analysis` object, the
        `unified_summary`, the legacy `ai_analysis`/`ai_analysis` nested object,
        or the top level. Reading all of them keeps the digest correct for rows
        written by any pipeline.
        """
        if not summary_data:
            return None
        nests = [
            (summary_data.get("multi_llm_analysis") or {}).get("text_analysis"),
            summary_data.get("unified_summary"),
            summary_data.get("ai_analysis"),
            summary_data,
        ]
        for nest in nests:
            if not isinstance(nest, dict):
                continue
            for key in keys:
                value = nest.get(key)
                if value is not None and value != "" and value != []:
                    return value
        return None

    def _extract_hashtags(self, summary_data: dict) -> list[str]:
        """Explicit hashtags from the analysis, else `#`-prefixed keywords.

        The current contract writes hashtags as objects — `{"tag": "тег",
        "count": 5, "sentiment": 0.8}` — so the name is taken from the `tag`
        key. Plain strings (the earlier contract, and rows derived from content)
        still pass through, which is why this accepts both rather than assuming.
        """
        text = self._text_analysis(summary_data)
        raw = text.get("hashtags") or []
        if not raw:
            legacy = (summary_data or {}).get("ai_analysis") or {}
            raw = (legacy.get("content_analysis") or {}).get("hashtags") or []
        out: list[str] = []
        for h in raw:
            if isinstance(h, str) and h:
                out.append(h)
            elif isinstance(h, dict):
                tag = h.get("tag") or h.get("hashtag") or h.get("name")
                if tag:
                    out.append(str(tag))
        if not out:
            keywords = self._digest_value(summary_data, ("keywords",))
            if isinstance(keywords, list):
                out = [str(k) for k in keywords if isinstance(k, str) and k.startswith("#")]
        return out

    def _extract_sentiment(self, summary_data: dict) -> Optional[dict]:
        """Extract sentiment data from summary_data JSON."""
        if not summary_data:
            return None

        # Try new structure first (v3.0-multi-llm)
        multi_llm = summary_data.get("multi_llm_analysis", {})
        text_analysis = multi_llm.get("text_analysis", {})

        # New structure: sentiment_score in text_analysis
        if "sentiment_score" in text_analysis:
            score = text_analysis["sentiment_score"]
            # Determine label from score
            if score > 0.6:
                label = "positive"
            elif score < 0.4:
                label = "negative"
            else:
                label = "neutral"
            return {"label": label, "score": score}

        # Fallback: Try to infer from overall_mood (text description)
        if "overall_mood" in text_analysis:
            mood_text = str(text_analysis["overall_mood"]).lower()
            # Simple heuristic based on keywords
            if any(word in mood_text for word in ["позитивн", "хорош", "положительн", "радост", "оптимист"]):
                return {"label": "positive", "score": 0.7}
            elif any(word in mood_text for word in ["негативн", "плох", "отрицательн", "грустн", "пессимист"]):
                return {"label": "negative", "score": 0.3}
            else:
                return {"label": "neutral", "score": 0.5}

        # Fallback: Try old structure
        ai_analysis = summary_data.get("ai_analysis", {})

        # Path 1: sentiment_analysis object
        sentiment = ai_analysis.get("sentiment_analysis", {})
        if sentiment and "overall_sentiment" in sentiment:
            overall = sentiment["overall_sentiment"]
            return {"label": overall.get("label", "neutral"), "score": overall.get("score", 0)}

        # Path 2: direct sentiment field
        if "sentiment" in ai_analysis:
            return {"label": ai_analysis["sentiment"], "score": 0}  # No score available

        # No sentiment data found
        return None

    def _extract_topics(self, summary_data: dict) -> list[str]:
        """Extract topics/keywords from summary_data JSON.

        Each topic is reduced to a single string so it can be a Counter key.
        A topic may arrive as a plain string ("россия") or as a structured
        object ({"name": "россия", "confidence": 0.9}) depending on which
        analysis contract wrote the row — both are flattened here, and any
        non-string/non-dict value is skipped rather than crashing the widget.
        """
        if not summary_data:
            return []

        topics: list[str] = []

        def _coerce(topic: Any) -> str | None:
            if isinstance(topic, str) and topic.strip():
                return topic.strip()
            if isinstance(topic, dict):
                for key in ("name", "topic", "keyword", "label", "title"):
                    val = topic.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
            return None

        # Try new structure first (v3.0-multi-llm)
        multi_llm = summary_data.get("multi_llm_analysis", {})
        text_analysis = multi_llm.get("text_analysis", {})

        # New structure: main_topics, highlights
        if "main_topics" in text_analysis:
            main_topics = text_analysis["main_topics"]
            if isinstance(main_topics, list):
                for topic in main_topics:
                    coerced = _coerce(topic)
                    if coerced:
                        topics.append(coerced)

        if "highlights" in text_analysis:
            highlights = text_analysis["highlights"]
            if isinstance(highlights, list):
                for topic in highlights:
                    coerced = _coerce(topic)
                    if coerced:
                        topics.append(coerced)

        if topics:
            return topics

        # Fallback: Try old structure
        ai_analysis = summary_data.get("ai_analysis", {})

        # Extract from various fields
        for field in ("key_topics", "categories", "keywords"):
            values = ai_analysis.get(field)
            if isinstance(values, list):
                for topic in values:
                    coerced = _coerce(topic)
                    if coerced:
                        topics.append(coerced)

        return topics

    def _extract_entities(self, summary_data: dict) -> list[dict]:
        """Extract mentioned entities from `summary_data` JSON.

        Contract: `[{"name": str, "type": "person|brand|org", "context": str}]`
        under `multi_llm_analysis.text_analysis.entities` (topics/trends types).
        Rows that predate the field or stored entities elsewhere contribute
        nothing — the widget groups by `name`/`type` across rows.
        """
        if not summary_data:
            return []
        text_analysis = (summary_data.get("multi_llm_analysis") or {}).get("text_analysis") or {}
        entities = text_analysis.get("entities") or []
        result: list[dict] = []
        for e in entities:
            if not isinstance(e, dict):
                continue
            name = str(e.get("name") or "").strip()
            if not name:
                continue
            result.append(
                {
                    "name": name[:255],
                    "type": str(e.get("type") or "org").strip()[:50] or "org",
                    "context": str(e.get("context") or "").strip()[:300],
                }
            )
        return result

    async def get_entity_mentions(
        self,
        source_id: Optional[int] = None,
        days: int = 7,
        limit: int = 20,
        tenant_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Aggregate entity mentions from analyses of the period.

        Groups the flat `entities` arrays by (name, type): each row may mention
        an entity once, so the mention count is how many analyses named it.
        Returns entities sorted by mention count with the average sentiment of
        the analyses they appear in. Each entry carries the id of the most recent
        analysis that mentioned it, so the widget can link to that record.
        """
        try:
            analytics = await self._analytics_query(days, source_id, tenant_id)

            counter = Counter()
            types: dict[str, str] = {}
            sentiments: dict[str, list[float]] = defaultdict(list)
            contexts: dict[str, str] = {}
            latest_analysis_id: dict[str, int] = {}
            latest_analysis_date: dict[str, Any] = {}

            for a in analytics:
                sentiment = self._extract_sentiment(a.summary_data)
                for e in self._extract_entities(a.summary_data):
                    key = e["name"]
                    counter[key] += 1
                    types[key] = e["type"]
                    if sentiment and sentiment.get("score") is not None:
                        sentiments[key].append(sentiment["score"])
                    if e["context"] and not contexts.get(key):
                        contexts[key] = e["context"]
                    # Track the most recent analysis that mentioned this entity,
                    # so the widget can link to it.
                    if a.id is not None:
                        if key not in latest_analysis_id or (a.analysis_date and latest_analysis_date.get(key) and a.analysis_date >= latest_analysis_date[key]):
                            latest_analysis_id[key] = a.id
                            latest_analysis_date[key] = a.analysis_date

            result = []
            for name, count in counter.most_common(limit):
                scores = sentiments.get(name, [])
                result.append(
                    {
                        "name": name,
                        "type": types.get(name, "org"),
                        "count": count,
                        "avg_sentiment": round(sum(scores) / len(scores), 2) if scores else None,
                        "context": contexts.get(name),
                        "analysis_id": latest_analysis_id.get(name),
                    }
                )
            return result

        except Exception as e:
            logger.error(f"Error getting entity mentions: {e}", exc_info=True)
            return []

    def _extract_example_text(self, summary_data: dict) -> Optional[str]:
        """Extract example text from summary_data."""
        if not summary_data:
            return None

        # Try to get first post or summary
        ai_analysis = summary_data.get("ai_analysis", {})

        if "summary" in ai_analysis:
            return ai_analysis["summary"][:200]  # Truncate

        return None

    def _extract_engagement(self, summary_data: dict) -> Optional[dict]:
        """Extract engagement metrics from summary_data."""
        if not summary_data:
            return None

        # Try new structure first (v3.0-multi-llm)
        content_statistics = summary_data.get("content_statistics", {})

        if content_statistics:
            return {
                "reactions": content_statistics.get("total_reactions", 0),
                "comments": content_statistics.get("total_comments", 0),
                "posts": content_statistics.get("total_posts", 1),
            }

        # Fallback: Try old structure
        content_stats = summary_data.get("content_stats", {})

        return {
            "reactions": content_stats.get("total_reactions", 0),
            "comments": content_stats.get("total_comments", 0),
            "posts": content_stats.get("total_posts", 1),
        }
