import hashlib

import httpx
import pytest
from app.db import Store
from app.gpu.drive import DriveError, DriveStorage
from app.gpu.repository import Repository


class FakeGoogle:
    def access_token(self, purpose):
        assert purpose == 'drive'
        return 'offline-fake-access'


class FileStream(httpx.SyncByteStream):
    def __init__(self, source):
        self.source = source

    def __iter__(self):
        with self.source.open('rb') as source:
            while part := source.read(64 * 1024):
                yield part


def storage_for(repo, source, handler):
    storage = DriveStorage({'id': 'job', 'owner_id': 'u'}, repo,
        google=FakeGoogle(), client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _: None)
    storage._job_parent = lambda key: 'fake-parent'
    storage._find_existing_file = lambda *args: None
    return storage


def test_file_upload_recovers_persisted_session_after_interruption(tmp_path):
    source = tmp_path / 'clip.mp4'
    source.write_bytes(b'01234567' * (2 * 1024 * 1024))
    total = source.stat().st_size
    repo = Repository(Store('sqlite:///' + str(tmp_path / 'upload.db')))
    state = {'offset': 0, 'init': 0, 'broken': True, 'ranges': []}

    def handler(request):
        if request.method == 'POST':
            state['init'] += 1
            return httpx.Response(200, headers={'Location': 'https://www.googleapis.com/upload/drive/v3/files?upload_id=offline-session'})
        if request.method == 'GET':
            return httpx.Response(200, stream=FileStream(source))
        value = request.headers['Content-Range']
        state['ranges'].append(value)
        if value.startswith('bytes */'):
            return httpx.Response(308, headers={'Range': f"bytes=0-{state['offset']-1}"} if state['offset'] else {})
        assert len(request.content) <= 8 * 1024 * 1024
        if state['broken'] and state['offset']:
            raise httpx.ReadError('offline interruption', request=request)
        start = int(value.split()[1].split('-')[0])
        assert start == state['offset']
        state['offset'] += len(request.content)
        if state['offset'] < total:
            return httpx.Response(308, headers={'Range': f"bytes=0-{state['offset']-1}"})
        return httpx.Response(201, json={'id': 'remote-file', 'size': str(total)})

    first = storage_for(repo, source, handler)
    with pytest.raises(DriveError):
        first.put_file('outputs/job/clip.mp4', source, 'video/mp4')
    assert state['offset'] == 8 * 1024 * 1024
    state['broken'] = False
    second = storage_for(Repository(Store('sqlite:///' + str(tmp_path / 'upload.db'))), source, handler)
    result = second.put_file('outputs/job/clip.mp4', source, 'video/mp4')
    assert result['verified'] is True
    assert state['init'] == 1
    assert state['offset'] == total
    assert not any('offline-session' in str(row) for row in repo.list('files', 'u') if row.get('kind') == 'drive_file')
    before = state['init']
    assert second.put_file('outputs/job/clip.mp4', source, 'video/mp4')['id'] == result['id']
    assert state['init'] == before


def test_file_upload_rejects_symlink_before_transport(tmp_path):
    source = tmp_path / 'data'
    source.write_bytes(b'data')
    link = tmp_path / 'link'
    link.symlink_to(source)
    repo = Repository(Store('sqlite://'))
    storage = storage_for(repo, source, lambda _: pytest.fail('unexpected transport'))
    with pytest.raises(DriveError):
        storage.put_file('outputs/job/file', link, 'video/mp4')


