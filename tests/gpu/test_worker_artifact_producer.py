import hashlib
import json

from app.gpu import runtime_worker
from app.gpu.artifacts import LocalArtifactStore


def test_worker_publishes_verified_manifest_without_inline_media(tmp_path, monkeypatch):
    root=tmp_path/'execution'
    root.mkdir()
    media=root/'outputs'/'large.mp4'
    media.parent.mkdir()
    # Real 150MiB disk file, streamed by the producer; no large Python bytes.
    with media.open('wb') as output:
        output.write(b'\x00\x00\x00\x18ftypisom')
        output.truncate(150*1024**2)
    outputs=[{'filename':'large.mp4','mime':'video/mp4','path':'outputs/large.mp4','size':media.stat().st_size}]
    monkeypatch.setattr(runtime_worker,'_snapshot',lambda payload: ({},{},{}))
    monkeypatch.setattr(runtime_worker,'_run_comfy',lambda *args: outputs)
    store=LocalArtifactStore(tmp_path/'objects')
    runtime_worker.run_job({'execution_id':'e','owner_id':'u'},root,artifact_store=store)
    meta=json.loads((root/'meta.json').read_text())
    assert 'outputs' not in meta
    item=meta['artifacts'][0]
    assert item['size_bytes']==150*1024**2
    assert item['upload_state']=='verified'
    assert store.verify_manifest(item,'u','e')[1].stat().st_size==150*1024**2
    status=json.loads((root/'status.json').read_text())
    assert status['state']=='completed'
    assert 'content' not in json.dumps(meta)


def test_file_publisher_resumes_existing_offset(tmp_path):
    store=LocalArtifactStore(tmp_path/'objects')
    source=tmp_path/'output.png'
    content=b'chunked-output'*1000
    source.write_bytes(content)
    item=store.begin('u','e',source.name,'image/png',len(content),hashlib.sha256(content).hexdigest())
    store.append(item['artifact_id'],'u','e',0,content[:5000])
    result=store.publish_file('u','e',source,'image/png')
    assert result['artifact_id']==item['artifact_id']
    assert result['upload_state']=='verified'


def test_worker_download_reads_bounded_chunks(tmp_path):
    class Stream:
        remaining=12*1024**2
        def read(self,size):
            assert 0<size<=1024**2
            count=min(size,self.remaining)
            self.remaining-=count
            return b'x'*count
    target=tmp_path/'video.mp4'
    assert runtime_worker._stream_output(Stream(),target,20*1024**2)==12*1024**2
    assert target.stat().st_size==12*1024**2


def test_worker_prep_json_can_use_manifest_store(tmp_path,monkeypatch):
    root=tmp_path/'prep'
    root.mkdir()
    (root/'report.json').write_text('{"mock":true,"status":"ready"}')
    monkeypatch.setattr(runtime_worker,'_snapshot',lambda payload: ({'prep':True},{},{}))
    monkeypatch.setattr(runtime_worker,'_run_prep',lambda *args:[{'path':'report.json','mime':'application/json'}])
    runtime_worker.run_job({'owner_id':'u','execution_id':'prep-e'},root,artifact_store=LocalArtifactStore(tmp_path/'objects'))
    meta=json.loads((root/'meta.json').read_text())
    assert meta['artifacts'][0]['mime']=='application/json'
    assert json.loads((root/'status.json').read_text())['state']=='completed'
