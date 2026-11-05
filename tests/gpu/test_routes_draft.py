"""Atelier 新旧任务契约隔离；测试只运行 Mock，不访问收费 GPU。"""

import copy
import time

import pytest
from app.config import settings
from app.db import Store
from app.main import app
from app.security import sign_session
from fastapi.testclient import TestClient


@pytest.fixture
def gpu_client(tmp_path, monkeypatch):
    st = Store("sqlite:///" + str(tmp_path / "gpu-api.db"))
    user = st.create("users", "bootstrap", {"username": "gpu-admin", "role": "admin"})
    project = st.create("projects", user["id"], {"title": "GPU验收项目"})
    app.state.store = st
    monkeypatch.setattr(settings, "data_dir", tmp_path / "media")
    monkeypatch.setenv("GPU_REAL_ENABLED", "0")
    monkeypatch.setenv("GPU_STORAGE", "local")
    monkeypatch.setattr(settings, "studio_password", "test-long-login-password")
    monkeypatch.setattr(settings, "session_secret", "test-session-private-signing-key")
    from cryptography.fernet import Fernet

    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    with TestClient(app) as c:
        token = sign_session(
            {"uid": user["id"], "role": "admin", "iat": int(time.time()), "exp": time.time() + 3600}
        )
        c.headers["Authorization"] = "Bearer " + token
        yield c, st, user, project, {}


def request_body(client, project):
    workflows = client.get("/api/gpu/workflows").json()["data"]
    w = next(w for w in workflows if w["state"] == "published" and w["active"])
    return {
        "project_id": project["id"],
        "model_id": w["model_id"],
        "workflow_key": w["workflow_key"],
        "workflow_version": w["workflow_version"],
        "params": {"prompt": "明确标识为Mock的管线测试"},
        "asset_ids": [],
        "execution_policy": {"provider": "mock", "shutdown_after_job": True, "max_runtime_minutes": 5},
    }


def test_models_require_existing_login(gpu_client):
    c, st, user, project, h = gpu_client
    assert c.get("/api/gpu/models").status_code == 200
    c.headers.pop("Authorization")
    assert c.get("/api/gpu/models").status_code == 401


def test_duplicate_mobile_requests_snapshot_and_project_outputs(gpu_client):
    from app.gpu.repository import Repository
    from app.gpu.scheduler import Scheduler

    c, st, user, project, h = gpu_client
    body = request_body(c, project)
    headers = {**h, "Idempotency-Key": "phone-retry-1"}
    responses = [c.post("/api/gpu/jobs", json=body, headers=headers) for _ in range(5)]
    assert all(r.status_code == 202 for r in responses), [r.text for r in responses]
    ids = {r.json()["data"]["id"] for r in responses}
    assert len(ids) == 1
    jid = ids.pop()
    repo = Repository(st)
    snapshot = copy.deepcopy(repo.get("jobs", jid)["snapshot"])
    for _ in range(22):
        Scheduler(st).tick()
        state = c.get("/api/gpu/jobs/" + jid).json()["data"]["state"]
        if state in {"completed", "failed", "cancelled"}:
            break
    job = c.get("/api/gpu/jobs/" + jid).json()["data"]
    assert job["state"] == "completed", job
    assert repo.get("jobs", jid)["snapshot"] == snapshot
    assert all(lease["state"] == "deleted" for lease in repo.list("leases"))
    outputs = c.get("/api/gpu/jobs/" + jid + "/outputs").json()["data"]
    assert outputs
    assets = st.list("assets", user["id"], project["id"])
    assert assets and assets[0].get("mock") is True
    assert c.get("/api/assets/" + assets[0]["id"] + "/download").status_code == 200
    changed = copy.deepcopy(body)
    changed["params"]["prompt"] = "changed"
    assert c.post("/api/gpu/jobs", json=changed, headers=headers).status_code == 409


def test_invalid_params_and_foreign_project_rejected_before_allocation(gpu_client):
    from app.gpu.repository import Repository

    c, st, user, project, h = gpu_client
    body = request_body(c, project)
    body["params"]["nonexistent_cfg"] = 100
    assert (
        c.post("/api/gpu/jobs", json=body, headers={**h, "Idempotency-Key": "bad-param"}).status_code == 422
    )
    body = request_body(c, project)
    body["project_id"] = st.create("projects", "other-owner", {"title": "private"})["id"]
    assert (
        c.post("/api/gpu/jobs", json=body, headers={**h, "Idempotency-Key": "bad-owner"}).status_code == 404
    )
    assert not Repository(st).list("leases")


