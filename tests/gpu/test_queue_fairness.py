"""Finite arrival stream cannot bypass the earlier incompatible queue item."""
from app.db import Store
from app.gpu.compute import MockColabProvider, MockGPUExecutor
from app.gpu.repository import Repository
from app.gpu.scheduler import Scheduler


def test_new_compatible_arrivals_do_not_starve_earlier_incompatible_job():
    repo=Repository(Store('sqlite://'))
    def body(version):
        return {'workflow_version':version,'project_id':'p',
                'snapshot':{'manifest':{'mock':True}},'execution_policy':{'provider':'mock'}}
    _,_=repo.create_batch('owner',[body(1),body(2)],'initial',{'mode':'batch'})
    initial=sorted(repo.list('jobs','owner'),key=lambda j:j['queue_order'])
    first,older=initial
    order=[]
    class Executor(MockGPUExecutor):
        def start(self,handle,job,execution_id):
            order.append(job['id'])
            return super().start(handle,job,execution_id)
    provider,executor=MockColabProvider(),Executor()
    scheduler=Scheduler(repo,provider_factory=lambda _:provider,executor_factory=lambda _:executor)
    arrivals=[]
    for tick in range(80):
        # Keep adding new v1 tasks while the original v2 waits.
        if tick<12:
            batch,_=repo.create_batch('owner',[body(1)],'arrival-'+str(tick),{'mode':'batch'})
            arrivals.append(batch['id'])
        scheduler.tick()
        if repo.get('jobs',older['id'],'owner')['state']=='completed':
            break
    assert repo.get('jobs',older['id'],'owner')['state']=='completed'
    assert order[:2]==[first['id'],older['id']]
    assert arrivals
    assert executor.max_concurrent==1
