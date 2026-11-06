"""Offline verification for the accompanying documentation and runnable client."""
import copy
import importlib.util
import io
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parents[1]
ROOT = Path(os.getenv("DOCS_SOURCE_ROOT", str(DOCS_DIR.parent)))
TEMP = tempfile.TemporaryDirectory(prefix="studio-api-docs-")
os.environ.update(STUDIO_OFFLINE_TEST="1", ENVIRONMENT="offline-test", GPU_REAL_ENABLED="0",
                  GPU_STORAGE="local", DATABASE_URL="sqlite:///:memory:", DATA_DIR=TEMP.name,
                  STUDIO_API_TOKEN="offline-test-token-not-a-secret", STUDIO_PASSWORD="offline-test-password",
                  SESSION_SECRET="offline-test-session-key")
sys.path.insert(0, str(ROOT / "api"))

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.db import Store
from app.gpu.inputs import resolve_input_images
from app.gpu.manifest import resolve_parameters
from app.gpu.repository import Repository
from app.gpu.scheduler import Scheduler
from app.main import app

NETWORK_ATTEMPTS = []

def deny(*args, **kwargs):
    NETWORK_ATTEMPTS.append(True)
    raise AssertionError("Offline verification forbids outbound network")

socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.create_connection = deny
httpx.HTTPTransport.handle_request = deny
httpx.AsyncHTTPTransport.handle_async_request = deny

spec = importlib.util.spec_from_file_location("doc_client", DOCS_DIR / "examples/studio_api_example.py")
assert spec is not None and spec.loader is not None
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)
app.state.store = Store("sqlite:///:memory:")
repo = Repository(app.state.store)
checks = []

def check(label, condition):
    assert condition, label
    checks.append(label)

