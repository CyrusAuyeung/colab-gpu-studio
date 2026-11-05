"""500MiB synthetic file; execute actual generated readers, interrupt at 60%."""
import contextlib
import hashlib
import io
import json
import re
import uuid

from app.config import settings
from app.db import Store
from app.gpu.artifacts import LocalArtifactStore
from app.gpu.mcp import MCPGPUExecutor, MCPTransient
from app.gpu.repository import Repository
from app.gpu.scheduler import Scheduler


def test_500mib_generated_reader_resumes_at_exact_sixty_percent(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'data_dir', tmp_path / 'local')
    execution = str(uuid.uuid4())
    remote = tmp_path / 'remote'
    root = remote / execution
    root.mkdir(parents=True)
    source = root / 'fake.mp4'
    size = 500 * 1024**2
    cutoff = size * 3 // 5
    with source.open('wb') as file:
        file.write(b'\x00\x00\x00\x18ftypisom')
        file.truncate(size)
    digest = hashlib.sha256()
    with source.open('rb') as file:
        while part := file.read(1024**2):
            digest.update(part)
    item = {'filename': source.name, 'path': source.name, 'mime': 'video/mp4',
            'size_bytes': size, 'sha256': digest.hexdigest()}
    (root / 'meta.json').write_text(json.dumps({'execution_id': execution, 'artifacts': [item]}))
    (root / 'status.json').write_text(json.dumps({'state': 'completed'}))
    url = 'sqlite:///' + str(tmp_path / 'jobs.db')
    repo = Repository(Store(url))
    job = repo.create('jobs', 'owner', {'state': 'running', 'execution_id': execution, 'provider': 'mock'})
    repo.create('leases', 'owner', {'owner_job_id': job['id'], 'state': 'active', 'handle': {}})
    reads = [[], []]
    def executor(phase):
        value = object.__new__(MCPGPUExecutor)
        value.owner_id = 'owner'
        value.artifact_store = LocalArtifactStore(settings.data_dir / 'assets' / 'artifacts')
        value._session = lambda *args: 'offline-session'
        def tool(name, args):
            code = args['code'].replace('/content/ai-studio', str(remote))
            match = re.search(r'_f\.seek\((\d+)\)', code)
            if match:
                offset = int(match[1])
                if phase == 0 and offset == cutoff:
                    raise MCPTransient('synthetic disconnect at 60 percent')
                reads[phase].append(offset)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exec(compile(code, 'trusted-offline-reader', 'exec'), {})
            return json.loads(output.getvalue())
        value._tool = tool
        return value
    clock = [100.0]
    first = executor(0)
    interrupted = Scheduler(repo, executor_factory=lambda _: first, clock=lambda: clock[0])._run(job)
    assert interrupted['state'] == 'running' and interrupted['poll_retry_count'] == 1
    objects = first.artifact_store
    local_item = objects.begin('owner', execution, source.name, item['mime'], size, item['sha256'])
    assert objects.offset(local_item['artifact_id'], 'owner', execution) == cutoff
    repo = Repository(Store(url))
    clock[0] = interrupted['poll_retry_at'] + 1
    second = executor(1)
    scheduler = Scheduler(repo, executor_factory=lambda _: second, clock=lambda: clock[0])
    resumed = scheduler._run(repo.get('jobs', job['id'], 'owner'))
    assert resumed['state'] == 'uploading'
    assert reads[0] == list(range(0, cutoff, 1024**2))
    assert reads[1] == list(range(cutoff, size, 1024**2))
    persisted = scheduler._upload(resumed)
    assert persisted['state'] == 'cleaning_up'
    asset = repo.store.get('assets', persisted['output_asset_ids'][0], 'owner')
    assert asset['size'] == size and asset['sha256'] == item['sha256']
    assert asset['upload_state'] == 'verified'