def test_cancel_queue_csrf_and_real_gpu_fail_closed(gpu_client):
    c, st, user, project, h = gpu_client
    body = request_body(c, project)
    assert c.post("/api/gpu/jobs", json=body).status_code == 422
    r = c.post("/api/gpu/jobs", json=body, headers={**h, "Idempotency-Key": "cancel"})
    jid = r.json()["data"]["id"]
    assert c.post("/api/gpu/jobs/" + jid + "/cancel", headers=h).json()["data"]["state"] == "cancelled"
    body["execution_policy"]["provider"] = "colab"
    r = c.post("/api/gpu/jobs", json=body, headers={**h, "Idempotency-Key": "real-disabled"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "REAL_GPU_DISABLED"


def test_agent_cannot_publish_or_change_budget(gpu_client, monkeypatch):
    c, st, user, project, h = gpu_client
    monkeypatch.setenv("GPU_AGENT_TOKEN", "test-agent-private-key")
    monkeypatch.setenv("GPU_AGENT_OWNER_ID", user["id"])
    c.headers.pop("Authorization")
    auth = {"Authorization": "Bearer test-agent-private-key"}
    r = c.post(
        "/api/gpu/agent/invoke", json={"tool": "budget.set", "arguments": {"daily": 999}}, headers=auth
    )
    assert r.status_code == 403
    r = c.post(
        "/api/gpu/agent/invoke",
        json={"tool": "workflow.publish", "arguments": {"confirmed": True}},
        headers=auth,
    )
    assert r.status_code == 403
    r = c.post("/api/gpu/agent/invoke", json={"tool": "usage.inspect", "arguments": {}}, headers=auth)
    assert r.status_code == 200
    assert "test-agent-private-key" not in r.text


def test_batch_http_idempotency_validation_and_owner_scope(gpu_client):
    from app.gpu.repository import Repository
    c,st,user,project,h=gpu_client
    job=request_body(c,project)
    body={'jobs':[job]*3,'mode':'batch'}
    headers={'Idempotency-Key':'http-batch'}
    first=c.post('/api/gpu/batches',json=body,headers=headers)
    assert first.status_code==202,first.text
    bid=first.json()['data']['id']
    replay=c.post('/api/gpu/batches',json=body,headers=headers)
    assert replay.status_code==202 and replay.json()['data']['id']==bid
    assert len(Repository(st).list('jobs',user['id']))==3
    changed=copy.deepcopy(body)
    changed['jobs'][0]['params']['prompt']='changed'
    assert c.post('/api/gpu/batches',json=changed,headers=headers).status_code==409
    summary=c.get('/api/gpu/batches/'+bid)
    assert summary.status_code==200 and len(summary.json()['data']['jobs'])==3
    assert c.post('/api/gpu/batches/'+bid+'/cancel').status_code==200
    assert all(j['state']=='cancelled' for j in Repository(st).list('jobs',user['id']))
    assert c.get('/api/gpu/batches/missing').status_code==404
    c.headers.pop('Authorization')
    assert c.post('/api/gpu/batches',json=body,headers=headers).status_code==401


def test_invalid_batch_is_atomic(gpu_client):
    from app.gpu.repository import Repository
    c,st,user,project,h=gpu_client
    valid=request_body(c,project)
    invalid=copy.deepcopy(valid)
    invalid['params']['unknown']=1
    response=c.post('/api/gpu/batches',json={'jobs':[valid,invalid]},headers={'Idempotency-Key':'bad-batch'})
    assert response.status_code==422
    assert not Repository(st).list('jobs',user['id'])


def test_batch_query_reports_terminal_aggregate(gpu_client):
    from app.gpu.repository import Repository
    client,store,user,project,_=gpu_client
    response=client.post('/api/gpu/batches',json={'jobs':[request_body(client,project)]},headers={'Idempotency-Key':'terminal-aggregate'})
    batch=response.json()['data']
    repo=Repository(store)
    job=next(j for j in repo.list('jobs',user['id']) if j.get('batch_id')==batch['id'])
    repo.update('jobs',job['id'],{'state':'completed'},user['id'])
    data=client.get('/api/gpu/batches/'+batch['id']).json()['data']
    assert data['state']=='completed'
    assert data['finished_jobs']==1


def test_batch_child_can_retry_through_existing_endpoint(gpu_client):
    from app.gpu.repository import Repository
    client,store,user,project,_=gpu_client
    response=client.post('/api/gpu/batches',json={'jobs':[request_body(client,project)]},headers={'Idempotency-Key':'retry-batch'})
    batch=response.json()['data']
    repo=Repository(store)
    job=next(j for j in repo.list('jobs',user['id']) if j.get('batch_id')==batch['id'])
    repo.update('jobs',job['id'],{'state':'failed'},user['id'])
    response=client.post('/api/gpu/jobs/'+job['id']+'/retry',headers={'Idempotency-Key':'retry-child'})
    assert response.status_code==202,response.text
    assert response.json()['data']['id']!=job['id']


def test_normal_admission_does_not_use_second_snapshot_transaction(gpu_client,monkeypatch):
    from app.gpu.repository import Repository
    client,store,user,project,_=gpu_client
    original=Repository.update
    def reject_snapshot_update(self,kind,key,patch,owner_id=None):
        if kind=='jobs' and 'snapshot' in patch:
            raise RuntimeError('separate snapshot update is forbidden')
        return original(self,kind,key,patch,owner_id)
    monkeypatch.setattr(Repository,'update',reject_snapshot_update)
    response=client.post('/api/gpu/jobs',json=request_body(client,project),headers={'Idempotency-Key':'atomic-admission'})
    assert response.status_code==202,response.text
    job=Repository(store).get('jobs',response.json()['data']['id'],user['id'])
    assert job['snapshot']['resolved_params']==job['params']


def test_retry_freezes_resolved_seed_and_keeps_original_evidence(gpu_client):
    from app.gpu.repository import Repository
    client,store,user,project,_=gpu_client
    body=request_body(client,project)
    body['params']['seed']=-1
    original=client.post('/api/gpu/jobs',json=body,headers={'Idempotency-Key':'seed-original'}).json()['data']
    repo=Repository(store)
    repo.update('jobs',original['id'],{'state':'failed'},user['id'])
    response=client.post('/api/gpu/jobs/'+original['id']+'/retry',headers={'Idempotency-Key':'seed-retry'})
    assert response.status_code==202,response.text
    retried=repo.get('jobs',response.json()['data']['id'],user['id'])
    assert retried['resolved_seed']==original['resolved_seed']
    assert retried['retry_of']==original['id']
    assert repo.get('jobs',original['id'],user['id'])['state']=='failed'


def test_system_retry_is_explicitly_rejected_without_ordinary_admission(gpu_client):
    from app.gpu.repository import Repository
    client,store,user,project,_=gpu_client
    job,_=Repository(store).create_job(user['id'],{'kind':'system-cuda-smoke'},'system-fixture')
    Repository(store).update('jobs',job['id'],{'state':'failed'},user['id'])
    response=client.post('/api/gpu/jobs/'+job['id']+'/retry',headers={'Idempotency-Key':'no-system-bypass'})
    assert response.status_code==409
    assert response.json()['error']['code']=='SPECIAL_JOB_RETRY_REQUIRED'


def test_workflow_smoke_requires_special_retry(gpu_client):
    from app.gpu.repository import Repository
    client,store,user,project,_=gpu_client
    job=client.post('/api/gpu/jobs',json=request_body(client,project),headers={'Idempotency-Key':'wf-smoke-fixture'}).json()['data']
    Repository(store).update('jobs',job['id'],{'state':'failed','snapshot':{'workflow_smoke':True}},user['id'])
    response=client.post('/api/gpu/jobs/'+job['id']+'/retry',headers={'Idempotency-Key':'wf-no-bypass'})
    assert response.status_code==409


def test_attempt_endpoint_is_same_job_and_idempotent(gpu_client):
    from app.gpu.repository import Repository
    client,store,user,project,_=gpu_client
    job=client.post('/api/gpu/jobs',json=request_body(client,project),headers={'Idempotency-Key':'attempt-fixture'}).json()['data']
    Repository(store).update('jobs',job['id'],{'state':'failed'},user['id'])
    url='/api/gpu/jobs/'+job['id']+'/attempts'
    for _ in range(2):
        response=client.post(url,headers={'Idempotency-Key':'same-attempt'})
        assert response.status_code==202
        assert response.json()['data']['id']==job['id']
    assert client.get(url).status_code==200
