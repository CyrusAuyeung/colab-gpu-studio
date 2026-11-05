from app.gpu.compute import (
    DisabledExecutor,
    DisabledProvider,
    GpuConfig,
    default_executor_factory,
    default_provider_factory,
)


def test_offline_rejects_even_explicit_real_config(monkeypatch):
    monkeypatch.setenv('STUDIO_OFFLINE_TEST', '1')
    config = GpuConfig(real_enabled=True)
    job = {'provider': 'colab', 'owner_id': 'fake-owner'}
    assert isinstance(default_provider_factory(job, config), DisabledProvider)
    assert isinstance(default_executor_factory(job, config), DisabledExecutor)