@pytest.mark.parametrize('size_mib', [150, 500])
def test_large_file_streams_without_whole_file_bytes(tmp_path, size_mib):
    source = tmp_path / 'large.mp4'
    with source.open('wb') as file:
        file.write(b'\x00\x00\x00\x18ftypisom')
        file.truncate(size_mib * 1024**2)
    total = source.stat().st_size
    repo = Repository(Store('sqlite://'))
    state = {'offset': 0, 'max_chunk': 0}

    def handler(request):
        if request.method == 'POST':
            return httpx.Response(200, headers={'Location': 'https://www.googleapis.com/upload/drive/v3/files?upload_id=fake'})
        if request.method == 'GET':
            return httpx.Response(200, stream=FileStream(source))
        value = request.headers['Content-Range']
        if value.startswith('bytes */'):
            return httpx.Response(308)
        start = int(value.split()[1].split('-')[0])
        assert start == state['offset']
        state['max_chunk'] = max(state['max_chunk'], len(request.content))
        state['offset'] += len(request.content)
        if state['offset'] < total:
            return httpx.Response(308, headers={'Range': f"bytes=0-{state['offset']-1}"})
        return httpx.Response(201, json={'id': 'remote-large', 'size': str(total)})

    result = storage_for(repo, source, handler).put_file('outputs/job/large.mp4', source, 'video/mp4')
    assert result['verified'] and result['size'] == total
    assert state['offset'] == total and state['max_chunk'] <= 8 * 1024**2
    digest = hashlib.sha256()
    with source.open('rb') as file:
        while chunk := file.read(1024**2):
            digest.update(chunk)
    assert result['sha256'] == digest.hexdigest()


@pytest.mark.parametrize('status', [404, 410])
def test_expired_session_recovers_completed_remote_before_new_upload(tmp_path, status):
    from app.security import encrypt_secret
    source=tmp_path/'result.mp4'
    source.write_bytes(b'completed output')
    repo=Repository(Store('sqlite://'))
    key='outputs/job/result.mp4'
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    kh=hashlib.sha256(key.encode()).hexdigest()
    sid=hashlib.sha256(f'upload:u:{kh}:{sha}'.encode()).hexdigest()
    repo.create('files','u',{'kind':'drive_upload_session','state':'uploading',
        'encrypted_session':encrypt_secret('https://www.googleapis.com/upload/drive/v3/files?upload_id=expired'),
        'size':source.stat().st_size,'sha256':sha,'key_hash':kh},record_id=sid)
    calls=[]
    def handler(request):
        calls.append(request.method)
        if request.method=='PUT':
            return httpx.Response(status)
        if request.method=='GET':
            return httpx.Response(200,stream=FileStream(source))
        pytest.fail('completed remote file must not be re-uploaded')
    storage=storage_for(repo,source,handler)
    storage._find_existing_file=lambda *args:{'id':'already-completed','size':str(source.stat().st_size)}
    result=storage.put_file(key,source,'video/mp4')
    assert result['verified'] and result['id']=='already-completed'
    assert 'POST' not in calls
    assert repo.get('files',sid,'u')['encrypted_session']==''


def test_expired_session_without_completed_remote_restarts_once(tmp_path):
    from app.security import encrypt_secret
    source=tmp_path/'result.mp4'
    source.write_bytes(b'new output')
    repo=Repository(Store('sqlite://'))
    key='outputs/job/result.mp4'
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    kh=hashlib.sha256(key.encode()).hexdigest()
    sid=hashlib.sha256(f'upload:u:{kh}:{sha}'.encode()).hexdigest()
    repo.create('files','u',{'kind':'drive_upload_session','state':'uploading',
        'encrypted_session':encrypt_secret('https://www.googleapis.com/upload/drive/v3/files?upload_id=expired'),
        'size':source.stat().st_size,'sha256':sha,'key_hash':kh},record_id=sid)
    calls=[]
    def handler(request):
        calls.append((request.method,str(request.url)))
        if request.method=='GET':
            return httpx.Response(200,stream=FileStream(source))
        if request.method=='POST':
            return httpx.Response(200,headers={'Location':'https://www.googleapis.com/upload/drive/v3/files?upload_id=new'})
        if request.url.params['upload_id']=='expired':
            return httpx.Response(410)
        return httpx.Response(201,json={'id':'new-remote','size':str(source.stat().st_size)})
    result=storage_for(repo,source,handler).put_file(key,source,'video/mp4')
    assert result['verified']
    assert sum(method=='POST' for method,_ in calls)==1


