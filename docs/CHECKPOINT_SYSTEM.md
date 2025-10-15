# Checkpoint System - Minimizing Duplicate Analysis

## Problem

Social media platforms have millions of posts. Without checkpoints:
- **Re-analyze same content** every time (waste of money)
- **No incremental collection** (always fetch from beginning)
- **No resume on failure** (lose progress)

## Architecture

```
BotScenario:
  collection_interval_hours = 1  ← How often to collect
  llm_strategy = "cost_efficient"

Source:
  last_checked = 2024-01-15 14:30:00  ← Checkpoint timestamp
  bot_scenario_id = 1

CheckpointManager:
  should_collect(source) → checks last_checked + interval
  
ContentScheduler:
  Runs every hour → collects only needed sources
```

## Solution: Checkpoint System

Track `last_checked` timestamp for each Source:

```python
Source:
  last_checked: 2024-01-15 14:30:00 UTC  # Last successful collection
  params: {                               # Platform-specific cursors
    "vk_offset": 100,                    # VK pagination offset
    "instagram_max_id": "abc123",        # Instagram cursor
    "telegram_offset_id": 42             # Telegram message ID
  }
```

## How It Works

### 1. Check if Collection Needed

```python
from app.services.checkpoint_manager import CheckpointManager

source = await Source.objects.get(id=1)

# Check if we should collect
# Uses source.bot_scenario.collection_interval_hours automatically
if CheckpointManager.should_collect(source):
    # Collect new content
    await collect_and_analyze(source)
else:
    # Skip - collected recently
    logger.info(f"Source {source.id} checked recently, skipping")

# Or specify custom interval
if CheckpointManager.should_collect(source, collection_interval_hours=2):
    await collect_and_analyze(source)
```

### 2. Collect Only New Content

```python
# Get checkpoint data
checkpoint = await CheckpointManager.get_checkpoint(source)
last_checked = checkpoint["last_checked"]  # e.g., 2024-01-15 14:30:00

# VK API: Get posts AFTER last_checked
vk_params = {
    "owner_id": source.external_id,
    "count": 100,
    "start_time": int(last_checked.timestamp()) if last_checked else None
}
posts = await vk_api.wall.get(**vk_params)

# Result: Only NEW posts since last collection
# No duplicates, no re-analysis!
```

### 3. Save Checkpoint After Success

```python
from app.services.checkpoint_manager import CollectionResult

# Collect content
new_posts = await collect_from_vk(source)

# Create result
result = CollectionResult(
    source_id=source.id,
    content_count=len(new_posts),
    has_new_content=len(new_posts) > 0,
    checkpoint_params={
        "vk_offset": 0,  # Reset offset
        "last_post_id": new_posts[0]["id"] if new_posts else None
    }
)

# Save checkpoint (updates last_checked timestamp)
await result.save_checkpoint()
```

## Platform-Specific Implementation

### VK (VKontakte)

```python
async def collect_vk_posts(source: Source) -> List[dict]:
    """Collect VK posts using checkpoint."""
    
    # Get checkpoint
    checkpoint = await CheckpointManager.get_checkpoint(source)
    last_checked = checkpoint["last_checked"]
    
    # Build VK API params
    params = {
        "owner_id": f"-{source.external_id}",  # Group ID
        "count": 100,
        "filter": "owner"
    }
    
    # Add timestamp filter (collect posts AFTER last_checked)
    if last_checked:
        params["start_time"] = int(last_checked.timestamp())
    
    # Call VK API
    response = await vk_api.wall.get(**params)
    posts = response["items"]
    
    # Filter: Only posts newer than checkpoint
    if last_checked:
        posts = [
            p for p in posts 
            if datetime.fromtimestamp(p["date"], tz=timezone.utc) > last_checked
        ]
    
    logger.info(f"Collected {len(posts)} new VK posts for source {source.id}")
    
    return posts
```

### Instagram

```python
async def collect_instagram_posts(source: Source) -> List[dict]:
    """Collect Instagram posts using checkpoint."""
    
    # Get checkpoint with cursor
    checkpoint = await CheckpointManager.get_checkpoint(source)
    max_id = checkpoint["params"].get("instagram_max_id")
    
    # Instagram API with cursor-based pagination
    params = {
        "user_id": source.external_id,
        "count": 50
    }
    
    if max_id:
        # Get posts AFTER this cursor
        params["max_id"] = max_id
    
    # Call Instagram API
    response = await instagram_api.get_user_media(**params)
    posts = response["data"]
    
    # Save new cursor for next collection
    if posts:
        new_max_id = posts[0]["id"]
        await CheckpointManager.update_checkpoint(
            source.id,
            params={"instagram_max_id": new_max_id}
        )
    
    return posts
```

### Telegram

