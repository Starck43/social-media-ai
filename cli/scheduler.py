import asyncio
import logging
import sys
from datetime import date, datetime
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.models import AIAnalytics, Source, Platform
from app.services.ai import AIAnalyzer
from app.services.checkpoint_manager import CheckpointManager
from app.services.monitoring import ContentCollector

logging.basicConfig(
	level=logging.INFO,
	format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
	handlers=[
		logging.StreamHandler(sys.stdout),
		logging.FileHandler(f'scheduler.log')
	]
)

# Установите уровень логирования для ваших модулей
logging.getLogger('app.services.monitoring.collector').setLevel(logging.INFO)
logging.getLogger('app.services.ai.analyzer').setLevel(logging.INFO)
logging.getLogger('app.services.social').setLevel(logging.INFO)

logger = logging.getLogger(__name__)

# Initialize Rich console
console = Console()


# Rich CLI Analyzer for beautiful console output
class RichCLIAnalyzer(AIAnalyzer):
	"""
	AIAnalyzer with Rich console output for CLI.
	Only overrides specific methods for UI enhancement.
	"""

	def __init__(self):
		"""Initialize with Rich console."""
		super().__init__()

	async def _analyze_content_by_themes(self, content: list[dict], source: Source) -> list[AIAnalytics]:
		"""
		Analyze content by themes with CLI progress display.
		"""
		if not content:
			console.print(f"[yellow]⚠️ No content to analyze for source {source.id}[/yellow]")
			return []

		console.print(f"[dim]🎯 Theme-based analysis with automatic linking[/dim]")

		# Call parent theme analysis
		analytics_list = await super()._analyze_content_by_themes(content, source)

		if analytics_list:
			console.print(f"\n[bold green]✅ Theme analysis completed[/bold green]")
			# Show theme linking info if available
			if len(analytics_list) == 1 and analytics_list[0].topic_chain_id:
				console.print(f"[dim]🔗 Linked to theme chain: {analytics_list[0].topic_chain_id}[/dim]")

		return analytics_list

	async def _analyze_content_by_days(self, content: list[dict], source: Source, **kwargs) -> list[AIAnalytics]:
		"""
		Analyze content by days with CLI progress display.
		"""
		if not content:
			console.print(f"[yellow]⚠️ No content to analyze for source {source.id}[/yellow]")
			return []

		console.print(f"[dim]📅 Event-based analysis: grouping by days[/dim]")

		# Call parent analysis
		analytics_list = await super()._analyze_content_by_days(content, source)

		if analytics_list:
			console.print(f"\n[bold green]✅ Analysis completed successfully[/bold green]")
			console.print(f"[dim]📊 Created {len(analytics_list)} analytics records (one per day)[/dim]")

			# Show details for each day
			for analysis in analytics_list[:3]:  # Show first 3 days
				console.print(f"[dim]  - {analysis.analysis_date}: ID={analysis.id}[/dim]")
			if len(analytics_list) > 3:
				console.print(f"[dim]  ... and {len(analytics_list) - 3} more[/dim]")
		else:
			console.print(f"\n[bold red]❌ Analysis failed[/bold red]")

		return analytics_list


