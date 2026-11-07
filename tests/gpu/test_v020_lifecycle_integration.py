from app.db import Store
from app.gpu.compute import MockColabProvider, MockGPUExecutor
from app.gpu.repository import Repository
from app.gpu.scheduler import Scheduler


def test_real_instance_service_mock_job_retains_reuses_and_stops(tmp_path):
    repo = Repository(Store('sqlite:///' + str(tmp_path / 'flow.db')))
    now = [1000.0]
    provider, executor = MockColabProvider(), MockGPUExecutor()
    scheduler = Scheduler(repo, provider_factory=lambda _: provider, executor_factory=lambda _: executor, clock=lambda: now[0])
    def create(key, instance_id=None):
        policy = {'provider': 'mock', 'lifecycle': 'retain', 'retain_idle_minutes': 2}
        if instance_id:
            policy.update(instance_id=instance_id, allocation='existing')
        return repo.create_job('owner-1', {
            'provider': 'mock', 'project_id': 'project-1',
            'snapshot': {'manifest': {'mock': True}},
            'execution_policy': policy, 'estimated_units': 0,
        }, key)[0]
    def finish(job):
        for _ in range(50):
            scheduler.tick()
            job = repo.get('jobs', job['id'], 'owner-1')
            if job['state'] in {'completed', 'failed', 'cancelled'}:
                break
        assert job['state'] == 'completed', job
        return job
    first = finish(create('first'))
    assert first.get('instance_id')
    instance = repo.get('instances', first['instance_id'], 'owner-1')
    assert instance['state'] == 'retained_idle', instance
    lease = first['lease_id']
    scheduler.watchdog()
    assert repo.get('instances', instance['id'], 'owner-1')['state'] == 'retained_idle'
    second = finish(create('second', instance['id']))
    assert second['instance_id'] == first['instance_id']
    assert second['lease_id'] == lease
    assert len(repo.list('instances', 'owner-1')) == 1
    scheduler.instances.stop('owner-1', instance['id'], 'stop-one')
    scheduler.watchdog()
    assert repo.get('instances', instance['id'], 'owner-1')['state'] == 'deleted'
