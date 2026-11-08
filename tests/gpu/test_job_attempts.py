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
    old_instance_id=original.get('instance_id')
    assert old_instance_id
    execution=original['execution_id']
    retried=repo.retry_attempt(job['id'],'u','attempt-two')
    assert retried['state']=='queued'
    assert retried.get('attempt_id') == 'attempt-two'
    assert retried.get('instance_id') is None
    assert retried.get('account_id') is None
    assert not retried.get('instance_completion_recorded')
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
    assert result['instance_id'] != old_instance_id
    assert result['execution_id']!=execution
    executions=repo.list('executions','u')
    assert len(executions)==2
    assert repo.get('executions',execution,'u')['state']=='failed'
    assert len(result['attempt_history'])==1


def test_failed_retry_does_not_rebind_to_the_old_requested_instance():
    repo=Repository(Store('sqlite://'))
    repo.create('accounts','u',{
        'account_id':'mock-account','provider':'mock','connected':True,
        'auth_status':'connected','enabled':True,'allow_allocation':True,
    },record_id='mock-account')
    old_instance=repo.create('instances','u',{
        'state':'ready','account_id':'mock-account','provider':'mock',
        'current_job_id':None,'runtime_deleted':False,
    },record_id='old-instance')
    body={
        'provider':'mock',
        'execution_policy':{'provider':'mock','instance_id':old_instance['id']},
    }
    job,_=repo.create_job('u',body,'original',admitted={
        'requested_instance_id':old_instance['id'],
    })
    provider=MockColabProvider()
    scheduler=Scheduler(repo,provider_factory=lambda job:provider,
                        executor_factory=lambda job:MockGPUExecutor(fail_on_start=True))
    for _ in range(20):
        scheduler.tick()
    original=repo.get('jobs',job['id'],'u')
    assert original['state']=='failed'
    assert original['instance_id']==old_instance['id']
    assert original['requested_instance_id']==old_instance['id']
    assert original['execution_policy']['instance_id']==old_instance['id']
    assert repo.get('instances',old_instance['id'],'u')['state'] in {'releasing','deleted'}

    retried=repo.retry_attempt(job['id'],'u','attempt-two')
    assert retried['state']=='queued'
    assert retried.get('instance_id') is None
    assert retried.get('requested_instance_id') is None
    assert retried['execution_policy'].get('instance_id') is None
    assert retried['request_body']==body
    assert retried['attempt_history'][0]['instance_id']==old_instance['id']
    assert retried['attempt_history'][0]['requested_instance_id']==old_instance['id']
    assert retried['attempt_history'][0]['execution_policy']['instance_id']==old_instance['id']

    healthy=MockGPUExecutor()
    scheduler=Scheduler(repo,provider_factory=lambda job:provider,
                        executor_factory=lambda job:healthy)
    for _ in range(20):
        scheduler.tick()
    result=repo.get('jobs',job['id'],'u')
    assert result['state']=='completed'
    assert result['instance_id'] != old_instance['id']


def test_retry_attempt_refuses_uncleaned_lease_and_wrong_owner():
    repo=Repository(Store('sqlite://'))
    job=repo.create('jobs','u',{'state':'failed','provider':'mock'})
    repo.create('leases','u',{'state':'cleanup_pending','owner_job_id':job['id']})
    with pytest.raises(ValueError,match='lease'):
        repo.retry_attempt(job['id'],'u','a')
    with pytest.raises(KeyError):
        repo.retry_attempt(job['id'],'other','a')