async def run_collection_with_cli(
		source_id: Optional[int] = None,
		source_url: Optional[str] = None,
		platform_id: Optional[int] = None,
		start_date: Optional[str] = None,
		end_date: Optional[str] = None,
		force_refresh: bool = False,
		show_details: bool = True
) -> dict:
	"""
	Run collection with CLI output.
	"""

	console.print()
	console.print(Panel.fit(
		"[bold cyan]🚀 STARTING CONTENT COLLECTION[/bold cyan]",
		border_style="cyan"
	))

	# Parse dates from DD-MM-YYYY format
	parsed_start_date: date | None = None
	parsed_end_date: date | None = None

	if start_date:
		try:
			parsed_start_date = datetime.strptime(start_date, '%d-%m-%Y').date()
		except ValueError:
			console.print(f"[red]❌ Invalid start date: {start_date}. Use DD-MM-YYYY[/red]")
			return {'error': 'Invalid start date format'}

	if end_date:
		try:
			parsed_end_date = datetime.strptime(end_date, '%d-%m-%Y').date()
		except ValueError:
			console.print(f"[red]❌ Invalid end date: {end_date}. Use DD-MM-YYYY[/red]")
			return {'error': 'Invalid end date format'}

	collector = ContentCollector()

	try:
		# Force refresh: delete old analytics and reset last_checked
		if force_refresh:
			try:
				deleted_count = await delete_analytics_and_reset(
					source_id=source_id,
					platform_id=platform_id,
					source_url=source_url,
					start_date=parsed_start_date,
					end_date=parsed_end_date
				)
				console.print(f"[yellow]🔄 Force refresh: deleted {deleted_count} analytics records[/yellow]")
			except Exception as e:
				console.print(f"[red]❌ Error during force refresh: {e}[/red]")
				return {'error': str(e)}

		# Single source collection
		if source_id or source_url:
			try:
				if source_url:
					external_id = source_url.rstrip('/').split('/')[-1]
					source, platform_data = await Source.objects.get_source_with_platform(external_id=external_id)
				else:
					source, platform_data = await Source.objects.get_source_with_platform(source_id=source_id)

				# Now you have both source and platform_data available
				console.print(f"[green]Found source: {source.name} (Platform: {platform_data['name']})[/green]")

			except ValueError as e:
				error_msg = f"Source not found: {e}"
				console.print(f"[red]Error: {error_msg}[/red]")
				return {'error': error_msg}

			console.print(f"[dim]🎯 Source: {source.name} ({source.platform.name})[/dim]")

			if show_details:
				# Show analysis type from bot_scenario
				analyze_type = source.bot_scenario.analyze_type if source.bot_scenario else "themes"
				console.print(
					f"[dim]🎚️ Scenario: {source.bot_scenario.name if source.bot_scenario else 'None'} "
					f"(analyze by: {analyze_type})[/dim]"
				)

			console.print(f"[dim]📊 Last checked: {source.last_checked}[/dim]")

			# Apply date parameters for API collection
			if parsed_start_date or parsed_end_date:
				if not source.params:
					source.params = {}

				source.params['cli_dates'] = {
					'start_date': parsed_start_date,
					'end_date': parsed_end_date
				}
				source.params['force_refresh'] = force_refresh

				if show_details:
					console.print(f"[dim]📅 Date range: {parsed_start_date} to {parsed_end_date or 'today'}[/dim]")

			# Single collection call - API handles pagination and date range
			source = await CheckpointManager.prepare_for_full_collection(source)
			result = await collector.collect_from_source(source)

			if result and show_details:
				console.print(f"[dim]✅ Collected {result['content_count']} items[/dim]")
				if result.get('analytics_count'):
					console.print(f"[dim]📈 Created {result['analytics_count']} analytics records[/dim]")

			return process_single_result(result, source.name)

		# Platform collection
		elif platform_id:
			if show_details:
				platform = await Platform.objects.get(id=platform_id)
				platform_name = platform.name if platform else f"ID:{platform_id}"
				console.print(f"[dim]🏢 Platform: {platform_name}[/dim]")

			stats = await collector.collect_from_platform(platform_id=platform_id)
			return stats

		# Full collection (all platforms)
		else:
			console.print(f"[dim]🌍 Collecting from ALL platforms[/dim]")

			platforms = await Platform.objects.filter(is_active=True)
			total_stats = {"platforms": 0, "sources": 0, "items": 0}

			for platform in platforms:
				if show_details:
					console.print(f"[dim]🔄 Processing {platform.name}...[/dim]")

				platform_stats = await collector.collect_from_platform(platform_id=platform.id)
				total_stats["platforms"] += 1
				total_stats["sources"] += platform_stats.get("successful", 0)
				total_stats["items"] += platform_stats.get("total_items", 0)

			return total_stats

	except Exception as e:
		console.print(f"[red]❌ Collection failed: {e}[/red]")
		return {'error': str(e)}


def process_single_result(result: Optional[dict], source_name: str) -> dict:
	"""
	Process single source collection result for consistent output.
	"""

	if result:
		return {
			'total_sources': 1,
			'successful': 1,
			'failed': 0,
			'total_items': result.get('content_count', 0),
			'analytics_count': result.get('analytics_count', 0),
			'source_name': source_name
		}
	else:
		return {
			'total_sources': 1,
			'successful': 0,
			'failed': 1,
			'total_items': 0,
			'analytics_count': 0,
			'source_name': source_name
		}


