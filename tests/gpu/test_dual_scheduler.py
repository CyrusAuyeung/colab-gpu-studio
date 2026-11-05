"""Two independently connected schedulers contend at each durable phase."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

from app.db import Store
from app.gpu.compute import MockColabProvider, MockGPUExecutor
from app.gpu.repository import Repository
from app.gpu.scheduler import Scheduler


def test_two_schedulers_start_single_execution_and_allocate_single_lease(tmp_path):
    url='sqlite:///'+str(tmp_path/'shared.db')
    repos=[Repository(Store(url)),Repository(Store(url))]
    job,_=repos[0].create_job('owner',{'provider':'mock'},'contended')
    entered,release=Event(),Event()
    class PausedExecutor(MockGPUExecutor):
        def start(self,*args):
            entered.set()
            assert release.wait(5)
            return super().start(*args)
    provider,executor=MockColabProvider(),PausedExecutor()
    schedulers=[Scheduler(repo,provider_factory=lambda _:provider,executor_factory=lambda _:executor) for repo in repos]
    # Prepare to immediately before the adapter start boundary.
    for _ in range(12):
        if repos[0].get('jobs',job['id'],'owner')['state']=='loading':
            break
        schedulers[0].tick()
    assert repos[0].get('jobs',job['id'],'owner')['state']=='loading'
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(schedulers[0].tick)
        try:
            assert entered.wait(5)
            second=pool.submit(schedulers[1].tick).result(timeout=3)
            assert executor.start_calls==0
            assert not second.get('advanced')
        finally:
            release.set()
        first.result(timeout=5)
        for _ in range(24):
            barrier=Barrier(2)
            def step(index):
                barrier.wait()
                return schedulers[index].tick()
            list(pool.map(step,range(2)))
            if repos[0].get('jobs',job['id'],'owner')['state']=='completed':
                break
    result=repos[0].get('jobs',job['id'],'owner')
    assert result['state']=='completed'
    assert provider.create_calls==1 and provider.delete_calls==1
    assert executor.start_calls==1
    assert len(repos[0].list('executions','owner'))==1
    assert len(repos[0].list('leases','owner'))==1
