import asyncio
import logging
from typing import Any, Optional

from app.core.tenant_context import current_tenant_id, is_bypass, tenant_scope
from app.models import Platform, Source
from app.services.ai.analyzer import AIAnalyzer
from app.services.social.factory import get_social_client
from app.services.social.rate_limit import SourceRateLimited
from app.types import NotificationType, SourceType
from app.utils.collected_content import _build_permalink, build_staged_rows

# Try to import notification service
try:
	from app.services.notifications.messenger import messenger_service
	from app.services.notifications.service import notify

	NOTIFICATIONS_AVAILABLE = True
except ImportError:
	NOTIFICATIONS_AVAILABLE = False

logger = logging.getLogger(__name__)


def _collection_summary(context: Any) -> dict | None:
	"""A bounded task-collection summary, never the task payload itself."""
	summary = getattr(context, "summary", None)
	if not callable(summary):
		return None
	try:
		value = summary()
	except Exception:  # noqa: BLE001 - metadata must not fail a collection
		return None
	return value if isinstance(value, dict) else None


def _city_scope(context: Any) -> Any:
	"""The task's city preference as a selection scope, if one is configured."""
	city = getattr(getattr(context, "intent", None), "city", None)
	if city is None:
		return None
	from app.services.monitoring.selection import CityScope

	try:
		return CityScope(name=city.name, mode=city.mode)
	except Exception:  # noqa: BLE001 - a malformed city must not fail a collection
		logger.warning("collection_city_invalid error_code=city_invalid")
		return None


def _profiles_report(city_scope: Any, profiles: Any, supported: bool) -> dict | None:
	"""Enrichment evidence: what was asked and what the platform could answer."""
	if city_scope is None:
		return None
	report = profiles.summary() if profiles is not None else {}
	report["city"] = city_scope.name
	report["mode"] = city_scope.mode
	report["supported"] = supported
	if profiles is None:
		report["error_code"] = "profiles_operation_failed" if supported else "profiles_unsupported"
	return report


def _city_preview(selection_result: Any, city_scope: Any) -> dict | None:
	"""How the city layer decided this batch; `None` when no city is configured."""
	if city_scope is None:
		return None
	from app.services.monitoring.selection import CITY_MISMATCH, CITY_UNKNOWN

	return {
		"name": city_scope.name,
		"mode": city_scope.mode,
		"matched": getattr(selection_result, "city_matched", 0),
		"unknown": selection_result.count(CITY_UNKNOWN),
		"mismatch": selection_result.count(CITY_MISMATCH),
	}


def _dry_run_preview(content: list[dict], limit: int = 10, text_limit: int = 300) -> list[dict]:
	"""A bounded, offline preview of a rehearsal batch.

	Only the fields an operator needs to judge relevance; the full text stays
	in the platform response and is never persisted or logged by this path.
	"""
	rows: list[dict] = []
	for item in content[:limit]:
		date = item.get("date") or item.get("published_at")
		author = item.get("author") if isinstance(item.get("author"), dict) else None
		rows.append(
			{
				"external_id": item.get("external_id") or item.get("id"),
				"date": date.isoformat() if hasattr(date, "isoformat") else date,
				"author": author,
				"text": (item.get("text") or "")[:text_limit],
				"permalink": item.get("permalink"),
			}
		)
	return rows


