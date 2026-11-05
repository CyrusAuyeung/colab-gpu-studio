"""External Atelier bridge. It never changes the Atelier installation."""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager

from fastapi import APIRouter, Depends, Request

from .common import APIError, current_user, get_store, ok, own
from .gpu.repository import Repository
from .integrations.atelier import AtelierConnector, AtelierConnectorError
from .security import decrypt_secret, encrypt_secret
from .storage import read_asset

router = APIRouter(prefix="/api/gpu")


def config_id(owner):
    return "atelier:" + hashlib.sha256(owner.encode()).hexdigest()[:48]


def load_config(st, owner):
    return st.get("provider_credentials", config_id(owner), owner)


@contextmanager
def connection(st, owner):
    row = load_config(st, owner)
    if not row:
        raise APIError("ATELIER_NOT_CONNECTED", "请先配置 Atelier API 连接")
    secret = json.loads(decrypt_secret(row["encrypted_secret"]))
    with AtelierConnector(row["base_url"], secret["username"], secret["password"]) as connector:
        yield connector, row


def status(row):
    return {
        "configured": bool(row),
        "base_url": row.get("base_url") if row else "https://atelier.tizenty.xyz",
        "auto_export": bool(row and row.get("auto_export")),
        "last_checked": row.get("last_checked") if row else None,
        "notice": "本站独立运行；仅通过服务器 API 登录、读取项目、上传作品，不修改 Atelier。",
    }


@router.get("/integrations/atelier")
def atelier_status(request: Request, user=Depends(current_user), st=Depends(get_store)):
    return ok(status(load_config(st, user["id"])), request)


@router.post("/integrations/atelier")
def save_connection(body: dict, request: Request, user=Depends(current_user), st=Depends(get_store)):
    previous = load_config(st, user["id"])
    if body.get("disconnect") is True:
        if previous:
            st.delete("provider_credentials", previous["id"], user["id"])
        return ok(status(None), request)
    base = str(body.get("base_url", "https://atelier.tizenty.xyz")).rstrip("/")
    old_secret = json.loads(decrypt_secret(previous["encrypted_secret"])) if previous else {}
    username = str(body.get("username") or old_secret.get("username", ""))
    password = str(body.get("password") or old_secret.get("password", ""))
    # Validate and check credentials before replacing a working connector.
    try:
        with AtelierConnector(base, username, password) as connector:
            connector.login()
            connector.projects()
    except AtelierConnectorError as exc:
        raise APIError("ATELIER_CONNECTION_FAILED", str(exc)[:200], 422) from exc
    row = {
        "base_url": base,
        "auto_export": body.get("auto_export", False) is True,
        "encrypted_secret": encrypt_secret(json.dumps({"username": username, "password": password})),
        "last_checked": time.time(),
    }
    if previous:
        saved = st.update("provider_credentials", previous["id"], row, user["id"])
    else:
        saved = st.create("provider_credentials", user["id"], row, record_id=config_id(user["id"]))
    return ok(status(saved), request)


@router.get("/integrations/atelier/projects")
def atelier_projects(request: Request, user=Depends(current_user), st=Depends(get_store)):
    try:
        with connection(st, user["id"]) as (connector, _):
            projects = connector.projects()
    except AtelierConnectorError as exc:
        raise APIError("ATELIER_REQUEST_FAILED", str(exc)[:200]) from exc
    return ok(projects, request)


def export_job(st, owner, job_id, atelier_project_id=None):
    repo = Repository(st)
    job = repo.get("jobs", job_id, owner)
    if not job:
        raise APIError("NOT_FOUND", "任务不存在", 404)
    if job["state"] != "completed":
        raise APIError("JOB_NOT_COMPLETE", "作品保存且 GPU 释放后才能回传")
    project = st.get("projects", job["project_id"], owner)
    target = atelier_project_id or job.get("atelier_project_id") or (project or {}).get("atelier_project_id")
    if not target:
        raise APIError("ATELIER_PROJECT_REQUIRED", "请为本地项目选择 Atelier 项目")
    result = []
    with st.dispatch_lock() as acquired:
        if not acquired:
            raise APIError("EXPORT_BUSY", "同步正在进行，请稍后用相同任务重试", 503, True)
        with connection(st, owner) as (connector, _):
            allowed = {p["id"] for p in connector.projects()}
            if target not in allowed:
                raise APIError("ATELIER_PROJECT_DENIED", "目标 Atelier 项目不存在或无权限", 403)
            for aid in job.get("output_asset_ids", []):
                asset = st.get("assets", aid, owner)
                if not asset:
                    raise APIError("ASSET_MISSING", "本地生成文件已删除")
                receipt_key = f"atelier-export:{job_id}:{target}:{asset['sha256']}"
                rid = hashlib.sha256(receipt_key.encode()).hexdigest()
                existing = repo.get("files", rid, owner)
                if existing and existing.get("state") == "verified":
                    result.append(existing)
                    continue
                # Filename is deterministic across reconnect/retry. Connector also compares remote hash+bytes.
                filename = f"job-{job_id}-{asset['filename']}"
                remote = connector.export_asset(target, filename, read_asset(asset))
                record = {
                    "kind": "atelier_export",
                    "job_id": job_id,
                    "asset_id": aid,
                    "atelier_project_id": target,
                    "remote_asset_id": remote["id"],
                    "sha256": asset["sha256"],
                    "size": asset["size"],
                    "state": "verified",
                }
                if existing:
                    saved = repo.update("files", rid, record, owner)
                else:
                    saved = repo.create("files", owner, record, project_id=job["project_id"], record_id=rid)
                result.append(saved)
    return {
        "job_id": job_id,
        "atelier_project_id": target,
        "receipts": result,
        "url": f"https://atelier.tizenty.xyz/projects?project={target}",
    }


