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
			return await admit_telegram_items(content, source, run_id=run_id)
		rows = build_staged_rows(content, source, run_id)

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

	async def collect_from_source(
			self,
			source: Source,
			content_type: str = "posts",
			analyze: bool = True,
			force_reanalyze: bool = False,
			run_id: Optional[int] = None,
			dry_run: bool = False,
			selection_scope: Any = None,
	) -> Optional[dict]:
		"""
		Collect content from a single source.

		Args:
			source: Source to collect from
			content_type: Type of content to collect (posts, comments, etc.)
			analyze: Whether to run AI analysis on collected content
			force_reanalyze: Bypass dedup and re-analyze everything (full-cycle refresh)
			run_id: The job this batch belongs to
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

			# A dry run answers "what would this source give us?" without
			# touching the write-ahead copy, spending a model call, or moving the
			# collection cursor: a rehearsal that advanced `last_checked` would
			# make the following real run skip exactly the window it rehearsed.
			if dry_run:
				# A rehearsal must answer the operator's real question: not just
				# "how many rows came back" but "how many of them would this
				# task actually analyse". The deterministic selection runs over
				# the full fetched batch (the preview below is truncated), and
				# only its counters + a small sample are returned.
				selection_preview = None
				if selection_scope is not None:
					from app.services.monitoring.selection import select_items

					selected = select_items(content, selection_scope)
					selection_preview = {
						"eligible": len(selected.eligible),
						"rejected": len(selected.rejected),
						"reasons": dict(selected.reasons),
						"sample": _dry_run_preview(selected.eligible, limit=3, text_limit=240),
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
				}

			# Write the raw batch first, then analyse it.
			#
			# Order matters and it is the whole point: the staged rows are a
			# write-ahead copy. While the LLM call runs (or fails, or the worker is
			# killed) the content is on disk, so a later `analyze` run can pick it
			# up. Rows are retired per the analyses that actually saved something,
			# which is why a failed analysis leaves the batch staged for a retry.
			staged = await self._stage_items(
				content, source, run_id, durable=all(item.get("platform") == "telegram" for item in content)
			)

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
