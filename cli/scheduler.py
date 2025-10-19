import asyncio

import click
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from datetime import datetime, timezone, timedelta, date
from typing import Optional, Dict, Any, List, Union
from dateutil.parser import parse as parse_date

from app.models import Source
from app.services.ai import AIAnalyzer

# Initialize Rich console
console = Console()


class CLIContentScheduler:
	"""
{{ ... }}
	CLI version of ContentScheduler with rich output.
	"""

	def __init__(self):
		"""Initialize scheduler with CLI analyzer."""
		self.analyzer = AIAnalyzer()
		self.optimizer = None  # We'll keep the original optimizer logic

	async def run_collection_cycle(
			self,
			source_id: Optional[int] = None,
			source_url: Optional[str] = None,
			start_date: Optional[Union[str, date]] = None,
			end_date: Optional[Union[str, date]] = None,
			force_refresh: bool = False
	) -> dict:
		"""
		Run one collection cycle with rich CLI output.
		
		Args:
			source_id: Optional source ID to collect from (deprecated, use source_url instead)
			source_url: Optional source URL (platform_url) to collect from. If None, collects from all active sources.
			start_date: Optional start date for collection (YYYY-MM-DD or date object).
			end_date: Optional end date for collection (YYYY-MM-DD or date object).
			force_refresh: If True, reset last_checked to force re-analysis of all data
		"""
		# Parse date strings if provided
		if isinstance(start_date, str):
			try:
				start_date = parse_date(start_date).date()
			except (ValueError, TypeError) as e:
				console.print(f"[red]Invalid start date format: {e}. Use YYYY-MM-DD[/red]")
				start_date = None

		if isinstance(end_date, str):
			try:
				end_date = parse_date(end_date).date()
			except (ValueError, TypeError) as e:
				console.print(f"[red]Invalid end date format: {e}. Use YYYY-MM-DD[/red]")
				end_date = None

		# Validate date range
		if start_date and end_date and start_date > end_date:
			console.print("[yellow]Warning: Start date is after end date, swapping values[/yellow]")
			start_date, end_date = end_date, start_date
		console.print()
		console.print(Panel.fit(
			"[bold cyan]🚀 STARTING COLLECTION CYCLE[/bold cyan]",
			title="[bold]Content Collection[/bold]",
			border_style="cyan"
		))

		# Import here to avoid circular imports
		from app.services.scheduler import ContentScheduler

		# Create original scheduler to reuse collection logic
		original_scheduler = ContentScheduler()

		# Get all active sources
		try:
			query = Source.objects.select_related('platform', 'bot_scenario')

			# Find source by URL or ID
			if source_url:
				# Parse URL to extract external_id
				external_id = source_url.rstrip('/').split('/')[-1]
				console.print(f"[dim]Looking for source with external_id: {external_id}[/dim]")
				sources = await query.filter(external_id=external_id, is_active=True)
				if not sources:
					console.print(f"[red]Error: Active source with URL {source_url} (external_id: {external_id}) not found[/red]")
					return {
						'collected': 0, 'skipped': 0, 'failed': 1,
						'total_sources': 0, 'total_content': 0, 'total_cost': 0.0,
						'start_date': start_date, 'end_date': end_date
					}
			elif source_id:
				# Legacy support for source_id
				sources = [await query.get(id=source_id, is_active=True)]
				if not sources[0]:
					console.print(f"[red]Error: Active source with ID {source_id} not found[/red]")
					return {
						'collected': 0, 'skipped': 0, 'failed': 1,
						'total_sources': 0, 'total_content': 0, 'total_cost': 0.0,
						'start_date': start_date, 'end_date': end_date
					}
			else:
				sources = await query.filter(is_active=True)

			# Reset last_checked if force_refresh is enabled
			if force_refresh and sources:
				console.print(f"[yellow]🔄 Force refresh enabled - resetting last_checked for {len(sources)} source(s)[/yellow]")
				for source in sources:
					await Source.objects.update_by_id(source.id, last_checked=None)

			# Store date range in stats
			stats = {
				'collected': 0,
				'skipped': 0,
				'failed': 0,
				'total_sources': len(sources),
				'total_content': 0,
				'total_cost': 0.0,
				'start_date': start_date,
				'end_date': end_date
			}
			total_sources = len(sources)
			console.print(f"[dim]📋 Found {total_sources} active sources[/dim]")

			if total_sources == 0:
				console.print("[yellow]⚠️  No active sources found[/yellow]")
				return {
					"total_sources": 0,
					"collected": 0,
					"skipped": 0,
					"failed": 0,
					"total_content": 0,
					"total_cost": 0.0,
				}

			stats = {
				"total_sources": total_sources,
				"collected": 0,
				"skipped": 0,
				"failed": 0,
				"total_content": 0,
				"total_cost": 0.0,
			}

			# Process each source with CLI feedback
			with console.status("[bold green]Processing sources...", spinner="dots") as status:
				for i, source in enumerate(sources, 1):
					status.update(f"[bold green]Processing source {i}/{total_sources}: {source.name}")

					try:
						# Use original collection logic but with CLI analyzer
						result = await self._collect_source_with_display(source)

						if result:
							stats["collected"] += 1
							stats["total_content"] += result.get("content_count", 0)
						else:
							stats["skipped"] += 1

					except Exception as e:
						console.print(f"[red]❌ Failed to collect source {source.id}: {e}[/red]")
						stats["failed"] += 1

			# Display final statistics
			self._display_collection_stats(stats)

			return stats

		except Exception as e:
			console.print(f"[red]❌ Collection cycle failed: {e}[/red]")
			return {
				"total_sources": 0,
				"collected": 0,
				"skipped": 0,
				"failed": 0,
				"total_content": 0,
				"total_cost": 0.0,
				"error": str(e)
			}

	async def _collect_source_with_display(self, source: Source) -> dict | None:
		"""
		Collect and analyze content from single source with CLI display.
		"""
		console.print(f"\n[dim]🔍 Processing source: {source.name}[/dim]")

		# Import here to avoid circular imports
		from app.services.checkpoint_manager import CheckpointManager, CollectionResult
		from app.services.social.factory import get_social_client

		# Check if collection needed (via checkpoint)
		if not CheckpointManager.should_collect(source):
			console.print(f"[dim]⏭️  Source {source.id} collected recently, skipping[/dim]")
			return None

		console.print(f"[dim]📥 Source {source.id} needs collection[/dim]")

		try:
			# Get platform client
			client = get_social_client(source.platform)
			if not client:
				console.print(f"[red]❌ No client for platform {source.platform.name}[/red]")
				return None

			# Get checkpoint for incremental collection
			checkpoint = await CheckpointManager.get_checkpoint(source)

			# Collect new content
			content = await self._collect_platform_content(
				client=client,
				source=source,
				checkpoint=checkpoint
			)

			if not content:
				console.print(f"[dim]📭 No new content for source {source.id}[/dim]")
				# Update checkpoint anyway
				result = CollectionResult(
					source_id=source.id,
					content_count=0,
					has_new_content=False
				)
				await result.save_checkpoint()
				return None

			console.print(f"[green]✅ Collected {len(content)} items from source {source.id}[/green]")

			# Analyze with LLM (with CLI display)
			console.print(f"\n[bold yellow]🔄 Starting AI Analysis for source: {source.name}[/bold yellow]")
			console.print(f"[dim]📝 Analyzing {len(content)} content items[/dim]")

			# Show sample prompt for text analysis (first 200 chars)
			if content and any(item.get('text') for item in content):
				text_items = [item for item in content if item.get('text')]
				if text_items:
					from app.services.ai.content_classifier import ContentClassifier
					text_content = ContentClassifier.prepare_text_content(text_items[:3])  # Show first 3 items
					console.print(f"\n[dim]💭 Sample content being analyzed:[/dim]")
					console.print(f"[dim]{text_content[:200]}{'...' if len(text_content) > 200 else ''}[/dim]")

			# Check if we should use day-by-day analysis
			bot_scenario = source.bot_scenario if hasattr(source, 'bot_scenario') else None
			is_event_based = False
			if bot_scenario and bot_scenario.scope:
				is_event_based = bot_scenario.scope.get('event_based', False)
			
			if is_event_based:
				console.print(f"[dim]📅 Event-based analysis: grouping by days[/dim]")
				analytics_list = await self.analyzer.analyze_content_by_days(
					content=content,
					source=source,
				)
				
				if analytics_list:
					console.print(f"\n[bold green]✅ Analysis completed successfully[/bold green]")
					console.print(f"[dim]📊 Created {len(analytics_list)} analytics records (one per day)[/dim]")
					
					# Show details for each day
					for analysis in analytics_list[:3]:  # Show first 3 days
						console.print(f"[dim]  - {analysis.analysis_date}: ID={analysis.id}[/dim]")
					if len(analytics_list) > 3:
						console.print(f"[dim]  ... and {len(analytics_list) - 3} more[/dim]")
					
					# Use first analysis for detailed display
					analysis = analytics_list[0] if analytics_list else None
				else:
					console.print(f"\n[bold red]❌ Analysis failed[/bold red]")
					return None
			else:
				# Regular aggregated analysis
				analysis = await self.analyzer.analyze_content(
					content=content,
					source=source,
					topic_chain_id=f"source_{source.id}_chain",  # Generate chain ID for this source
				)
				
				if analysis:
					console.print(f"\n[bold green]✅ Analysis completed successfully[/bold green]")
					console.print(f"[dim]📊 Analysis ID: {analysis.id}[/dim]")
				else:
					console.print(f"\n[bold red]❌ Analysis failed[/bold red]")
					return None

			# Show detailed analysis results if available
			if analysis and hasattr(analysis, 'summary_data') and analysis.summary_data:
				# Convert JSON to dict if needed
				summary_dict = analysis.summary_data
				if hasattr(summary_dict, 'items'):  # Check if it's already a dict-like object
					pass  # It's already a dict
				else:
					# Try to convert from JSON string or other format
					try:
						import json
						if isinstance(summary_dict, str):
							summary_dict = json.loads(summary_dict)
						else:
							summary_dict = dict(summary_dict) if summary_dict else {}
					except:
						summary_dict = {}

				self._display_analysis_details(summary_dict, source.name)

			# Save checkpoint on success
			result = CollectionResult(
				source_id=source.id,
				content_count=len(content),
				has_new_content=True
			)
			await result.save_checkpoint()

			console.print(f"[green]✅ Successfully processed source {source.id}[/green]")

			return {
				"source_id": source.id,
				"content_count": len(content),
				"analysis_id": analysis.id,
			}

		except Exception as e:
			console.print(f"[red]❌ Failed to collect source {source.id}: {e}[/red]")
			return None

	async def _collect_platform_content(
			self,
			client,
			source: Source,
			checkpoint: dict
	) -> list[dict]:
		"""
		Collect content from platform using checkpoint.
		"""
		last_checked = checkpoint.get("last_checked")

		original_params = None
		try:
			original_params = source.params.copy() if source.params else {}
			if not source.params:
				source.params = {}
			if 'collection' not in source.params:
				source.params['collection'] = {}

			# Merge checkpoint params and since timestamp
			cp_params = checkpoint.get('params', {}) or {}
			source.params['collection'].update(cp_params)
			if last_checked:
				source.params['collection']['since'] = last_checked

			# Let the concrete client handle request building and normalization
			content = await client.collect_data(source=source, content_type="posts")
			return content or []

		except Exception as e:
			console.print(f"[red]❌ Platform collection failed: {e}[/red]")
			return []
		finally:
			# Restore original params to avoid side effects
			if original_params is not None:
				source.params = original_params

	def _display_collection_stats(self, stats: dict):
		"""Display collection statistics in a beautiful format."""
		console.print()
		console.print(Panel.fit(
			"[bold green]📊 COLLECTION CYCLE COMPLETE[/bold green]",
			title="[bold]Final Statistics[/bold]",
			border_style="green"
		))

		# Create statistics table
		table = Table(show_header=True, header_style="bold blue")
		table.add_column("Metric", style="cyan", width=20)
		table.add_column("Value", style="white", width=15)

		table.add_row("Total Sources", str(stats["total_sources"]))
		table.add_row("Collected", str(stats["collected"]))
		table.add_row("Skipped", str(stats["skipped"]))
		table.add_row("Failed", str(stats["failed"]))
		table.add_row("Total Content", str(stats["total_content"]))
		table.add_row("Total Cost", f"${stats['total_cost']:.4f}")

		console.print(table)

		# Show success rate
		if stats["total_sources"] > 0:
			success_rate = (stats["collected"] / stats["total_sources"]) * 100
			console.print(f"\n[bold]Success Rate: {success_rate:.1f}%[/bold]")

	def _display_analysis_details(self, summary_data: dict, source_name: str):
		"""Display detailed analysis results in a beautiful format."""
		console.print()
		console.print(Panel.fit(
			f"[bold green]📊 DETAILED ANALYSIS RESULTS[/bold green]\n"
			f"[dim]Source: {source_name}[/dim]",
			title="[bold]AI Analysis Summary[/bold]",
			border_style="green"
		))

		# Create summary table
		table = Table(title="Analysis Summary", show_header=True, header_style="bold magenta")
		table.add_column("Metric", style="cyan", width=25)
		table.add_column("Value", style="white", width=50)

		# Extract and display key metrics from summary_data
		multi_llm = summary_data.get('multi_llm_analysis', {})
		content_stats = summary_data.get('content_statistics', {})
		source_meta = summary_data.get('source_metadata', {})
		analysis_meta = summary_data.get('analysis_metadata', {})

		# Content statistics
		if content_stats:
			table.add_row("Total Posts", str(content_stats.get('total_posts', 0)))
			avg_text_length = content_stats.get('avg_text_length', 0)
			if isinstance(avg_text_length, (int, float)):
				table.add_row("Avg Text Length", f"{avg_text_length:.1f} chars")
			else:
				table.add_row("Avg Text Length", f"{avg_text_length} chars")
			table.add_row("Total Reactions", str(content_stats.get('total_reactions', 0)))
			table.add_row("Total Comments", str(content_stats.get('total_comments', 0)))

		# Source info
		if source_meta:
			table.add_row("Platform", source_meta.get('platform', 'Unknown'))
			table.add_row("Source Type", source_meta.get('source_type', 'Unknown'))

		# Analysis metadata
		if analysis_meta:
			table.add_row("LLM Providers", str(analysis_meta.get('llm_providers_used', 0)))
			table.add_row("Content Samples", str(analysis_meta.get('content_samples_analyzed', 0)))

		# Text analysis results
		text_analysis = multi_llm.get('text_analysis', {})
		if text_analysis:
			if 'main_topics' in text_analysis:
				topics = text_analysis['main_topics']
				if isinstance(topics, list) and topics:
					table.add_row("Main Topics", f"{len(topics)} found")
					for i, topic in enumerate(topics[:3]):
						table.add_row(f"  Topic {i + 1}", str(topic))
				else:
					table.add_row("Main Topics", str(topics))

			if 'sentiment_score' in text_analysis:
				score = text_analysis['sentiment_score']
				try:
					score_num = float(score) if score is not None else 0.0
					sentiment = "Positive" if score_num > 0.6 else "Negative" if score_num < 0.4 else "Neutral"
					table.add_row("Sentiment", f"{sentiment} ({score})")
				except (ValueError, TypeError):
					table.add_row("Sentiment", f"Unknown ({score})")

			if 'overall_mood' in text_analysis:
				table.add_row("Overall Mood", str(text_analysis['overall_mood']))

		console.print(table)

		# Show scenario info if available
		scenario_meta = summary_data.get('scenario_metadata')
		if scenario_meta:
			console.print(f"\n[bold]🎯 Bot Scenario:[/bold] {scenario_meta.get('scenario_name', 'Unknown')}")
			console.print(f"[dim]Analysis Types: {', '.join(scenario_meta.get('analysis_types', []))}[/dim]")