def test_lost_final_reply_recovers_without_retransmitting(tmp_path):
    source=tmp_path/'result.mp4'
    source.write_bytes(b'final response was lost')
    repo=Repository(Store('sqlite://'))
    chunks=[]
    def handler(request):
        if request.method=='POST':
            return httpx.Response(200,headers={'Location':'https://www.googleapis.com/upload/drive/v3/files?upload_id=lost'})
        if request.method=='GET':
            return httpx.Response(200,stream=FileStream(source))
        if request.headers['Content-Range'].startswith('bytes */'):
            return httpx.Response(200,json={'id':'completed','size':str(source.stat().st_size)})
        chunks.append(request.content)
        raise httpx.ReadError('offline final response loss',request=request)
    result=storage_for(repo,source,handler).put_file('outputs/job/result.mp4',source,'video/mp4')
    assert result['verified'] and len(chunks)==1


@pytest.mark.parametrize('status', [401, 403, 429, 500])
def test_nonexpiry_failure_does_not_replace_session(tmp_path,status):
    from app.security import encrypt_secret
    source=tmp_path/'result.mp4'
    source.write_bytes(b'keep original session')
    repo=Repository(Store('sqlite://'))
    key='outputs/job/result.mp4'
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    kh=hashlib.sha256(key.encode()).hexdigest()
    sid=hashlib.sha256(f'upload:u:{kh}:{sha}'.encode()).hexdigest()
    encrypted=encrypt_secret('https://www.googleapis.com/upload/drive/v3/files?upload_id=keep')
    repo.create('files','u',{'kind':'drive_upload_session','state':'uploading',
        'encrypted_session':encrypted,'size':source.stat().st_size,'sha256':sha,'key_hash':kh},record_id=sid)
    calls=[]
    def handler(request):
        calls.append(request.method)
        return httpx.Response(status)
    storage=storage_for(repo,source,handler)
    def forbidden(*args):
        pytest.fail('nonexpiry error cannot start reconciliation or replacement')
    storage._find_existing_file=forbidden
    with pytest.raises(DriveError):
        storage.put_file(key,source,'video/mp4')
    assert calls==['PUT']
    assert repo.get('files',sid,'u')['encrypted_session']==encrypted


@pytest.mark.parametrize('bad_size', ['not-an-int', None, {}, True])
def test_invalid_completion_size_is_a_safe_drive_error(tmp_path,bad_size):
    source=tmp_path/'result.mp4'
    source.write_bytes(b'x')
    repo=Repository(Store('sqlite://'))
    def handler(request):
        if request.method=='POST':
            return httpx.Response(200,headers={'Location':'https://www.googleapis.com/upload/drive/v3/files?upload_id=bad'})
        if request.method=='PUT':
            return httpx.Response(201,json={'id':'r','size':bad_size})
        pytest.fail('invalid completion must not reach media verification')
    with pytest.raises(DriveError,match='completion invalid'):
        storage_for(repo,source,handler).put_file('outputs/job/result.mp4',source,'video/mp4')
    assert not any(row.get('kind')=='drive_file' for row in repo.list('files','u'))


def test_competing_upload_does_not_initialize_second_session(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    source=tmp_path/'result.mp4'
    source.write_bytes(b'concurrent bytes')
    url='sqlite:///'+str(tmp_path/'shared.db')
    first_repo=Repository(Store(url))
    second_repo=Repository(Store(url))
    entered=threading.Event()
    release=threading.Event()
    posts=[]
    def handler(request):
        if request.method=='POST':
            posts.append(1)
            entered.set()
            assert release.wait(5)
            return httpx.Response(200,headers={'Location':'https://www.googleapis.com/upload/drive/v3/files?upload_id=single'})
        if request.method=='GET':
            return httpx.Response(200,stream=FileStream(source))
        return httpx.Response(201,json={'id':'r','size':str(source.stat().st_size)})
    first=storage_for(first_repo,source,handler)
    second=storage_for(second_repo,source,handler)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(first.put_file,'outputs/job/result.mp4',source,'video/mp4')
        try:
            assert entered.wait(5)
            with pytest.raises(DriveError,match='busy'):
                second.put_file('outputs/job/result.mp4',source,'video/mp4')
        finally:
            release.set()
        assert future.result()['verified']
    assert len(posts)==1
    assert second.put_file('outputs/job/result.mp4',source,'video/mp4')['verified']
