"""Real consumption must never fall back to an unspecified Google identity."""
from types import SimpleNamespace

import pytest
from app import compute_routes, review_routes
from app.common import APIError
from app.gpu.compute import GpuConfig


@pytest.mark.parametrize('account_id', [None, '', '   ', 123])
@pytest.mark.parametrize('route', ['cuda', 'workflow'])
def test_smoke_requires_explicit_account_before_repository_or_upstream(monkeypatch, account_id, route):
    monkeypatch.setattr(compute_routes.GpuConfig, 'from_env', lambda: GpuConfig(real_enabled=True, unit_rate=1))
    module = compute_routes if route == 'cuda' else review_routes
    monkeypatch.setattr(module, 'Repository', lambda _: pytest.fail('missing identity reached repository'))
    req = SimpleNamespace(headers={'Idempotency-Key': 'missing-account'}, state=SimpleNamespace(request_id='test'))
    body = {'confirmed': True, 'account_id': account_id, 'project_id': 'project', 'spec': {'key': {'variant': 'GPU'}}}
    with pytest.raises(APIError) as caught:
        if route == 'cuda':
            module.cuda_smoke(body, req, {'id': 'owner', 'role': 'admin'}, object())
        else:
            module.real_smoke('workflow', body, req, {'id': 'owner', 'role': 'admin'}, object())
    assert caught.value.code == 'ACCOUNT_REQUIRED'
