"""Create two synthetic threads, run local HTTP archive checks, and remove them."""
import asyncio, os, subprocess
from datetime import datetime, timezone
from urllib.parse import urlsplit
url = urlsplit(os.environ.get("POSTGRES_URL", "").replace("postgresql+asyncpg://", "postgresql://", 1))
assert url.hostname == "127.0.0.1" and url.port == 55432 and url.path == "/p20_work_demo"
assert os.environ.get("DB_SCHEMA") == "p20_t16_demo"
from sqlalchemy import select, delete, func
from app.core.database import async_session_maker
from app.models import WorkspaceConnection, MessageThread
from app.core.tenant_context import tenant_scope

async def run():
    keys = ['review-archive-nonarchived-fixture', 'review-archive-archived-fixture']
    with tenant_scope(1):
        async with async_session_maker() as s:
            conn=await s.scalar(select(WorkspaceConnection).where(WorkspaceConnection.account_key=='synthetic-offline'))
            assert conn and conn.tenant_id == 1
            assert not await s.scalar(select(func.count()).select_from(MessageThread).where(MessageThread.thread_key.in_(keys)))
            for i,k in enumerate(keys):
                s.add(MessageThread(tenant_id=1,connection_id=conn.id,channel='email',thread_key=k,subject_encrypted=k,participants_encrypted='[]',last_message_at=datetime.now(timezone.utc),message_count=0,unread_count=0,is_archived=bool(i)))
            await s.commit()
            rows=(await s.execute(select(MessageThread.thread_key,MessageThread.id).where(MessageThread.thread_key.in_(keys)))).all()
            ids=dict(rows)
        try:
            env={**os.environ,'P20_ACTIVE_THREAD':str(ids[keys[0]]),'P20_ARCHIVED_THREAD':str(ids[keys[1]])}
            subprocess.run(['node','tests/browser/p20_t16_archive_filter.cjs'],env=env,check=True,timeout=120)
        finally:
            async with async_session_maker() as s:
                await s.execute(delete(MessageThread).where(MessageThread.thread_key.in_(keys)))
                await s.commit()
                remaining=await s.scalar(select(func.count()).select_from(MessageThread).where(MessageThread.thread_key.in_(keys)))
                assert remaining == 0, f'fixture rows left: {remaining}'
                print('PASS synthetic thread cleanup')
asyncio.run(run())
