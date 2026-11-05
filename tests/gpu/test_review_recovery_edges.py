import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.db import Store
from app.gpu.compute import MockColabProvider, MockGPUExecutor
from app.gpu.mock_sessions import SessionError
from app.gpu.repository import Repository
from app.gpu.scheduler import Scheduler


def test_prepared_but_not_started_execution_recovers_same_identity():
    repo=Repository(Store('sqlite://'))
    job=repo.create('jobs','u',{'state':'loading','provider':'mock'})
    provider=MockColabProvider()
    handle=provider.create({},str(uuid.uuid4()),job['id'],str(uuid.uuid4()))
    lease=repo.create('leases','u',{'state':'active','owner_job_id':job['id'],'handle':handle})
    prepared=repo.prepare_execution(job['id'],'u',lease['id'],1)
    executor=MockGPUExecutor()
    scheduler=Scheduler(repo,executor_factory=lambda job:executor)
    result=scheduler._load(prepared)
    assert result['state']=='running'
    assert executor.started==[prepared['execution_id']]
    assert len(repo.list('executions','u'))==1


def test_same_owner_foreign_lease_is_not_resolved():
    repo=Repository(Store('sqlite://'))
    lease=repo.create('leases','u',{'state':'active','owner_job_id':'other-job'})
    job=repo.create('jobs','u',{'state':'running','lease_id':lease['id']})
    assert Scheduler(repo)._lease(job) is None


def test_usage_concurrent_insert_has_one_database_identity(tmp_path):
    repo=Repository(Store('sqlite:///'+str(tmp_path/'usage.db')))
    payload={'job_id':'j','execution_id':'e','units':1}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:repo.record_usage_once('u',payload),range(2)))
    assert results[0]['id']==results[1]['id']
    assert len(repo.list('usage','u'))==1


def test_old_mock_poll_is_rejected_after_next_execution_starts():
    executor=MockGPUExecutor()
    handle={'runtime_name':'mock-runtime-'+str(uuid.uuid4())}
    first={'owner_id':'u','execution_id':str(uuid.uuid4())}
    second={**first,'execution_id':str(uuid.uuid4())}
    executor.start(handle,first,first['execution_id'])
    assert executor.poll(handle,first)['state']=='completed'
    executor.start(handle,second,second['execution_id'])
    with pytest.raises(SessionError,match='stale'):
        executor.poll(handle,first)


def test_retry_clears_old_presentation_and_archives_it():
    repo=Repository(Store('sqlite://'))
    job=repo.create('jobs','u',{'state':'failed','provider':'mock','stage':'sampling',
        'worker_timings':{'sampling':9},'output_bytes':123,'started_at':'old'})
    result=repo.retry_attempt(job['id'],'u','retry')
    for key in ('stage','worker_timings','output_bytes','started_at'):
        assert key not in result
    assert result['attempt_history'][0]['stage']=='sampling'


def test_handoff_refuses_successor_with_existing_lease():
    repo=Repository(Store('sqlite://'))
    first=repo.create('jobs','u',{'state':'cleaning_up','compatibility_signature':'sig'})
    following=repo.create('jobs','u',{'state':'waiting_for_capacity','compatibility_signature':'sig'})
    lease=repo.create('leases','u',{'state':'active','owner_job_id':first['id'],'deadline_epoch':100})
    other=repo.create('leases','u',{'state':'active','owner_job_id':following['id'],'deadline_epoch':100})
    assert not repo.handoff_lease('u',first['id'],following['id'],lease['id'],1)
    assert repo.get('leases',other['id'],'u')['owner_job_id']==following['id']
    assert repo.get('leases',lease['id'],'u')['owner_job_id']==first['id']


def test_raw_outputs_with_foreign_execution_are_rejected():
    repo=Repository(Store('sqlite://'))
    job=repo.create('jobs','u',{'state':'running','provider':'mock','execution_id':'new'})
    repo.create('leases','u',{'state':'active','owner_job_id':job['id'],'handle':{}})
    class Executor:
        def poll(self,handle,job):
            return {'state':'completed','execution_id':'old','outputs':[{'content':b'fake','mime':'image/png'}]}
    result=Scheduler(repo,executor_factory=lambda job:Executor())._run(job)
    assert result['state']=='cleaning_up'
    assert result['error_code']=='gpu_execution_id_mismatch'
    assert not result.get('outputs')