async def delete_analytics_and_reset(
		source_id: Optional[int] = None,
		platform_id: Optional[int] = None,
		source_url: Optional[str] = None,
		start_date: Optional[date] = None,
		end_date: Optional[date] = None
) -> int:
	"""
	Delete analytics records and reset last_checked for force refresh.
	"""

	if source_id:
		source = await Source.objects.get(id=source_id)
		sources = [source] if source else []
	elif source_url:
		external_id = source_url.rstrip('/').split('/')[-1]
		sources = await Source.objects.filter(external_id=external_id, is_active=True)
	elif platform_id:
		sources = await Source.objects.filter(platform_id=platform_id, is_active=True)
	else:
		sources = await Source.objects.filter(is_active=True)

	deleted_count = 0

	for source in sources:
		if source:
			# Build filters with DATE objects
			filters = {"source_id": source.id}
			if start_date:
				filters["analysis_date__gte"] = start_date
			if end_date:
				filters["analysis_date__lte"] = end_date

			# Get and count analytics to delete
			analytics_to_delete = await AIAnalytics.objects.filter(**filters)
			deleted_count += len(analytics_to_delete)

			# Delete analytics
			if analytics_to_delete:
				analytics_ids = [a.id for a in analytics_to_delete]
				await AIAnalytics.objects.filter(id__in=analytics_ids).delete()

			await Source.objects.update_by_id(source.id, last_checked=start_date)

	return deleted_count


def display_collection_stats(stats: dict):
	"""Display collection statistics in beautiful format."""
	console.print()
	console.print(Panel.fit(
		"[bold green]📊 COLLECTION COMPLETE[/bold green]",
		title="[bold]Results[/bold]",
		border_style="green"
	))

	table = Table(show_header=True, header_style="bold blue")
	table.add_column("Metric", style="cyan", width=20)
	table.add_column("Value", style="white", width=15)

	if 'platforms' in stats:
		table.add_row("Platforms", str(stats["platforms"]))
	if 'sources' in stats:
		table.add_row("Sources", str(stats["sources"]))
	if 'total_sources' in stats:
		table.add_row("Total Sources", str(stats["total_sources"]))
		table.add_row("Successful", str(stats["successful"]))
		table.add_row("Failed", str(stats["failed"]))
	if 'items' in stats:
		table.add_row("Items Collected", str(stats["items"]))
	if 'total_items' in stats:
		table.add_row("Items Collected", str(stats["total_items"]))
	if 'source_name' in stats:
		table.add_row("Source", stats['source_name'])

	console.print(table)


@click.group()
def scheduler_cli():
	"""Content collection scheduler commands."""
	pass


@scheduler_cli.command("run")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed collection process")
@click.option("--source-id", type=int, help="Collect from specific source ID")
@click.option("--source-url", help="Collect from specific source by URL")
@click.option("--platform-id", type=int, help="Collect from specific platform ID")
@click.option("--start-date", help="Start date for collection (DD-MM-YYYY)")
@click.option("--end-date", help="End date for collection (DD-MM-YYYY)")
@click.option("--force-refresh", is_flag=True, help="Delete old analytics and reset last_checked")
def run_scheduler(
		verbose: bool,
		source_id: int,
		source_url: str,
		platform_id: int,
		start_date: str,
		end_date: str,
		force_refresh: bool
):
	"""
	Run content collection (for debugging and manual runs).

	Examples:
		python -m cli.scheduler run --verbose                                   # Test all with details
		python -m cli.scheduler run --source-id 1 --verbose                     # Test source with details
		python -m cli.scheduler run --source-id 1 --force-refresh               # Re-analyze source
		python -m cli.scheduler run --force-refresh --start-date 2024-01-01     # Full re-analysis
	"""

	stats = asyncio.run(run_collection_with_cli(
		source_id=source_id,
		source_url=source_url,
		platform_id=platform_id,
		start_date=start_date,
		end_date=end_date,
		force_refresh=force_refresh,
		show_details=verbose
	))

	# Display summary
	display_collection_stats(stats)


if __name__ == "__main__":
	scheduler_cli()
