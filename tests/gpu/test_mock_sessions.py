import uuid

import pytest
from app.gpu.compute import ComputeError, MockGPUExecutor
from app.gpu.mock_sessions import MockWorkerSessions, SessionError


def test_session_reuses_service_and_rejects_foreign_or_incompatible_execution():
    runtime='mock-runtime-'+str(uuid.uuid4())
    handle={'runtime_name':runtime,'mock':True}
    job={'owner_id':'u','compatibility_signature':'sig','execution_id':str(uuid.uuid4())}
    executor=MockGPUExecutor()
    executor.start(handle,job,job['execution_id'])
    assert executor.start(handle,job,job['execution_id'])['idempotent']
    with pytest.raises(ComputeError,match='owner'):
        executor.start(handle,{**job,'owner_id':'other'},job['execution_id'])
    with pytest.raises(SessionError,match='already executing'):
        executor.start(handle,job,str(uuid.uuid4()))
    session_id=executor._executions[job['execution_id']]['worker_session_id']
    executor.poll(handle,job)
    next_id=str(uuid.uuid4())
    executor.start(handle,{**job,'execution_id':next_id},next_id)
    assert executor._executions[next_id]['worker_session_id']==session_id
    assert MockWorkerSessions._sessions[runtime]['service_starts']==1
    executor.poll(handle,{**job,'execution_id':next_id})
    with pytest.raises(SessionError,match='compatibility'):
        executor.start(handle,{**job,'compatibility_signature':'other'},str(uuid.uuid4()))
    with pytest.raises(SessionError,match='owner'):
        executor.start(handle,{**job,'owner_id':'other'},str(uuid.uuid4()))
    MockWorkerSessions.close(runtime)
    assert runtime not in MockWorkerSessions._sessions


def test_replayed_execution_rejects_changed_signature():
    executor=MockGPUExecutor()
    handle={'runtime_name':'mock-runtime-'+str(uuid.uuid4())}
    job={'owner_id':'u','compatibility_signature':'a'}
    execution=str(uuid.uuid4())
    executor.start(handle,job,execution)
    with pytest.raises(ComputeError,match='signature'):
        executor.start(handle,{**job,'compatibility_signature':'b'},execution)


def test_mock_provider_rejects_tampered_owner_handle():
    from app.gpu.compute import MockColabProvider
    provider=MockColabProvider()
    handle=provider.create({},str(uuid.uuid4()),'j',str(uuid.uuid4()))
    handle=provider.bind_owner(handle,'u')
    with pytest.raises(ComputeError,match='identity'):
        provider.delete({**handle,'owner_id':'other'})
    assert provider.inspect(handle)['ready']
    assert provider.delete(handle)
