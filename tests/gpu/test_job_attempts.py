import pytest
from app.db import Store
from app.gpu.compute import MockColabProvider, MockGPUExecutor
from app.gpu.repository import Repository
from app.gpu.scheduler import Scheduler


def test_failed_mock_job_can_run_second_attempt_without_losing_evidence():
    repo=Repository(Store('sqlite://'))
    job,_=repo.create_job('u',{'provider':'mock'},'original')
    provider=MockColabProvider()
    failed=MockGPUExecutor(fail_on_start=True)
    scheduler=Scheduler(repo,provider_factory=lambda job:provider,executor_factory=lambda job:failed)
    for _ in range(20):
        scheduler.tick()
    original=repo.get('jobs',job['id'],'u')
    assert original['state']=='failed'
    execution=original['execution_id']
    retried=repo.retry_attempt(job['id'],'u','attempt-two')
    assert retried['state']=='queued'
    assert retried['snapshot']==original['snapshot'] if 'snapshot' in original else 'snapshot' not in retried
    assert repo.retry_attempt(job['id'],'u','attempt-two')['id']==job['id']
    with pytest.raises(ValueError):
        repo.retry_attempt(job['id'],'u','other-key')
    healthy=MockGPUExecutor()
    scheduler=Scheduler(repo,provider_factory=lambda job:provider,executor_factory=lambda job:healthy)
    for _ in range(20):
        scheduler.tick()
    result=repo.get('jobs',job['id'],'u')
    assert result['state']=='completed'
    assert result['attempt_number']==2
    assert result['execution_id']!=execution
    executions=repo.list('executions','u')
    assert len(executions)==2
    assert repo.get('executions',execution,'u')['state']=='failed'
    assert len(result['attempt_history'])==1


def test_retry_attempt_refuses_uncleaned_lease_and_wrong_owner():
    repo=Repository(Store('sqlite://'))
    job=repo.create('jobs','u',{'state':'failed','provider':'mock'})
    repo.create('leases','u',{'state':'cleanup_pending','owner_job_id':job['id']})
    with pytest.raises(ValueError,match='lease'):
        repo.retry_attempt(job['id'],'u','a')
    with pytest.raises(KeyError):
        repo.retry_attempt(job['id'],'other','a')