```python
async def collect_telegram_messages(source: Source) -> List[dict]:
    """Collect Telegram messages using checkpoint."""
    
    # Get checkpoint
    checkpoint = await CheckpointManager.get_checkpoint(source)
    offset_id = checkpoint["params"].get("telegram_offset_id", 0)
    
    # Telegram API with message ID offset
    messages = await telegram_client.get_messages(
        entity=source.external_id,
        limit=100,
        offset_id=offset_id,  # Get messages AFTER this ID
        reverse=True
    )
    
    # Save new offset
    if messages:
        new_offset_id = messages[-1].id
        await CheckpointManager.update_checkpoint(
            source.id,
            params={"telegram_offset_id": new_offset_id}
        )
    
    return messages
```

## Complete Flow Example

```python
from app.services.checkpoint_manager import CheckpointManager, CollectionResult
from app.services.ai.analyzer_v2 import AIAnalyzerV2

async def collect_and_analyze_source(source: Source):
    """
    Complete flow with checkpoint:
    1. Check if collection needed
    2. Collect only new content
    3. Analyze with LLM
    4. Save checkpoint
    """
    
    # Step 1: Check if we should collect
    if not CheckpointManager.should_collect(source, collection_interval_minutes=60):
        logger.info(f"Source {source.id} checked recently, skipping")
        return None
    
    logger.info(f"Starting collection for source {source.id}")
    
    # Step 2: Collect new content using checkpoint
    if source.platform.name == "VK":
        new_content = await collect_vk_posts(source)
    elif source.platform.name == "Instagram":
        new_content = await collect_instagram_posts(source)
    elif source.platform.name == "Telegram":
        new_content = await collect_telegram_messages(source)
    else:
        logger.warning(f"Unknown platform: {source.platform.name}")
        return None
    
    # No new content - still update checkpoint to avoid rechecking
    if not new_content:
        result = CollectionResult(
            source_id=source.id,
            content_count=0,
            has_new_content=False
        )
        await result.save_checkpoint()
        logger.info(f"No new content for source {source.id}")
        return None
    
    logger.info(f"Collected {len(new_content)} new items for source {source.id}")
    
    # Step 3: Analyze with LLM (only new content!)
    analyzer = AIAnalyzerV2()
    analysis = await analyzer.analyze_content(
        content=new_content,
        source=source
    )
    
    if not analysis:
        logger.error(f"Analysis failed for source {source.id}")
        return None
    
    # Step 4: Save checkpoint on success
    result = CollectionResult(
        source_id=source.id,
        content_count=len(new_content),
        has_new_content=True,
        checkpoint_params={
            # Platform-specific cursors saved here
        }
    )
    await result.save_checkpoint()
    
    logger.info(
        f"✅ Successfully analyzed {len(new_content)} items for source {source.id}, "
        f"checkpoint updated"
    )
    
    return analysis
```

## Benefits

### Before (No Checkpoints)

```python
# Fetch ALL posts every time
posts = await vk_api.wall.get(owner_id="-12345", count=1000)
# Result: 1000 posts (990 already analyzed before)

# Analyze ALL posts again
for post in posts:
    analysis = await llm.analyze(post)  # $$$$ waste
    
# Cost: $100 for 1000 posts (990 duplicates)
```

### After (With Checkpoints)

```python
# Fetch only NEW posts since last_checked
posts = await vk_api.wall.get(
    owner_id="-12345",
    start_time=last_checked.timestamp()  # Filter by checkpoint
)
# Result: 10 NEW posts only

# Analyze only NEW posts
for post in posts:
    analysis = await llm.analyze(post)
    
# Cost: $1 for 10 posts (99% savings!)
```

## Monitoring

Check checkpoint status:

```python
# Get all sources with old checkpoints
from datetime import timedelta

cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
stale_sources = await Source.objects.filter(
    is_active=True,
    last_checked__lt=cutoff
)

for source in stale_sources:
    logger.warning(f"Source {source.id} not checked for 24h, last: {source.last_checked}")
```

## Automated Scheduler

**ContentScheduler runs automatically:**

```bash
# Run every hour (default)
python cli/scheduler.py run

# Run every 30 minutes
python cli/scheduler.py run -i 30

# Run once and exit
python cli/scheduler.py run --once
```

**What it does:**
1. Gets all active sources
2. Checks which need collection (via checkpoint)
3. Collects only NEW content
4. Analyzes with LLM (optimized)
5. Saves checkpoints
6. Logs statistics and costs

**Example output:**
```
COLLECTION CYCLE COMPLETE
Collected: 15/20
Skipped: 3 | Failed: 2
Total content: 450 items
Total cost: $0.45
```

## Summary

**Checkpoint System eliminates duplicate analysis:**

1. ✅ Track `last_checked` per source
2. ✅ Collect only NEW content (timestamp/cursor filters)
3. ✅ Save checkpoint after success
4. ✅ Resume from checkpoint on failure
5. ✅ Automatic scheduling (ContentScheduler)
6. ✅ Respects `collection_interval_hours` from BotScenario

**Result: 90-99% cost reduction** by avoiding re-analysis!