@router.post("/jobs/{job_id}/export-atelier")
def export_atelier(
    job_id: str, body: dict, request: Request, user=Depends(current_user), st=Depends(get_store)
):
    try:
        result = export_job(st, user["id"], job_id, body.get("atelier_project_id"))
    except AtelierConnectorError as exc:
        raise APIError("ATELIER_EXPORT_FAILED", str(exc)[:200]) from exc
    return ok(result, request)


@router.patch("/projects/{project_id}")
def map_project(
    project_id: str, body: dict, request: Request, user=Depends(current_user), st=Depends(get_store)
):
    own(st, "projects", project_id, user)
    patch = {}
    if "title" in body:
        patch["title"] = str(body["title"]).strip()[:200]
        if not patch["title"]:
            raise APIError("PROJECT_INVALID", "项目名不能为空")
    if "atelier_project_id" in body:
        target = str(body["atelier_project_id"])
        if target:
            with connection(st, user["id"]) as (connector, _):
                if target not in {p["id"] for p in connector.projects()}:
                    raise APIError("ATELIER_PROJECT_DENIED", "项目无访问权限", 403)
        patch["atelier_project_id"] = target
    return ok(st.update("projects", project_id, patch, user["id"]), request)


@router.post("/integrations/atelier/preview-job")
def preview_downstream(body: dict, request: Request, user=Depends(current_user), st=Depends(get_store)):
    with connection(st, user["id"]) as (connector, _):
        result = connector.preview_job(body)
    return ok(result, request)


@router.post("/integrations/atelier/submit-job")
def submit_downstream(body: dict, request: Request, user=Depends(current_user), st=Depends(get_store)):
    if body.get("confirmed") is not True:
        raise APIError("CONFIRMATION_REQUIRED", "下游可能收费，必须明确确认", 403)
    payload = {k: v for k, v in body.items() if k != "confirmed"}
    with connection(st, user["id"]) as (connector, _):
        result = connector.submit_job(payload, confirmed=True)
    return ok(result, request)


def auto_export_tick(st):
    """At most one completed job per tick; bounded retries never submit paid jobs."""
    repo = Repository(st)
    for job in reversed(repo.list("jobs")):
        if job.get("state") != "completed" or job.get("atelier_exported"):
            continue
        cfg = load_config(st, job["owner_id"])
        if not cfg or not cfg.get("auto_export") or not job.get("atelier_project_id"):
            continue
        attempts = int(job.get("atelier_export_attempts", 0))
        if attempts >= 3 or float(job.get("atelier_export_next_at", 0)) > time.time():
            continue
        try:
            result = export_job(st, job["owner_id"], job["id"])
            repo.update(
                "jobs", job["id"], {"atelier_exported": True, "atelier_receipt": result}, job["owner_id"]
            )
            repo.event(job["id"], job["owner_id"], "atelier_exported", "作品已通过 API 校验回传 Atelier 项目")
        except Exception:
            repo.update(
                "jobs",
                job["id"],
                {
                    "atelier_export_attempts": attempts + 1,
                    "atelier_export_next_at": time.time() + 30 * (2**attempts),
                },
                job["owner_id"],
            )
            repo.event(
                job["id"],
                job["owner_id"],
                "atelier_export_failed",
                "Atelier 同步失败；已保留本地作品，最多重试三次，不重新生成",
            )
        return {"job_id": job["id"], "attempt": attempts + 1}
    return {"idle": True}
