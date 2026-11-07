from test_v020_instance_routes import FakeInstanceService, FakeRepo, _client
from test_v020_workbench import MemoryRepo, _service


def test_graph_http_uses_top_level_identity_with_real_workbench_service():
    wb, row = _service(MemoryRepo())
    client = _client(FakeInstanceService({row['id']: row}), FakeRepo([row]))
    client.app.state.workbench_service = wb
    response = client.post('/api/gpu/instances/instance-1/workbench/actions', json={
        'action': 'get_reviewed_graph', 'workflow_key': 'demo-mock', 'workflow_version': 1,
    })
    assert response.status_code == 200
    assert response.json()['data']['graph']['1']['class_type'] == 'Prompt'