with TestClient(app) as client:
    check("health version", client.get('/api/health').json()['version'] == '0.1.31-dev.8')
    check("missing auth rejected", client.get('/api/gpu/projects').status_code == 401)
    login = client.post('/api/auth/login', json={'password': 'offline-test-password'})
    check("login returns direct access_token", 'access_token' in login.json() and 'data' not in login.json())
    check("cookie GET accepted", client.get('/api/gpu/projects').status_code == 200)
    check("cookie write rejected", client.post('/api/gpu/projects', json={'title':'denied'}).status_code == 401)
    client.headers['Authorization'] = 'Bearer offline-test-token-not-a-secret'
    check("api me is direct object", client.get('/api/me').json()['id'] == 'owner')
    state_path = Path(TEMP.name) / 'state.json'
    state = example.submit(client, state_path, 'http://testserver', 'demo-mock',
                           {'prompt':'Document integration Mock', 'steps':4, 'seed':42})
    check("example submits batch", bool(state['batch_id']) and len(state['job_ids']) == 1)
    original_count = len(repo.list('jobs','owner'))
    resumed = example.submit(client,state_path,'http://testserver','ignored',{})
    check("example resume creates no duplicate", resumed == state and len(repo.list('jobs','owner')) == original_count)
    replay = client.post('/api/gpu/batches', json=state['body'], headers={'Idempotency-Key':state['idempotency_key']})
    check("batch replay returns same id", replay.status_code == 202 and replay.json()['data']['id'] == state['batch_id'])
    check("POST batch has job_ids not expanded jobs", 'job_ids' in replay.json()['data'] and 'jobs' not in replay.json()['data'])
    changed = copy.deepcopy(state['body'])
    changed['jobs'][0]['params']['seed'] = 43
    conflict = client.post('/api/gpu/batches', json=changed,headers={'Idempotency-Key':state['idempotency_key']})
    check("changed payload conflicts", conflict.status_code == 409 and conflict.json()['error']['code'] == 'IDEMPOTENCY_CONFLICT')
    missing = client.post('/api/gpu/jobs', json=state['body']['jobs'][0])
    check("job idempotency required", missing.json()['error']['code'] == 'IDEMPOTENCY_REQUIRED')
    job_response = client.post('/api/gpu/jobs', json=state['body']['jobs'][0], headers={'Idempotency-Key':'single-mock-docs'})
    check("single Mock submission 202", job_response.status_code == 202)
    bad_origin = client.post('/api/gpu/projects',json={'title':'cross'},headers={'Origin':'https://other.invalid'})
    check("foreign Origin rejected", bad_origin.status_code == 403)
    scheduler = Scheduler(app.state.store)
    for _ in range(160):
        scheduler.tick()
        if all(j['state'] in {'completed','failed','cancelled'} for j in repo.list('jobs','owner')):
            break
    check("Mock jobs completed", all(j['state']=='completed' for j in repo.list('jobs','owner')))
    paths = example.wait_and_download(client,state,Path(TEMP.name)/'downloads',interval=0,timeout=10)
    check("example verifies and saves Mock output", bool(paths) and all(Path(p).stat().st_size > 0 for p in paths))
    check("all Mock leases deleted", all(l['state']=='deleted' for l in repo.list('leases','owner')))
    project = state['body']['jobs'][0]['project_id']
    image = io.BytesIO()
    Image.new('RGB',(64,64),(30,60,90)).save(image,format='PNG')
    uploaded = client.post('/api/assets', data={'project_id':project},files={'file':('reference.png',image.getvalue(),'image/png')})
    check("multipart image upload 201", uploaded.status_code == 201)
    aid = uploaded.json()['data']['id']
    manifest = json.loads((ROOT/'api/app/gpu/templates/builtin/minimax-h3-ref2v-1.4.0.json').read_text())['manifest']
    resolved = resolve_parameters(manifest, {'ref_1':aid,'prompt':'<Picture 1> turns their head. Audio: wind.',
                                           'mode':'fast8','width':544,'height':960,'length':124,'seed':42})
    bound, files = resolve_input_images(lambda x: app.state.store.get('assets',x,'owner'), project,manifest,resolved)
    check("H3 request parameters resolve locally", resolved['mode']=='fast8' and resolved['length']==124)
    check("H3 ref_1 binds to JPEG input", bound['ref_1']=='input_ref_1.jpg' and len(files)==1)
    client.cookies.clear()
    auth = client.headers.pop('Authorization')
    check("download requires authentication", client.get(f'/api/assets/{aid}/download').status_code==401)
    client.headers['Authorization'] = auth
    check("authenticated source download", client.get(f'/api/assets/{aid}/download').content==image.getvalue())
    fake_real = copy.deepcopy(state['body'])
    fake_real['jobs'][0]['execution_policy']['provider'] = 'colab'
    real_rejected = client.post('/api/gpu/batches',json=fake_real,headers={'Idempotency-Key':'disabled-real-docs'})
    check("offline real batch fails closed", real_rejected.status_code==403 and real_rejected.json()['error']['code']=='REAL_GPU_DISABLED')
    schema=app.openapi()
    check("OpenAPI batch schema exists", 'requestBody' in schema['paths']['/api/gpu/batches']['post'])
    output_dir=DOCS_DIR/'api-evidence'
    output_dir.mkdir(exist_ok=True)
    (output_dir/'openapi.json').write_text(json.dumps(schema,ensure_ascii=False,indent=2),encoding='utf-8')

check("zero outbound network attempts", len(NETWORK_ATTEMPTS)==0)
report={'application_version':'0.1.31-dev.8','source_commit':'7b91190bed6fe5cea02d92c6ff039f64c7ebe497',
        'verification':'isolated local Mock, TestClient, real network denied', 'passed_checks':checks,
        'passed_count':len(checks),'real_network_attempts':len(NETWORK_ATTEMPTS),
        'paid_gpu_started':False,'production_accessed':False,
        'not_verified':['production deployment','Google OAuth','real model generation','actual billing','real runtime deletion']}
(output_dir/'验证记录.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
