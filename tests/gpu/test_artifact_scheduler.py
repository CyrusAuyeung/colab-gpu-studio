import hashlib

from app.config import settings
from app.db import Store
from app.gpu.artifacts import LocalArtifactStore
from app.gpu.compute import MockColabProvider, MockGPUExecutor
from app.gpu.repository import Repository
from app.gpu.scheduler import Scheduler
from app.storage import asset_path


def test_manifest_persists_one_asset_without_base64(tmp_path):
    repo = Repository(Store("sqlite:///:memory:"))
    provider = MockColabProvider()
    job, _ = repo.create_job("owner", {"provider": "mock", "project_id": "p"}, "artifact-job")
    job = repo.update("jobs", job["id"], {"state": "running", "execution_id": "exec-1"})
    objects = LocalArtifactStore(settings.data_dir / "assets" / "artifacts")
    content = MockGPUExecutor().output
    item = objects.begin(
        "owner", "exec-1", "mock.png", "image/png", len(content), hashlib.sha256(content).hexdigest()
    )
    objects.append(item["artifact_id"], "owner", "exec-1", 0, content)

    # The executor's upload_state flag is deliberately untrusted; server verifies actual bytes.
    class Executor:
        def poll(self, handle, job):
            return {"state": "completed", "execution_id": "exec-1", "artifacts": [item]}

    scheduler = Scheduler(
        repo, provider_factory=lambda job: provider, executor_factory=lambda job: Executor()
    )
    handle = provider.create("owner", "artifact-request", job["id"], "artifact-runtime")
    repo.create("leases", "owner", {"owner_job_id": job["id"], "state": "active", "handle": handle})
    polled = scheduler._run(job)
    assert polled["state"] == "uploading", polled
    assert "content" not in str(polled["outputs"])
    first = scheduler._upload(polled)
    assert first["state"] == "cleaning_up", first
    second = scheduler._upload(polled)
    assert first["output_asset_ids"] == second["output_asset_ids"]
    assert len(repo.store.list("assets", "owner")) == 1
    asset = repo.store.list("assets", "owner")[0]
    assert asset_path(asset).read_bytes() == content
    assert asset["upload_state"] == "verified"
    assert asset["execution_id"] == "exec-1"
    assert asset["mock"] is True


def test_unverified_or_wrong_owner_output_never_completes():
    repo = Repository(Store("sqlite:///:memory:"))
    job, _ = repo.create_job("owner", {"provider": "mock", "project_id": "p"}, "bad-artifact")
    job = repo.update(
        "jobs",
        job["id"],
        {
            "state": "uploading",
            "execution_id": "exec-1",
            "outputs": [
                {
                    "artifact_id": "a" * 32,
                    "filename": "fake.mp4",
                    "upload_state": "verified",
                    "storage": {"provider": "local", "object_key": "a" * 32},
                }
            ],
        },
    )
    scheduler = Scheduler(repo)
    for _ in range(3):
        job = scheduler._upload(job)
    assert job["state"] != "completed"
    assert not repo.store.list("assets", "owner")


def test_json_report_is_verified_and_restricted_to_diagnostics(tmp_path):
    import pytest
    from app.gpu.artifacts import ArtifactError
    from app.gpu.output_protocol import commit_output
    repo=Repository(Store('sqlite://'))
    source=tmp_path/'report.json'
    source.write_text('{"mock":true}')
    objects=LocalArtifactStore(settings.data_dir/'assets'/'artifacts')
    item=objects.publish_file('owner','report-exec',source,'application/json')
    job={'id':'report-job','owner_id':'owner','execution_id':'report-exec','project_id':'p','provider':'mock'}
    with pytest.raises(ArtifactError,match='only allowed'):
        commit_output(repo.store,job,item,{})
    assert repo.store.list('assets','owner')==[]
    asset=commit_output(repo.store,{**job,'snapshot':{'prep':True}},item,{})
    assert asset['upload_state']=='verified'
    assert asset_path(asset).read_text()=='{"mock":true}'


