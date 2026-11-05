from app.db import Store
from app.gpu.compute import MockColabProvider, MockGPUExecutor
from app.gpu.repository import Repository
from app.gpu.scheduler import Scheduler


def test_new_scheduler_recovers_existing_execution_without_restart(tmp_path):
    url = 'sqlite:///' + str(tmp_path / 'recovery.db')
    repo = Repository(Store(url))
    jobs = [{'project_id': 'p', 'snapshot': {'manifest': {'mock': True}},
             'execution_policy': {'provider': 'mock'}} for _ in range(3)]
    repo.create_batch('u', jobs, 'recovery', {'mode': 'batch'})
    provider, executor = MockColabProvider(), MockGPUExecutor()
    scheduler = Scheduler(repo, provider_factory=lambda job: provider, executor_factory=lambda job: executor)
    for _ in range(20):
        scheduler.tick()
        if any(job['state'] == 'running' for job in repo.list('jobs', 'u')):
            break
    running = next(job for job in repo.list('jobs', 'u') if job['state'] == 'running')
    first_execution = running['execution_id']
    # Persisted database is reopened; the fake remote process deliberately survives.
    recovered = Repository(Store(url))
    scheduler = Scheduler(recovered, provider_factory=lambda job: provider, executor_factory=lambda job: executor)
    for _ in range(60):
        scheduler.tick()
        if all(job['state'] == 'completed' for job in recovered.list('jobs', 'u')):
            break
    assert all(job['state'] == 'completed' for job in recovered.list('jobs', 'u'))
    assert executor.started.count(first_execution) == 1
    assert len(executor.started) == 3
    assert provider.create_calls == provider.delete_calls == 1


def test_unknown_remote_execution_never_restarts_automatically(tmp_path):
    repo = Repository(Store('sqlite:///' + str(tmp_path / 'unknown.db')))
    repo.create_batch('u', [{'project_id': 'p', 'snapshot': {'manifest': {'mock': True}},
                            'execution_policy': {'provider': 'mock'}}], 'unknown', {'mode': 'batch'})
    provider, executor = MockColabProvider(), MockGPUExecutor()
    scheduler = Scheduler(repo, provider_factory=lambda job: provider, executor_factory=lambda job: executor)
    for _ in range(20):
        scheduler.tick()
        if repo.list('jobs', 'u')[0]['state'] == 'running':
            break
    # New fake remote has lost its marker. Recovery must clean up, never start again.
    unknown_executor = MockGPUExecutor()
    unknown_executor._executions = {}
    scheduler = Scheduler(repo, provider_factory=lambda job: provider, executor_factory=lambda job: unknown_executor)
    for _ in range(10):
        scheduler.tick()
    job = repo.list('jobs', 'u')[0]
    assert job['state'] == 'failed'
    assert job['error_code'] == 'gpu_poll_failed'
    assert unknown_executor.started == []
    assert provider.delete_calls == 1