# CLI Scheduler instance
cli_scheduler = CLIContentScheduler()


def display_prompt(prompt: str, media_type: str, source_name: str):
	"""Display the prompt that will be sent to LLM in a beautiful format."""
	console.print()
	console.print(Panel.fit(
		f"[bold blue]🔍 PROMPT FOR {media_type.upper()} ANALYSIS[/bold blue]\n"
		f"[dim]Source: {source_name}[/dim]",
		title="[bold]AI Analysis Prompt[/bold]",
		border_style="blue"
	))

	# Show prompt in a syntax-highlighted box
	console.print(Syntax(
		prompt,
		"markdown",
		theme="monokai",
		word_wrap=True,
		padding=(1, 2)
	))


def display_analysis_results(results: dict, source_name: str):
	"""Display analysis results in a beautiful summary format."""
	console.print()
	console.print(Panel.fit(
		f"[bold green]✅ ANALYSIS COMPLETE[/bold green]\n"
		f"[dim]Source: {source_name}[/dim]",
		title="[bold]AI Analysis Results[/bold]",
		border_style="green"
	))

	# Create summary table
	table = Table(title="Analysis Summary", show_header=True, header_style="bold magenta")
	table.add_column("Metric", style="cyan", width=20)
	table.add_column("Value", style="white", width=40)

	# Add key metrics from results
	if results.get('parsed'):
		parsed = results['parsed']

		if 'main_topics' in parsed:
			topics = parsed['main_topics']
			if isinstance(topics, list):
				table.add_row("Main Topics", f"{len(topics)} topics found")
				for i, topic in enumerate(topics[:3]):  # Show first 3
					table.add_row(f"  Topic {i + 1}", str(topic))
			else:
				table.add_row("Main Topics", str(topics))

		if 'sentiment_score' in parsed:
			score = parsed['sentiment_score']
			sentiment = "Positive" if score > 0.6 else "Negative" if score < 0.4 else "Neutral"
			table.add_row("Sentiment", f"{sentiment} ({score})")

		if 'overall_mood' in parsed:
			table.add_row("Overall Mood", str(parsed['overall_mood']))

	# Add technical details
	if results.get('request'):
		req = results['request']
		if 'model' in req:
			table.add_row("LLM Model", str(req['model']))
		if 'provider' in req:
			table.add_row("Provider", str(req['provider']))

	if results.get('response', {}).get('usage'):
		usage = results['response']['usage']
		prompt_tokens = usage.get('prompt_tokens', 0)
		completion_tokens = usage.get('completion_tokens', 0)
		total_tokens = prompt_tokens + completion_tokens
		table.add_row("Tokens Used", f"{total_tokens} ({prompt_tokens} + {completion_tokens})")

	console.print(table)


