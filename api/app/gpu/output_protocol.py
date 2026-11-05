"""Server-verified object manifests become private, idempotent gallery assets."""

import json
import time

from PIL import Image

from app.config import settings

from .artifacts import ArtifactError, LocalArtifactStore


def verify_output(job, item):
    if not isinstance(item, dict) or (item.get("storage") or {}).get("provider") not in {"local", "mock"}:
        raise ArtifactError("unsupported output manifest storage")
    store = LocalArtifactStore(settings.data_dir / "assets" / "artifacts")
    return store.verify_manifest(item, job["owner_id"], job["execution_id"])


def commit_output(db, job, item, metadata, storage=None):
    verified, path = verify_output(job, item)
    with path.open("rb") as source:
        header = source.read(16)
    mime = verified["mime"]
    width = height = None
    if mime == 'application/json':
        read_diagnostic_report(job,item)
    if mime.startswith("image/"):
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            if Image.MIME.get(image.format) != mime:
                raise ArtifactError("artifact mime mismatch")
            width, height = image.size
    elif mime == "video/mp4" and header[4:8] != b"ftyp":
        raise ArtifactError("invalid mp4 artifact header")
    elif mime == "video/webm" and not header.startswith(b"\x1a\x45\xdf\xa3"):
        raise ArtifactError("invalid webm artifact header")
    aid = verified['artifact_id']
    existing = db.get('assets', aid, job['owner_id'])
    if existing:
        if existing.get('execution_id') != job['execution_id'] or existing.get('sha256') != verified['sha256']:
            raise ArtifactError('asset identity conflict')
        if storage is not None and not (existing.get('cloud_record') or {}).get('verified'):
            raise ArtifactError('existing asset lacks verified cloud persistence')
        return existing
    if storage is not None:
        put_file = getattr(storage, 'put_file', None)
        if put_file is None:
            raise ArtifactError('configured storage does not support streamed files')
        cloud = put_file(f"outputs/{job['id']}/{job['execution_id']}/{verified['filename']}", path, mime)
        if (cloud.get('verified') is not True or cloud.get('sha256') != verified['sha256']
                or cloud.get('size') != verified['size_bytes']):
            raise ArtifactError('cloud file failed verified manifest checks')
        metadata = {**metadata, 'cloud_record': cloud}
    aid = verified["artifact_id"]
    with db.dispatch_lock() as acquired:
        if not acquired:
            raise ArtifactError("asset commit busy; retry")
        existing = db.get("assets", aid, job["owner_id"])
        if existing:
            if (
                existing.get("execution_id") != job["execution_id"]
                or existing.get("sha256") != verified["sha256"]
            ):
                raise ArtifactError("asset identity conflict")
            return existing
        row = {
            **metadata,
            "filename": verified["filename"],
            "mime": mime,
            "size": verified["size_bytes"],
            "sha256": verified["sha256"],
            "storage_key": f"artifacts/{aid}.data",
            "storage_provider": "local",
            "object_key": aid,
            "upload_state": "verified",
            "verified_at": time.time(),
            "source_job_id": job["id"],
            "execution_id": job["execution_id"],
            "width": width,
            "height": height,
            "favorite": False,
            "mock": job.get("provider", "mock") == "mock",
        }
        return db.create("assets", job["owner_id"], row, project_id=job.get("project_id"), record_id=aid)


def read_diagnostic_report(job,item):
    verified,path=verify_output(job,item)
    if verified['mime']!='application/json' or verified['size_bytes']>1024*1024:
        raise ArtifactError('diagnostic report must be JSON <=1MiB')
    if not ((job.get('snapshot') or {}).get('system_smoke') or job.get('prep') or (job.get('snapshot') or {}).get('prep')):
        raise ArtifactError('JSON output only allowed for diagnostic or prep jobs')
    with path.open(encoding='utf-8') as report:
        value=json.load(report)
    if not isinstance(value,dict):
        raise ArtifactError('diagnostic report must be an object')
    return value
