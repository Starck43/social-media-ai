#!/usr/bin/env python3
"""
Direct test script for VK content collection.
Bypasses API authentication for testing purposes.
"""

import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.core.config import settings
from app.models import Source, Platform
from app.services.social.vk_client import VKClient


async def test_collection():
    """Test VK collection directly"""
    
    print('=== VK Collection Test ===\n')
    
    # Setup database
    db_url = settings.POSTGRES_URL.replace('postgresql://', 'postgresql+asyncpg://')
    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        # Get source
        result = await session.execute(select(Source).where(Source.id == 2))
        source = result.scalar_one_or_none()
        
        if not source:
            print('❌ Source with ID=2 not found')
            return
        
        # Get platform
        result = await session.execute(select(Platform).where(Platform.id == source.platform_id))
        platform = result.scalar_one_or_none()
        
        if not platform:
            print('❌ Platform not found')
            return
        
        print(f'Source: {source.name}')
        print(f'Type: {source.source_type}')
        print(f'External ID: {source.external_id}')
        print(f'Platform: {platform.name}')
        print()
        
        # Create VKClient
        client = VKClient(platform=platform)
        
        # Collect posts
        print('Collecting posts...')
        try:
            posts = await client.collect_data(
                source=source,
                content_type='posts'
            )
            
            print(f'\n✅ Successfully collected {len(posts)} posts\n')
            
            # Display posts
            for i, post in enumerate(posts[:5], 1):
                print(f'{i}. Post ID: {post.get("id")}')
                print(f'   External ID: {post.get("external_id")}')
                print(f'   Date: {post.get("date")}')
                print(f'   Text: {post.get("text", "")[:100]}...')
                print(f'   Likes: {post.get("likes")} | Comments: {post.get("comments")} | Shares: {post.get("shares")}')
                print(f'   Views: {post.get("views")} | Reactions: {post.get("reactions")}')
                print()
                
            # Show summary
            total_likes = sum(p.get('likes', 0) for p in posts)
            total_comments = sum(p.get('comments', 0) for p in posts)
            total_views = sum(p.get('views', 0) for p in posts)
            
            print(f'--- Summary ---')
            print(f'Total posts: {len(posts)}')
            print(f'Total likes: {total_likes}')
            print(f'Total comments: {total_comments}')
            print(f'Total views: {total_views}')
            
        except Exception as e:
            print(f'❌ Collection failed: {e}')
            import traceback
            traceback.print_exc()
    
    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(test_collection())
