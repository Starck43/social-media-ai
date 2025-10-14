#!/usr/bin/env python3
"""
Direct test script for VK content collection.
Bypasses API authentication for testing purposes.
Updated for new VKClient methods and error handling.
"""

import asyncio
from typing import cast

from sqlalchemy import select, ColumnElement
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import settings
from app.models import Source, Platform
from app.services.social.vk_client import VKClient


async def test_collection():
    """Test VK collection directly"""
    
    print('=== VK Collection Test ===\n')
    
    # Setup database
    db_url = settings.POSTGRES_URL.replace('postgresql://', 'postgresql+asyncpg://')
    engine = create_async_engine(db_url, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        # Get source (use a more robust way to find test source)
        result = await session.execute(
            select(Source)
            .where(Source.is_active)
            .limit(1)
        )
        source = result.scalar_one_or_none()
        
        if not source:
            print('❌ No active sources found. Please create a test source in admin panel.')
            return
        
        # Get platform
        result = await session.execute(select(Platform).where(cast(ColumnElement, Platform.id == source.platform_id)))
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
        
        # Test different content types
        content_types = ['posts', 'comments', 'info']
        
        for content_type in content_types:
            print(f'Collecting {content_type}...')
            try:
                data = await client.collect_data(
                    source=source,
                    content_type=content_type
                )
                
                print(f'✅ Successfully collected {len(data)} {content_type}\n')
                
                # Display sample
                for i, item in enumerate(data[:3], 1):
                    print(f'{i}. {content_type.capitalize()} ID: {item.get("id")}')
                    if content_type == 'posts':
                        print(f'   Text: {item.get("text", "")[:100]}...')
                        print(f'   Likes: {item.get("likes")} | Comments: {item.get("comments")}')
                    print()
                    
            except Exception as e:
                print(f'❌ Collection failed for {content_type}: {e}')
        
    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(test_collection())