class ContentCollector:
	"""Service for collecting content from social media sources"""

	def __init__(self):
		self.analyzer = AIAnalyzer()

	async def _count_new_items(self, content: list[dict], source: Source) -> int:
		"""How many of `content` this source has never handed us before.

		Delegates to the dedup module, which is the only place that knows about
		all three places a hash can be recorded (analysed, staged, collected
		inline). Over-reporting is recoverable — the run looks busier than it
		was — while under-reporting would claim there was nothing to do when
		there was.
		"""
		from app.services.ai.dedup import count_new_items

		return await count_new_items(content, source.id)

	async def _stage_items(
		self,
		content: list[dict],
		source: Source,
		run_id: Optional[int],
		*, durable: bool = False,
		selection: dict[str, tuple[str, str | None]] | None = None,
	) -> int:
		"""Park a fetched batch in `collected_items` as the durable copy.

		Called on *every* collection, analysis inline or not. Staging used to be
		the alternative to analysing, which made the raw copy exist only in the
		one case nobody wanted it in: a collect run that analysed inline kept
		its batch in a local variable for the length of one LLM call, so a
		crashed worker, a timeout or an outage left nothing behind. Written
		first and deleted only once an analysis has saved a result, the table is
		a write-ahead copy — which is the only reason it can promise "a failed
		analysis keeps the content for a retry".

		Telegram collection requests strict durable admission; errors propagate
		before inline analysis. Other platforms retain legacy count/error policy.
		Returns the number of rows written. The run's hash ledger lives on the
		call's result, not here, so it outlives the rows themselves.
		"""
		from app.core.database import new_session
		from app.models import CollectedItem
		# Telegram must not acknowledge a fetched cursor before durable admission.
		if durable:
			from app.services.monitoring.staging import admit_telegram_items
			return await admit_telegram_items(content, source, run_id=run_id, selection=selection)
		rows = [
			row
			for row in build_staged_rows(content, source, run_id, selection=selection)
			if row.get("selection_status") in ("selected", "needs_review")
		]
		if not rows:
			return 0

		session = new_session()
		try:
			async with session.begin():
				written = await CollectedItem.objects.store_items(session, rows)
		except Exception as e:  # noqa: BLE001 — staging must not fail a collection
			logger.warning("collector_staging_failed error_code=storage_operation_failed")
			return 0
		finally:
			await session.close()
		logger.info(f"Staged {written} raw items for source {source.id} (run {run_id})")
		return written

	async def _retire_staged(self, source: Source, analytics: list) -> int:
		"""Drop the raw rows whose content these analyses have actually saved.

		An `AIAnalytics` row carries `summary_data["content_hashes"]` — the items
		it covers. Retiring by exactly those hashes is what makes "delete only
		after a successful save" true rather than approximately true: an analysis
		that covered one day of a three-day batch retires that day and leaves the
		other two staged, instead of the whole batch being declared consumed.

		Never raises: the rows left behind are swept by the retention pass.
		"""
		from app.core.database import new_session
		from app.models import CollectedItem
		from app.services.ai.dedup import analysed_hashes

		hashes = analysed_hashes(analytics) | set(getattr(self.analyzer, "completed_hashes", set()))
		if not hashes:
			return 0

		session = new_session()
		try:
			async with session.begin():
				removed = await CollectedItem.objects.delete_hashes(
					session,
					source.id,
					hashes,
					tenant_id=source.tenant_id,
					analysis_fingerprint=getattr(self.analyzer, "current_fingerprint", None),
				)
		except Exception as e:  # noqa: BLE001 — leftovers are swept by retention
			logger.warning("collector_retirement_failed error_code=storage_operation_failed")
			return 0
		finally:
			await session.close()
		logger.info(f"Retired {removed} analysed raw item(s) for source {source.id}")
		return removed

	async def _expand_comments(
		self,
		content: list[dict],
		client: Any,
		context: Any,
		selection_scope: Any,
	) -> Any:
		"""Read comments for the posts a comment-enabled task may expand.

		The adapter comes from the per-source context, so a platform without
		comment support reports the warning the context already carries instead
		of attempting a call. Fetch failures are counted per post inside the
		expansion: the post batch is the deliverable, comments are an addition.
		"""
		from app.services.collection.comments import CommentBudget, expand_comments

		capabilities = getattr(context, "capabilities", None)
		build_adapter = getattr(context, "adapter", None)
		if capabilities is None or not getattr(capabilities, "comments", False) or not callable(build_adapter):
			return None
		adapter = build_adapter(client)
		if adapter is None or not hasattr(adapter, "fetch_comments"):
			return None
		intent = context.intent
		return await expand_comments(
			content,
			adapter.fetch_comments,
			budget=CommentBudget(intent.comments.max_per_post, intent.comments.total),
			scope=selection_scope,
		)

	async def _expand_candidates(
		self,
		content: list[dict],
		client: Any,
		context: Any,
		selection_scope: Any,
	) -> Any:
		"""Read the engagement lanes the per-source context asked for.

		Returns ``None`` when no requested lane is supported by the provider, so
		a platform without likes/reposts keeps the context's unsupported warning
		instead of an empty candidate batch.
		"""
		from app.services.collection.candidates import CandidateBudget, expand_candidates

		engagements = tuple(getattr(context, "engagement_types", ()) or ())
		capabilities = getattr(context, "capabilities", None)
		build_adapter = getattr(context, "adapter", None)
		if not engagements or capabilities is None or not callable(build_adapter):
			return None
		adapter = build_adapter(client)
		if adapter is None:
			return None
		lanes = []
		for engagement in engagements:
			if not capabilities.supports(engagement):
				continue
			fetch = getattr(adapter, f"fetch_{engagement}", None)
			if callable(fetch):
				lanes.append((engagement, fetch))
		if not lanes:
			return None
		return await expand_candidates(
			content,
			lanes,
			budget=CandidateBudget(context.intent.candidates.limit),
			scope=selection_scope,
		)

	def _profile_fetch(self, context: Any, client: Any) -> Any:
		"""The provider's profile fetch for a city-enabled context, if it has one.

		`None` means the platform cannot enrich profiles at all (its capability
		says so and the context already carries the warning), not that it failed.
		"""
		capabilities = getattr(context, "capabilities", None)
		build_adapter = getattr(context, "adapter", None)
		if capabilities is None or not getattr(capabilities, "profiles", False) or not callable(build_adapter):
			return None
		adapter = build_adapter(client)
		fetch = getattr(adapter, "fetch_profiles", None)
		return fetch if callable(fetch) else None

	async def _store_candidates(self, items: list, source: Source, task_id: int | None, context: Any) -> int:
		"""Persist discovered identities outside the collected-content path.

		A candidate is never a `collected_items` row: it is an identity awaiting
		an operator. `status` starts as `new`, or `confirmed` when the task
		allows automatic confirmation; the task-wide cap was already applied by
		the expansion. The caller owns the failure policy.
		"""
		from app.core.database import new_session
		from app.models import CollectionCandidate
		from app.services.collection.intent import CONFIRM_AUTO

		limit = getattr(context.intent, "candidates", None)
		status = "confirmed" if getattr(limit, "confirm", None) == CONFIRM_AUTO else "new"
		rows = []
		for item in items:
			evidence = dict(item.get("evidence") or {})
			evidence["task_id"] = task_id
			rows.append(
				{
					**item,
					"evidence": evidence,
					"status": status,
					"source_id": getattr(source, "id", None),
					"discovered_by": "collection",
				}
			)
		session = new_session()
		try:
			async with session.begin():
				stored = await CollectionCandidate.objects.store_candidates(session, rows)
		finally:
			await session.close()
		logger.info(f"Stored {stored} collection candidate(s) for source {source.id}")
		return stored

	async def collect_from_source(
			self,
			source: Source,
			content_type: str = "posts",
			analyze: bool = True,
			force_reanalyze: bool = False,
			run_id: Optional[int] = None,
			dry_run: bool = False,
			selection_scope: Any = None,
			task_id: int | None = None,
			collection_context: Any = None,
	) -> Optional[dict]:
		"""
		Collect content from a single source.

		Args:
			source: Source to collect from
			content_type: Type of content to collect (posts, comments, etc.)
			analyze: Whether to run AI analysis on collected content
			force_reanalyze: Bypass dedup and re-analyze everything (full-cycle refresh)
			run_id: The job this batch belongs to
			collection_context: Parsed task intent and provider capabilities for
				this source; metadata only until the comments/engagement lanes land.
			dry_run: Read-only rehearsal: fetch and report, but write no raw rows,
				call no model and do not advance `last_checked`

		Returns:
			Dict with collection results or None if failed
		"""
		try:
			from app.models import Platform

			if isinstance(source.source_type, str):
				source.source_type = SourceType.get_by_value(source.source_type)
				if not source.source_type:
					logger.error(f"Invalid source type: {source.source_type}")
					return None

			platform_id = source.platform_id
			try:
				platform_obj = source.platform
			except Exception:
				platform_obj = None

			if not platform_obj or not hasattr(platform_obj, 'params'):
				platform_obj = await Platform.objects.get(id=platform_id)

			client = get_social_client(platform_obj)

			# Collect data with date parameters preserved
			logger.info(
				f"Collecting {content_type} from source {source.id} ({source.name}) ... [{source.last_checked}]"
			)

			content = await client.collect_data(source, content_type)

			if not content:
				logger.warning(f"No content collected from source {source.id}")
				return None

			logger.info(f"Collected {len(content)} items from source {source.id}")

			# A comment-enabled task reads each eligible post's comments before
			# selection, because the thread decides together: a matching comment
			# keeps a post whose own text does not match. The expansion is capped
			# per post and in total and reports every cap it hit.
			comments = None
			if collection_context is not None and getattr(collection_context, "includes_comments", False):
				try:
					comments = await self._expand_comments(
						content, client, collection_context, selection_scope
					)
				except Exception:  # noqa: BLE001 — comments never fail the post batch
					logger.warning("collection_comments_failed error_code=comments_operation_failed")
					comments = None
				if comments is not None and comments.items:
					content = list(content) + list(comments.items)
					logger.info(
						f"Collected {len(comments.items)} comments for {comments.posts} post(s) "
						f"from source {source.id}"
					)

			# Engagement lanes answer "who reacted": they produce candidates, never
			# text rows, and share one task-wide cap. A candidate is stored outside
			# the collected-content path, so likes never inflate `content_count`.
			candidates = None
			if collection_context is not None and getattr(collection_context, "engagement_types", ()):
				try:
					candidates = await self._expand_candidates(
						content, client, collection_context, selection_scope
					)
				except Exception:  # noqa: BLE001 — candidates never fail the post batch
					logger.warning("collection_candidates_failed error_code=candidates_operation_failed")
					candidates = None

			# A city is not a post attribute: it is declared by the author's or
			# community's profile, so it arrives through the enrichment lane and
			# carries its source. Without the capability the gate is skipped with an
			# explicit reason instead of sending a whole batch to review.
			city_scope = _city_scope(collection_context)
			profile_fetch = self._profile_fetch(collection_context, client) if city_scope is not None else None
			profiles = None
			if city_scope is not None and profile_fetch is not None:
				try:
					from app.services.collection.profiles import enrich_posts

					profiles = await enrich_posts(content, profile_fetch)
				except Exception:  # noqa: BLE001 — enrichment never fails the post batch
					logger.warning("collection_profiles_failed error_code=profiles_operation_failed")
					profiles = None
				if candidates is not None and candidates.items:
					try:
						from app.services.collection.profiles import enrich_candidates

						await enrich_candidates(candidates.items, profile_fetch)
					except Exception:  # noqa: BLE001 — a candidate keeps an unknown city
						logger.warning("collection_candidate_profiles_failed error_code=profiles_operation_failed")

			candidates_stored = 0
			if candidates is not None and candidates.items and not dry_run:
				try:
					candidates_stored = await self._store_candidates(
						candidates.items, source, task_id, collection_context
					)
				except Exception:  # noqa: BLE001 — storage failure is reported, not raised
					logger.warning("collection_candidates_storage_failed error_code=storage_operation_failed")
			candidates_report = None
			if candidates is not None and collection_context is not None:
				confirm_mode = getattr(
					getattr(collection_context.intent, "candidates", None), "confirm", None
				)
				candidates_report = candidates.summary(
					stored=0 if dry_run else candidates_stored,
					confirm=confirm_mode,
					dry_run=dry_run,
				)
			profiles_report = _profiles_report(city_scope, profiles, profile_fetch is not None)


			# How many of these the platform handed us are actually new.
			#
			# `content_count` is the size of the API response, and the platform
			# does not remember what we already fetched: re-collecting an unchanged
			# wall returns the same N posts every single time. Reporting that N as
			# "records collected" made a run that found nothing new look identical
			# to a productive one — so the count of unseen items is reported
			# separately, reusing the dedup index rather than a second query of its
			# own.
			#
			# Computed before the analysis, because that is what decides whether the
			# analysis is paid for at all.
			new_count = await self._count_new_items(content, source)

			# The item hashes, whatever happens next.
			#
			# These are the run's own record of what the platform handed us, and
			# they go into `jobs.result` unconditionally. The analyser keeps its
			# own copy in `ai_analytics.summary_data["content_hashes"]` — but
			# only if it got as far as saving, so relying on it alone is exactly
			# how a broken analysis turns every re-collection into "32 new" for
			# good. Written here, the ledger survives a failed analysis.
			from app.services.ai.dedup import item_hash
			from app.services.monitoring.selection import SelectionScope, select_for_collection
			from app.services.monitoring.selection_store import (
				limit_reached as selection_limit_reached,
				record_collection_selection,
				source_limit,
			)

			hashes = [item_hash(item) for item in content]

			# Selection is a collection decision now. Apply it to the fetched
			# batch before staging; only selected/needs-review rows are stored.
			# Thread mode groups a post with its comments so a matching comment
			# can keep a non-matching parent.
			selection_result = select_for_collection(
				content,
				selection_scope if selection_scope is not None else SelectionScope(),
				thread=any(str(item.get("content_type") or "").lower() == "comment" for item in content),
				# The gate exists only when the provider could actually answer it.
				city=city_scope if profiles is not None else None,
			)
			selection_by_hash = {
				item_hash(decision.item): (decision.status, decision.reason)
				for decision in selection_result.decisions
				if isinstance(decision.item, dict)
			}
			storable = selection_result.selected + selection_result.needs_review
			effective_limit = source_limit(source)

			# A dry run answers "what would this source give us?" without
			# touching the write-ahead copy, spending a model call, or moving the
			# collection cursor: a rehearsal that advanced `last_checked` would
			# make the following real run skip exactly the window it rehearsed.
			if dry_run:
				# A rehearsal must answer the operator's real question: not just
				# "how many rows came back" but "how many of them would this
				# task actually analyse". Selection runs over the full fetched
				# batch; only its counters and a small sample are returned.
				selection_preview = {
					"eligible": len(selection_result.selected),
					"needs_review": len(selection_result.needs_review),
					"rejected": len(selection_result.rejected),
					"reasons": dict(selection_result.reasons),
					"unknown_tracked_users": list(selection_result.unknown_tracked_users),
					"city": _city_preview(selection_result, city_scope),
					"sample": _dry_run_preview(selection_result.selected, limit=3, text_limit=240),
				}
				return {
					"source_id": source.id,
					"content_count": len(content),
					"new_items": new_count,
					"analyzed": False,
					"analytics_count": 0,
					"staged": 0,
					"content_hashes": hashes,
					"dry_run": True,
					"preview": _dry_run_preview(content),
					"selection": selection_preview,
					"comments": comments.summary() if comments is not None else None,
					"candidates": candidates_report,
					"profiles": profiles_report,
					"collection": _collection_summary(collection_context),
				}

			# Write the raw batch first, then analyse it.
			#
			# Order matters and it is the whole point: the staged rows are a
			# write-ahead copy. While the LLM call runs (or fails, or the worker is
			# killed) the content is on disk, so a later `analyze` run can pick it
			# up. Rows are retired per the analyses that actually saved something,
			# which is why a failed analysis leaves the batch staged for a retry.
			staged = 0
			if storable:
				staged = await self._stage_items(
					content,
					source,
					run_id,
					durable=all(item.get("platform") == "telegram" for item in content),
					selection=selection_by_hash,
				)

			# Rejected text is not stored, but its identity/count/reason is:
			# `collection_seen` keeps dedupe stable and the lane run explains
			# why the batch was narrowed. Best-effort; raw staging is already safe.
			await record_collection_selection(
				source,
				content,
				selection_result,
				run_id=run_id,
				task_id=task_id,
				limit_count=effective_limit,
				limit_reached=selection_limit_reached(content, effective_limit),
			)

			analytics = None
			analysis_errors = None
			if analyze and selection_result.selected:
				# Inline analysis sees only collection-selected items; the later
				# analyze path reads the same statuses and does not re-filter.
				# This collector reuses its analyzer across sources/users. Report only
				# this call's measured delta, never old failures or coerced counters.
				errors_before = getattr(self.analyzer, "reported_errors", None)
				analytics = await self.analyzer.analyze_content(
					selection_result.selected, source, force_reanalyze=force_reanalyze
				)
				errors_after = getattr(self.analyzer, "reported_errors", None)
				if (
					type(errors_before) is int and errors_before >= 0
					and type(errors_after) is int and errors_after >= errors_before
				):
					analysis_errors = errors_after - errors_before
				await self._retire_staged(source, analytics)
			# No analysis this run: the staged rows are the deliverable, left for
			# the separate `analyze` step to drain.

			await Source.objects.update_last_checked(source.id)  # type: ignore[attr-defined]

			result = {
				"source_id": source.id,
				"content_count": len(content),
				"new_items": new_count,
				"analyzed": analyze,
				"analytics_count": len(analytics) if analytics else 0,
				# Rows written to `collected_items` this run. Still there if the
				# analysis failed or is deferred — that is the honest count of the
				# raw copy, not of what survived it.
				"staged": staged,
				"selected": len(selection_result.selected),
				"needs_review": len(selection_result.needs_review),
				"rejected": len(selection_result.rejected),
				"selection": {
					"eligible": len(selection_result.selected),
					"needs_review": len(selection_result.needs_review),
					"rejected": len(selection_result.rejected),
					"reasons": dict(selection_result.reasons),
					"unknown_tracked_users": list(selection_result.unknown_tracked_users),
					"city": _city_preview(selection_result, city_scope),
				},
				# This run's "seen" ledger, carried in the job result.
				"content_hashes": hashes,
				# Comment expansion evidence: parents read, comments fetched and
				# every cap the lane hit. `None` means the task did not ask for
				# comments or the platform cannot provide them.
				"comments": comments.summary() if comments is not None else None,
				# Engagement evidence: identities discovered per lane and how many
				# were actually stored. Candidates are never staged as text.
				"candidates": candidates_report,
				# Profile evidence: identities enriched, cities unknown, and the
				# reason the city gate could not run at all.
				"profiles": profiles_report,
				"collection": _collection_summary(collection_context),
			}
			if analysis_errors is not None:
				result["analysis_errors"] = analysis_errors
			return result

		except SourceRateLimited:
			# Deferred, not failed: no error notification or operator alert. The
			# caller records the source as rate limited for the next run.
			logger.warning("collection_source_deferred error_code=rate_limited")
			raise
		except Exception as e:
			logger.error("collection_source_failed error_code=collection_operation_failed")

			# Send critical notification if available
			if NOTIFICATIONS_AVAILABLE:
				try:
					# Operator collection may be unscoped; the source owns this notification.
					notification_tenant = getattr(source, "tenant_id", None)
					if notification_tenant is not None and (
						is_bypass() or current_tenant_id() == notification_tenant
					):
						with tenant_scope(notification_tenant):
							await notify.create(
								title=f"Ошибка сбора источника {source.name}",
								message="Не удалось собрать данные. Проверьте подключение и права доступа к источнику.",
								ntype=NotificationType.API_ERROR,
								entity_type="source",
								entity_id=source.id,
								send_to_messenger=False,
							)
					else:
						logger.warning("Collection notification skipped: source workspace unavailable or unauthorized")
				except Exception:
					logger.warning("Collection workspace notification failed")
				try:
					await messenger_service.send_operator_alert("collection_failed")
				except Exception:
					logger.warning("Collection operator alert failed")

			# Re-raise so the caller can tell a real failure (error) apart from a
			# legitimately empty result (no content). Swallowing here turns every
			# API/network failure into "no content".
			raise

	async def collect_from_platform(
			self,
			platform_id: int,
			source_types: Optional[list[SourceType]] = None,
			analyze: bool = True,
	) -> dict:
		"""
		Collect content from all active sources on a platform.

		Args:
			platform_id: Platform ID
			source_types: Optional list of source types to filter by
			analyze: Whether to run AI analysis

		Returns:
			Dict with collection statistics
		"""
		# Build query
		query = Source.objects.filter(platform_id=platform_id, is_active=True)

		if source_types:
			# Filter by source types using __in lookup
			query = query.filter(source_type__in=[st.name for st in source_types])

		sources = await query

		source_types_info = "all types"
		if source_types:
			source_types_info = ", ".join([st.name for st in source_types])

		logger.info(
			f"Collecting sources on platform {platform_id} for [{source_types_info}]"
		)

		results = {
			"total_sources": len(sources),
			"successful": 0,
			"failed": 0,
			"total_items": 0,
			"total_new_items": 0,
			"source_types": source_types_info
		}

		for source in sources:
			result = await self.collect_from_source(
				source,
				analyze=analyze,
			)
			if result:
				results["successful"] += 1
				results["total_items"] += result["content_count"]
				results["total_new_items"] += result.get("new_items", result["content_count"])
			else:
				results["failed"] += 1

		logger.info(f"Collection complete: {results}")
		return results

	async def _analyze_content(
			self,
			content: list[dict],
			source: Source,
			topic_chain_id: Optional[str] = None,
			parent_analysis_id: Optional[int] = None,
	):
		"""
		Run AI analysis on collected content in a single comprehensive pass.

		Args:
			content: List of normalized content items
			source: Source from which content was collected
			topic_chain_id: Optional chain ID for ongoing topics
			parent_analysis_id: Optional parent analysis ID for threaded analysis
		"""
		try:
			await self.analyzer.base_analyze_content(
				content,
				source,
				topic_chain_id=topic_chain_id,
				parent_analysis_id=parent_analysis_id
			)
		except Exception as e:
			logger.error("collection_analysis_failed error_code=analysis_operation_failed")
