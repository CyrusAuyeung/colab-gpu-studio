from test_v020_instance_routes import FakeInstanceService, FakeRepo, _client


def test_workbench_accepts_idempotency_header_and_maps_domain_error():
    from app.gpu.workbench import WorkbenchError
    class Wb:
        def execute(self, owner, instance, action, payload, key):
            assert key == 'stable-key'
            assert payload == {'params': {}}
            raise WorkbenchError('COMFY_UNVERIFIED', '未验证', 503, True)
    row = {'id': 'i1', 'owner_id': 'owner', 'state': 'retained_idle'}
    service = FakeInstanceService({'i1': row})
    client = _client(service, FakeRepo([row]))
    client.app.state.workbench_service = Wb()
    response = client.post('/api/gpu/instances/i1/workbench/actions',
                           json={'action': 'set_reviewed_params', 'params': {}},
                           headers={'Idempotency-Key': 'stable-key'})
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'COMFY_UNVERIFIED'
