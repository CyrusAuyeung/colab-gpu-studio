import copy

from app.gpu.repository import Repository
from test_routes_draft import gpu_client, request_body  # noqa: F401


def test_batch_random_seed_replay_uses_original_snapshot(gpu_client):  # noqa: F811
    client, store, user, project, _ = gpu_client
    job = request_body(client, project)
    job['params']['seed'] = -1
    body = {'jobs': [job, copy.deepcopy(job)], 'mode': 'batch'}
    headers = {'Idempotency-Key': 'random-seed-replay'}
    first = client.post('/api/gpu/batches', json=body, headers=headers)
    assert first.status_code == 202, first.text
    original = [j['resolved_seed'] for j in Repository(store).list('jobs', user['id'])]
    second = client.post('/api/gpu/batches', json=body, headers=headers)
    assert second.status_code == 202, second.text
    assert first.json()['data']['id'] == second.json()['data']['id']
    assert original == [j['resolved_seed'] for j in Repository(store).list('jobs', user['id'])]
