import pytest
from app.db import Store
from app.gpu.repository import GpuEventRow, Repository
from sqlalchemy import event


def test_transition_event_failure_rolls_back_job_and_execution():
    repo=Repository(Store('sqlite://'))
    job=repo.create('jobs','u',{'state':'running','batch_id':'b','execution_id':'e'})
    repo.create('executions','u',{'state':'running'},record_id='e')
    def reject(mapper,connection,target):
        raise RuntimeError('event disk failure')
    event.listen(GpuEventRow,'before_insert',reject)
    try:
        with pytest.raises(RuntimeError,match='event disk failure'):
            repo.transition_job(job['id'],'u','completed','done',{},123)
    finally:
        event.remove(GpuEventRow,'before_insert',reject)
    assert repo.get('jobs',job['id'],'u')['state']=='running'
    assert repo.get('executions','e','u')['state']=='running'
    assert repo.list('events','u')==[]


def test_transition_commits_all_and_terminal_replay_is_noop():
    repo=Repository(Store('sqlite://'))
    job=repo.create('jobs','u',{'state':'running','execution_id':'e'})
    repo.create('executions','u',{'state':'running'},record_id='e')
    repo.transition_job(job['id'],'u','completed','done',{},123)
    repo.transition_job(job['id'],'u','completed','done',{},124)
    assert repo.get('executions','e','u')['finished_at']==123
    assert len(repo.list('events','u'))==1


def test_stale_state_cannot_overwrite_newer_transition():
    repo=Repository(Store('sqlite://'))
    job=repo.create('jobs','u',{'state':'running'})
    repo.transition_job(job['id'],'u','uploading','persist',{},1,expected_state='running')
    returned=repo.transition_job(job['id'],'u','running','stale',{},2,expected_state='running')
    assert returned['state']=='uploading'
    assert len(repo.list('events','u'))==1


def test_execution_preparation_is_atomic_and_idempotent():
    repo=Repository(Store('sqlite://'))
    job=repo.create('jobs','u',{'state':'loading'})
    lease=repo.create('leases','u',{'state':'active'})
    first=repo.prepare_execution(job['id'],'u',lease['id'],12)
    second=repo.prepare_execution(job['id'],'u',lease['id'],13)
    assert first['execution_id']==second['execution_id']
    executions=repo.list('executions','u')
    assert len(executions)==1
    assert executions[0]['attempt']==1
    assert first['attempt_number']==1


def test_foreign_execution_rolls_back_entire_transition():
    repo=Repository(Store('sqlite://'))
    repo.create('executions','other',{'state':'running'},record_id='foreign')
    job=repo.create('jobs','u',{'state':'running','execution_id':'foreign'})
    with pytest.raises(ValueError,match='owner'):
        repo.transition_job(job['id'],'u','completed','done',{},1)
    assert repo.get('jobs',job['id'],'u')['state']=='running'
    assert repo.list('events','u')==[]


def test_old_execution_callback_cannot_finish_new_attempt():
    repo=Repository(Store('sqlite://'))
    job=repo.create('jobs','u',{'state':'running','execution_id':'new-execution'})
    returned=repo.transition_job(job['id'],'u','failed','old worker failed',{},2,
                                  expected_state='running',expected_execution_id='old-execution')
    assert returned['state']=='running'
    assert repo.list('events','u')==[]
