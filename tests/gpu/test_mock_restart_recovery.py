from app.gpu.compute import MockColabProvider


def test_mock_recovers_persisted_lease_after_process_registry_loss():
    provider = MockColabProvider()
    handle = provider.create({}, 'request-restart', 'job-restart', 'runtime-restart')
    handle['owner_id'] = 'owner-restart'
    provider._runtimes.pop(handle['runtime_name'])
    recovered = MockColabProvider().recover_handle(handle)
    assert recovered['owner_id'] == 'owner-restart'
    assert MockColabProvider().delete(recovered) is True