@click.group()
def scheduler_cli():
	"""Content collection scheduler commands."""
	pass


@scheduler_cli.command("run")
@click.option("--interval", "-i", default=60, help="Collection interval in minutes (default: 60)")
@click.option("--once", is_flag=True, help="Run one collection cycle and exit")
@click.option("--source-id", type=int, help="(Deprecated) Collect from specific source ID")
@click.option("--source-url", help="Collect from specific source by URL (e.g., https://vk.com/username)")
@click.option("--start-date", help="Start date for collection (YYYY-MM-DD)")
@click.option("--end-date", help="End date for collection (YYYY-MM-DD, defaults to today)")
@click.option("--force-refresh", is_flag=True, help="Force re-analysis by resetting last_checked")
def run_scheduler(interval: int, once: bool, source_id: int, source_url: str, start_date: str, end_date: str, force_refresh: bool):
	"""
	Start content collection scheduler.
	
	Examples:
		python -m cli.scheduler run                                    # Run all sources every hour
		python -m cli.scheduler run -i 30                              # Run every 30 minutes
		python -m cli.scheduler run --once                             # Run once and exit
		python -m cli.scheduler run --source-url https://vk.com/user1  # Collect from specific source
		python -m cli.scheduler run --source-id 1 --force-refresh      # Force refresh specific source
	"""
	click.echo("=" * 60)
	click.echo("CONTENT COLLECTION SCHEDULER")
	click.echo("=" * 60)
	click.echo()

	if once or source_id or source_url or start_date or end_date or force_refresh:
		if source_url:
			click.echo(f"Running collection for source URL {source_url}...")
		elif source_id:
			click.echo(f"Running collection for source ID {source_id} (consider using --source-url instead)...")
		else:
			click.echo("Running one collection cycle...")
		
		if force_refresh:
			click.echo("⚠️  Force refresh enabled - will reset last_checked")

		# Set default end date to today if not provided
		if start_date and not end_date:
			end_date = date.today().isoformat()

		if start_date:
			click.echo(f"Date range: {start_date} to {end_date or 'now'}")

		stats = asyncio.run(cli_scheduler.run_collection_cycle(
			source_id=source_id,
			source_url=source_url,
			start_date=start_date,
			end_date=end_date,
			force_refresh=force_refresh
		))

		click.echo()
		click.echo("✅ Collection cycle complete!")
		click.echo(f"   Collected: {stats['collected']}/{stats['total_sources']}")
		click.echo(f"   Skipped: {stats['skipped']} | Failed: {stats['failed']}")
		click.echo(f"   Total items: {stats['total_content']}")
		click.echo(f"   Total cost: ${stats['total_cost']:.4f}")
		if stats.get('start_date') or stats.get('end_date'):
			click.echo(f"   Date range: {stats.get('start_date') or 'start'} to {stats.get('end_date') or 'now'}")
	else:
		click.echo(f"Starting scheduler (interval: {interval} minutes)")
		click.echo("Press Ctrl+C to stop")
		click.echo()

		# For continuous mode, we'll still use the original scheduler for now
		# as the CLI version doesn't have run_forever implemented
		from app.services.scheduler import scheduler
		try:
			asyncio.run(scheduler.run_forever(interval))
		except KeyboardInterrupt:
			click.echo()
			click.echo("⏹️  Scheduler stopped by user")


@scheduler_cli.command("status")
def scheduler_status():
	"""Show scheduler status and statistics."""
	click.echo("Scheduler Status:")
	click.echo("  Status: Not implemented yet")
	click.echo("  Use 'cli scheduler run --once' to test collection")


if __name__ == "__main__":
	scheduler_cli()
