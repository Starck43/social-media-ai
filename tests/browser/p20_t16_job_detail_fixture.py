import asyncio,os,subprocess
from datetime import datetime,timezone,timedelta
from uuid import uuid4
from sqlalchemy import delete,select
from app.core.database import async_session_maker
from app.models import AgentTask
from app.models.scheduling.job import Job
async def main():
 assert os.environ['DB_SCHEMA']=='p20_t16_demo' and '127.0.0.1:55432/p20_work_demo' in os.environ['POSTGRES_URL'] and os.environ['SCHEDULER_ENABLED']=='false'
 task_id=None; now=datetime.now(timezone.utc); old=now-timedelta(hours=3)
 try:
  async with async_session_maker() as s, s.begin():
   task=AgentTask(tenant_id=1,name='job-detail-'+uuid4().hex,cron_expr='0 9 * * *',job_type='digest',payload={},is_active=False,dry_run=True); s.add(task); await s.flush(); task_id=task.id
   ids=[]
   for _ in range(2):
    j=Job(tenant_id=1,agent_task_id=task_id,job_type='digest',payload={},status='running',run_at=old,created_at=old,locked_at=old,started_at=old,attempts=1,result={'progress':{'attempt':1,'total':5,'completed':2,'items':[]}});s.add(j);await s.flush();ids.append(j.id)
   for _ in range(11):s.add(Job(tenant_id=1,agent_task_id=task_id,job_type='digest',payload={},status='done',run_at=now,created_at=now,attempts=1,result={}))
  env=dict(os.environ,P20_TASK_ID=str(task_id),P20_JOB_ID=str(max(ids)));subprocess.run(['node','tests/browser/p20_t16_job_detail.cjs'],env=env,check=True)
 finally:
  if task_id:
   async with async_session_maker() as s, s.begin():
    await s.execute(delete(Job).where(Job.tenant_id==1,Job.agent_task_id==task_id)); await s.execute(delete(AgentTask).where(AgentTask.tenant_id==1,AgentTask.id==task_id))
   async with async_session_maker() as s: assert await s.scalar(select(AgentTask.id).where(AgentTask.id==task_id)) is None
   print('Temporary task and jobs removed')
asyncio.run(main())
