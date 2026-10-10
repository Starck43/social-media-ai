import asyncio
import logging
from typing import Optional

from app.core.tenant_context import current_tenant_id, is_bypass, tenant_scope
from app.models import Platform, Source
from app.services.ai.analyzer import AIAnalyzer
from app.services.social.factory import get_social_client
from app.types import NotificationType, SourceType

# Try to import notification service
try:
	from app.services.notifications.messenger import messenger_service
	from app.services.notifications.service import notify

	NOTIFICATIONS_AVAILABLE = True
except ImportError:
	NOTIFICATIONS_AVAILABLE = False

logger = logging.getLogger(__name__)


def _build_permalink(source: Source, external_id: str | int | None) -> str | None:
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

		Returns the number of rows written. The run's hash ledger lives on the
		call's result, not here, so it outlives the rows themselves.
		"""
		from app.core.database import new_session
		from app.models import CollectedItem
		from app.services.ai.dedup import item_hash
		from app.utils.date_parsing import universal_date_parser

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

		session = new_session()
		try:
			async with session.begin():
				written = await CollectedItem.objects.store_items(session, rows)
		except Exception as e:  # noqa: BLE001 — staging must not fail a collection
			logger.warning(f"Could not stage items for source {source.id}: {e}")
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

		hashes = analysed_hashes(analytics)
		if not hashes:
			return 0

		session = new_session()
		try:
			async with session.begin():
				removed = await CollectedItem.objects.delete_hashes(session, source.id, hashes)
		except Exception as e:  # noqa: BLE001 — leftovers are swept by retention
			logger.warning(f"Could not retire staged rows for source {source.id}: {e}")
			return 0
		finally:
			await session.close()
		logger.info(f"Retired {removed} analysed raw item(s) for source {source.id}")
		return removed

	async def collect_from_source(
			self,
			source: Source,
			content_type: str = "posts",
			analyze: bool = True,
			force_reanalyze: bool = False,
			run_id: Optional[int] = None,
	) -> Optional[dict]:
		"""
		Collect content from a single source.

		Args:
			source: Source to collect from
			content_type: Type of content to collect (posts, comments, etc.)
			analyze: Whether to run AI analysis on collected content
			force_reanalyze: Bypass dedup and re-analyze everything (full-cycle refresh)

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

			hashes = [item_hash(item) for item in content]

			# Write the raw batch first, then analyse it.
			#
			# Order matters and it is the whole point: the staged rows are a
			# write-ahead copy. While the LLM call runs (or fails, or the worker is
			# killed) the content is on disk, so a later `analyze` run can pick it
			# up. Rows are retired per the analyses that actually saved something,
			# which is why a failed analysis leaves the batch staged for a retry.
			staged = await self._stage_items(content, source, run_id)

			analytics = None
			analysis_errors = None
			if analyze and content:
				# This collector reuses its analyzer across sources/users. Report only
				# this call's measured delta, never old failures or coerced counters.
				errors_before = getattr(self.analyzer, "reported_errors", None)
				analytics = await self.analyzer.analyze_content(
					content, source, force_reanalyze=force_reanalyze
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
				# This run's "seen" ledger, carried in the job result.
				"content_hashes": hashes,
			}
			if analysis_errors is not None:
				result["analysis_errors"] = analysis_errors
			return result

		except Exception as e:
			logger.error(f"Error collecting from source {source.id}: {e}", exc_info=True)

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

	async def collect_monitored_users(self, source: Source, analyze: bool = True, monitored_users: list = None, force_reanalyze: bool = False, run_id: Optional[int] = None) -> dict:
		"""Collect each requested user without losing earlier successful totals.

		A missing source or raised collection error is a failed request. None from
		a resolved USER follows collect_from_source's normal no-content path,
		not a failure. Authentication failures are a subset of failed requests.
		No raw exception or authorization hint is added to the returned counters.
		Only positive reported inline diagnostics are aggregated; absence is not zero.
		This does not measure complete coverage or change retry.
		"""
		from app.services.social.credentials import AuthorizationRequired

		if monitored_users is None:
			monitored_usernames = source.params.get("monitored_users", []) or []
		else:
			monitored_usernames = monitored_users
		results = {
			"total_users": len(monitored_usernames),
			"successful": 0,
			"failed": 0,
			"empty": 0,
			"auth_required": 0,
			"total_items": 0,
			"total_new_items": 0,
		}
		for username in monitored_usernames:
			try:
				user_source = await Source.objects.filter(
					platform_id=source.platform_id,
					external_id=username.lstrip("@"),
					source_type=SourceType.USER.name,
				).first()
				if user_source is None:
					results["failed"] += 1
					continue
				result = await self.collect_from_source(
					user_source, analyze=analyze, force_reanalyze=force_reanalyze, run_id=run_id
				)
				if result is None:
					results["empty"] += 1
					continue
				if not isinstance(result, dict):
					raise ValueError("Invalid monitored collection result")
				items = result.get("content_count")
				# Preserve the existing legacy fallback; this does not certify that
				# older clients measured new items separately.
				new_items = result.get("new_items", items)
				if any(type(value) is not int or value < 0 for value in (items, new_items)):
					raise ValueError("Invalid monitored collection counters")
				if items == 0:
					results["empty"] += 1
					continue
				# Compute before accumulating, so malformed totals cannot partially
				# update the counters for this request and then count it as failed.
				total_items = results["total_items"] + items
				total_new_items = results["total_new_items"] + new_items
				results["total_items"] = total_items
				results["total_new_items"] = total_new_items
				results["successful"] += 1
				# Reported analysis failures coexist with successful collection.
				# Missing/legacy diagnostics never certify a measured zero total.
				analysis_errors = result.get("analysis_errors")
				if type(analysis_errors) is int and analysis_errors > 0:
					results["analysis_errors"] = results.get("analysis_errors", 0) + analysis_errors
			except Exception as error:
				results["failed"] += 1
				if isinstance(error, AuthorizationRequired):
					results["auth_required"] += 1
				logger.warning(
					"monitored_collection_failed source_id=%s error_code=user_collection_failed",
					source.id if type(source.id) is int and source.id > 0 else None,
				)
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
			logger.error(f"Error analyzing content: {e}", exc_info=True)
