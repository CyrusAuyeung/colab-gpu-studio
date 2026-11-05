
from app.db import Store
from app.gpu.mcp import MCPError, MCPTransient
from app.gpu.repository import Repository
from app.gpu.scheduler import Scheduler


def setup_job(error):
    repo=Repository(Store('sqlite://'))
    job=repo.create('jobs','owner',{'state':'running','execution_id':'original-execution'})
    repo.create('leases','owner',{'owner_job_id':job['id'],'state':'active','handle':{}})
    class Executor:
        calls=0
        def poll(self,*args):
            self.calls+=1
            raise error
    executor=Executor()
    clock=[100.0]
    scheduler=Scheduler(repo,executor_factory=lambda _:executor,clock=lambda:clock[0])
    return repo,job,scheduler,executor,clock


def test_transient_poll_is_durable_and_backed_off():
    repo,job,scheduler,executor,clock=setup_job(MCPTransient('offline interruption'))
    result=scheduler._run(job)
    assert result['state']=='running'
    assert result['execution_id']=='original-execution'
    assert result['poll_retry_count']==1 and result['poll_retry_at']>clock[0]
    # Recreate scheduler as on restart; durable deadline suppresses immediate I/O.
    restarted=Scheduler(repo,executor_factory=lambda _:executor,clock=lambda:clock[0])
    assert restarted._run(repo.get('jobs',job['id'],'owner'))['state']=='running'
    assert executor.calls==1


def test_poll_retries_have_finite_budget():
    repo,job,scheduler,executor,clock=setup_job(MCPTransient('offline interruption'))
    for _ in range(6):
        job=scheduler._run(job)
        clock[0]+=100
    assert job['state']=='cleaning_up'
    assert job['error_code']=='gpu_poll_failed'
    assert executor.calls==6


def test_integrity_failure_is_not_retried():
    _,job,scheduler,executor,_=setup_job(MCPError('artifact chunk identity mismatch'))
    result=scheduler._run(job)
    assert result['state']=='cleaning_up' and result['error_code']=='gpu_poll_failed'
    assert executor.calls==1



def test_new_attempt_drops_previous_poll_backoff_but_keeps_history():
    repo=Repository(Store('sqlite://'))
    job=repo.create('jobs','owner',{'state':'failed','provider':'mock',
        'poll_retry_count':5,'poll_retry_at':9999999999})
    retried=repo.retry_attempt(job['id'],'owner','new-attempt')
    assert 'poll_retry_at' not in retried and 'poll_retry_count' not in retried
    assert retried['attempt_history'][0]['poll_retry_count']==5
