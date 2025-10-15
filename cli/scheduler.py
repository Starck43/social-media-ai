"""
CLI commands for content collection scheduler.
"""
import asyncio
import click

from app.services.scheduler import scheduler


@click.group()
def scheduler_cli():
	"""Content collection scheduler commands."""
	pass


@scheduler_cli.command("run")
@click.option("--interval", "-i", default=60, help="Collection interval in minutes (default: 60)")
@click.option("--once", is_flag=True, help="Run one collection cycle and exit")
def run_scheduler(interval: int, once: bool):
	"""
	Start content collection scheduler.
	
	Examples:
	    cli scheduler run                    # Run every hour
	    cli scheduler run -i 30              # Run every 30 minutes
	    cli scheduler run --once             # Run once and exit
	"""
	click.echo("=" * 60)
	click.echo("CONTENT COLLECTION SCHEDULER")
	click.echo("=" * 60)
	click.echo()
	
	if once:
		click.echo("Running one collection cycle...")
		stats = asyncio.run(scheduler.run_collection_cycle())
		
		click.echo()
		click.echo("✅ Collection cycle complete!")
		click.echo(f"   Collected: {stats['collected']}/{stats['total_sources']}")
		click.echo(f"   Skipped: {stats['skipped']} | Failed: {stats['failed']}")
		click.echo(f"   Total items: {stats['total_content']}")
		click.echo(f"   Total cost: ${stats['total_cost']:.4f}")
	else:
		click.echo(f"Starting scheduler (interval: {interval} minutes)")
		click.echo("Press Ctrl+C to stop")
		click.echo()
		
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