def test_worker_to_gallery_manifest_closed_loop(tmp_path,monkeypatch):
    from app.gpu import runtime_worker
    root=tmp_path/'worker'
    root.mkdir()
    (root/'mock.png').write_bytes(MockGPUExecutor().output)
    monkeypatch.setattr(runtime_worker,'_snapshot',lambda payload: ({},{},{}))
    monkeypatch.setattr(runtime_worker,'_run_comfy',lambda *args:[{'path':'mock.png','mime':'image/png'}])
    objects=LocalArtifactStore(settings.data_dir/'assets'/'artifacts')
    repo=Repository(Store('sqlite://'))
    job,_=repo.create_job('owner',{'provider':'mock','project_id':'p'},'worker-gallery')
    job=repo.update('jobs',job['id'],{'state':'running','execution_id':'worker-gallery-e'})
    runtime_worker.run_job({'owner_id':'owner','execution_id':job['execution_id']},root,artifact_store=objects)
    import json
    manifest=json.loads((root/'meta.json').read_text())
    class Executor:
        def poll(self,handle,job):
            return {'state':'completed',**manifest}
    provider=MockColabProvider()
    handle=provider.create({},'worker-gallery-request',job['id'],'worker-gallery-runtime')
    repo.create('leases','owner',{'owner_job_id':job['id'],'state':'active','handle':handle})
    scheduler=Scheduler(repo,provider_factory=lambda job:provider,executor_factory=lambda job:Executor())
    job=scheduler._run(job)
    job=scheduler._upload(job)
    for _ in range(5):
        scheduler.tick()
    job=repo.get('jobs',job['id'],'owner')
    assert job['state']=='completed'
    assert job['runtime_deleted'] is True
    assert len(job['output_asset_ids'])==1
    asset=repo.store.get('assets',job['output_asset_ids'][0],'owner')
    assert asset['upload_state']=='verified'
    assert asset_path(asset).read_bytes()==MockGPUExecutor().output


def test_prep_manifest_reads_verified_report_into_job(tmp_path):
    import uuid
    repo=Repository(Store('sqlite://'))
    execution=str(uuid.uuid4())
    source=tmp_path/'prep-report.json'
    source.write_text('{"mock":true,"env":{"ready":true}}')
    objects=LocalArtifactStore(settings.data_dir/'assets'/'artifacts')
    item=objects.publish_file('owner',execution,source,'application/json')
    job=repo.create('jobs','owner',{'state':'uploading','prep':True,'execution_id':execution,'outputs':[item]})
    result=Scheduler(repo)._upload(job)
    assert result['state']=='cleaning_up'
    assert result['prep_report']=={'mock':True,'env':{'ready':True}}
    assert result['output_asset_ids']==[]


def test_manifest_streams_verified_file_to_configured_storage(tmp_path):
    source=tmp_path/'result.png'
    source.write_bytes(MockGPUExecutor().output)
    objects=LocalArtifactStore(settings.data_dir/'assets'/'artifacts')
    item=objects.publish_file('owner','cloud-e',source,'image/png')
    repo=Repository(Store('sqlite://'))
    job=repo.create('jobs','owner',{'state':'uploading','provider':'mock','execution_id':'cloud-e','outputs':[item]})
    seen=[]
    class Storage:
        def put(self,key,content,mime):
            return {'id':'metadata','sha256':hashlib.sha256(content).hexdigest()}
        def verify(self,record,content):
            return record['sha256']==hashlib.sha256(content).hexdigest()
        def put_file(self,key,path,mime):
            assert path.read_bytes()==MockGPUExecutor().output
            seen.append(key)
            return {'id':'offline-cloud-file','verified':True,'size':path.stat().st_size,'sha256':item['sha256']}
    result=Scheduler(repo,storage_factory=lambda _:Storage())._upload(job)
    assert result['state']=='cleaning_up'
    assert len(seen)==1
    asset=repo.store.get('assets',result['output_asset_ids'][0],'owner')
    assert asset['cloud_record']['id']=='offline-cloud-file'



def test_committed_cloud_asset_replay_does_not_need_cloud_transport(tmp_path):
    from app.gpu.output_protocol import commit_output
    source=tmp_path/'result.png'
    source.write_bytes(MockGPUExecutor().output)
    objects=LocalArtifactStore(settings.data_dir/'assets'/'artifacts')
    item=objects.publish_file('owner','replay-e',source,'image/png')
    repo=Repository(Store('sqlite://'))
    job={'id':'replay-job','owner_id':'owner','execution_id':'replay-e','provider':'mock'}
    class Storage:
        calls=0
        def put_file(self,key,path,mime):
            self.calls+=1
            if self.calls>1:
                raise RuntimeError('cloud unavailable')
            return {'id':'remote','verified':True,'size':item['size_bytes'],'sha256':item['sha256']}
    storage=Storage()
    first=commit_output(repo.store,job,item,{},storage)
    second=commit_output(repo.store,job,item,{},storage)
    assert first['id']==second['id'] and storage.calls==1
