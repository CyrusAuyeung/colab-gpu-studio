import base64
import hashlib
import json
import uuid

import pytest
from app.gpu.artifacts import LocalArtifactStore
from app.gpu.mcp import MCPGPUExecutor, MCPTransient, build_chunk_code


def test_remote_manifest_transfer_resumes_after_interruption(tmp_path,monkeypatch):
    execution=str(uuid.uuid4())
    content=b'bounded transfer' * 100000
    item={'filename':'result.mp4','path':'outputs/result.mp4','mime':'video/mp4',
          'size_bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}
    from app.config import settings
    monkeypatch.setattr(settings,'data_dir',tmp_path)
    objects=LocalArtifactStore(tmp_path/'assets'/'artifacts')
    def executor(fail):
        value=object.__new__(MCPGPUExecutor)
        value.owner_id='owner'
        value.artifact_store=objects
        value._session=lambda *args:'session'
        calls=[]
        def tool(name,args):
            code=args['code']
            calls.append(code)
            if len(calls)==1:
                return {'state':'completed','execution_id':execution,'artifacts':[item]}
            offset=objects.offset(objects.begin('owner',execution,item['filename'],item['mime'],len(content),item['sha256'])['artifact_id'],'owner',execution)
            if fail and offset:
                raise MCPTransient('offline interruption')
            chunk=content[offset:offset+1024**2]
            return {'execution_id':execution,'offset':offset,'content':base64.b64encode(chunk).decode()}
        value._tool=tool
        return value,calls
    job={'id':'job','execution_id':execution}
    from app.db import Store
    from app.gpu.repository import Repository
    from app.gpu.scheduler import Scheduler
    repo=Repository(Store('sqlite://'))
    job=repo.create('jobs','owner',{'state':'running','execution_id':execution})
    repo.create('leases','owner',{'owner_job_id':job['id'],'state':'active','handle':{}})
    first,_=executor(True)
    clock=[100.0]
    interrupted=Scheduler(repo,executor_factory=lambda _:first,clock=lambda:clock[0])._run(job)
    assert interrupted['state']=='running' and interrupted['poll_retry_count']==1
    second,calls=executor(False)
    clock[0]=interrupted['poll_retry_at']+1
    # Rebuilt scheduler/executor resume solely from durable DB and object bytes.
    recovered=Scheduler(repo,executor_factory=lambda _:second,clock=lambda:clock[0])._run(interrupted)
    assert recovered['state']=='uploading'
    assert recovered['poll_retry_count']==0 and recovered['poll_retry_at']==0
    assert recovered['execution_id']==execution
    result={'artifacts':recovered['outputs']}
    manifest=result['artifacts'][0]
    assert len(calls)==2
    verified,path=objects.verify_manifest(manifest,'owner',execution)
    assert path.read_bytes()==content and verified['upload_state']=='verified'


def test_generated_chunk_reader_rejects_escape(tmp_path):
    execution=str(uuid.uuid4())
    root=tmp_path/execution
    root.mkdir()
    (root/'meta.json').write_text(json.dumps({'execution_id':execution,'artifacts':[{'path':'../secret'}]}))
    (tmp_path/'secret').write_bytes(b'private')
    code=build_chunk_code(execution,0,0).replace('/content/ai-studio',str(tmp_path))
    with pytest.raises(ValueError):
        exec(compile(code,'trusted-reader','exec'),{})


def test_worker_cli_injects_manifest_store_and_removes_secrets(tmp_path,monkeypatch):
    from app.gpu import runtime_worker as worker
    execution=str(uuid.uuid4())
    payload=tmp_path/'payload.json'
    payload.write_text(json.dumps({'execution_dir':str(tmp_path),'execution_id':execution,
        'owner_id':'owner','output_transport':'mcp-chunks-v1','drive_cache':{'access_token':'fake-only'}}))
    monkeypatch.setattr(worker,'_snapshot',lambda _:({},{},{}))
    def generate(root,payload,*args):
        (root/'result.png').write_bytes(b'offline media')
        assert payload['_local_artifact_limit']==2*1024**3
        return [{'path':'result.png','mime':'image/png'}]
    monkeypatch.setattr(worker,'_run_comfy',generate)
    assert worker.main([str(payload)])==0
    meta=json.loads((tmp_path/'meta.json').read_text())
    assert meta['artifacts'][0]['sha256']==hashlib.sha256(b'offline media').hexdigest()
    assert 'content' not in meta['artifacts'][0]
    assert 'drive_cache' not in json.loads(payload.read_text())



def test_generated_reader_returns_exact_bounded_bytes(tmp_path,capsys):
    execution=str(uuid.uuid4())
    root=tmp_path/execution
    root.mkdir()
    content=b'x'*(1024**2)+b'end'
    (root/'result.mp4').write_bytes(content)
    (root/'meta.json').write_text(json.dumps({'execution_id':execution,'artifacts':[{'path':'result.mp4'}]}))
    code=build_chunk_code(execution,0,1024**2).replace('/content/ai-studio',str(tmp_path))
    exec(compile(code,'trusted-reader','exec'),{})
    value=json.loads(capsys.readouterr().out)
    assert base64.b64decode(value['content'])==b'end'
    assert value['offset']==1024**2 and value['execution_id']==execution



def test_cli_to_mcp_reader_to_private_gallery(tmp_path,monkeypatch):
    import contextlib
    import io

    from app.config import settings
    from app.db import Store
    from app.gpu import runtime_worker as worker
    from app.gpu.compute import MockGPUExecutor
    from app.gpu.repository import Repository
    from app.gpu.scheduler import Scheduler
    from app.storage import asset_path
    execution=str(uuid.uuid4())
    remote=tmp_path/'remote'
    root=remote/execution
    root.mkdir(parents=True)
    payload=root/'payload.json'
    payload.write_text(json.dumps({'execution_dir':str(root),'execution_id':execution,
        'owner_id':'owner','output_transport':'mcp-chunks-v1'}))
    monkeypatch.setattr(worker,'_snapshot',lambda _:({},{},{}))
    def generate(*args):
        (root/'result.png').write_bytes(MockGPUExecutor().output)
        return [{'path':'result.png','mime':'image/png'}]
    monkeypatch.setattr(worker,'_run_comfy',generate)
    assert worker.main([str(payload)])==0
    value=object.__new__(MCPGPUExecutor)
    value.owner_id='owner'
    value.artifact_store=LocalArtifactStore(settings.data_dir/'assets'/'artifacts')
    value._session=lambda *args:'offline-session'
    def tool(name,args):
        output=io.StringIO()
        code=args['code'].replace('/content/ai-studio',str(remote))
        with contextlib.redirect_stdout(output):
            exec(compile(code,'trusted-generated-code','exec'),{})
        return json.loads(output.getvalue())
    value._tool=tool
    repo=Repository(Store('sqlite://'))
    job=repo.create('jobs','owner',{'state':'running','provider':'mock','execution_id':execution})
    result=value.poll({},job)
    class Executor:
        def poll(self,*args):
            return result
    scheduler=Scheduler(repo,executor_factory=lambda _:Executor())
    repo.create('leases','owner',{'owner_job_id':job['id'],'state':'active','handle':{}})
    job=scheduler._upload(scheduler._run(job))
    assert job['state']=='cleaning_up'
    asset=repo.store.get('assets',job['output_asset_ids'][0],'owner')
    assert asset['upload_state']=='verified'
    assert asset_path(asset).read_bytes()==MockGPUExecutor().output



def test_gif_descriptor_can_be_verified_and_committed(tmp_path):
    from app.config import settings
    from app.db import Store
    from app.gpu.output_protocol import commit_output
    from PIL import Image
    source=tmp_path/'animated.gif'
    Image.new('RGB',(2,2),'red').save(source,format='GIF')
    objects=LocalArtifactStore(settings.data_dir/'assets'/'artifacts')
    item=objects.publish_file('owner','gif-execution',source,'image/gif')
    asset=commit_output(Store('sqlite://'),{'id':'gif-job','owner_id':'owner','execution_id':'gif-execution'},item,{})
    assert asset['mime']=='image/gif' and asset['upload_state']=='verified'
