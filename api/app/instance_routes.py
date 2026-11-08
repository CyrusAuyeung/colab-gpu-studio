"""HTTP boundary for owner-scoped GPU instances and the controlled workbench."""

from __future__ import annotations

import copy
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import APIError, current_user, ok
from .gpu.instances import AllocationError, BudgetError, InstanceConflict, InstanceServiceError
from .gpu.repository import Repository
from .gpu.workbench import WorkbenchError, WorkbenchService
from .presenters import public_job

router = APIRouter(prefix="/api/gpu")


class RetentionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # ``retention_mode`` is the v0.2 client spelling; ``mode`` remains a
    # compatibility alias used by the first offline API clients.
    mode: Literal["save", "smart", "retain"] | None = None
    retention_mode: Literal["save", "smart", "retain"] | None = None
    idle_minutes: int = Field(default=0, ge=0, le=30)
    key: str | None = Field(default=None, min_length=1, max_length=200)
    expected_version: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def has_mode(self):
        if not self.mode and not self.retention_mode:
            raise ValueError("retention_mode不能为空")
        if self.mode and self.retention_mode and self.mode != self.retention_mode:
            raise ValueError("mode与retention_mode不能冲突")
        return self


class StopInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str | None = Field(default=None, min_length=1, max_length=200)
    confirmed: bool


class WorkbenchActionInput(BaseModel):
    model_config = ConfigDict(extra="allow")
    action: str = Field(min_length=1, max_length=64)
    key: str | None = Field(default=None, max_length=200)


_PUBLIC_SECRET_NAMES = {
    "handle",
    "runtime_handle",
    "connectioninfo",
    "connection_info",
    "connection_token",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "cookie",
    "authorization",
    "signed_url",
    "runtime_name",
}


def _safe_public(value: Any):
    """Remove credential-bearing fields even if a service.public implementation errs."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _PUBLIC_SECRET_NAMES or any(
                marker in normalized for marker in ("token", "secret", "password", "authorization", "cookie")
            ):
                continue
            result[key] = _safe_public(item)
        return result
    if isinstance(value, list):
        return [_safe_public(item) for item in value]
    return value


def _service(request: Request):
    service = getattr(request.app.state, "instance_service", None)
    if service is None:
        factory = getattr(request.app.state, "instance_service_factory", None)
        if callable(factory):
            service = factory(request)
    if service is None:
        raise APIError("INSTANCE_SERVICE_UNAVAILABLE", "实例服务尚未接入", 503, True)
    return service


def _repo(request: Request, service):
    repo = getattr(request.app.state, "repo", None) or getattr(service, "repo", None)
    if repo is not None:
        return repo
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise APIError("INSTANCE_SERVICE_UNAVAILABLE", "实例存储尚未接入", 503, True)
    return Repository(store)


def _list(repo, kind, owner):
    try:
        return list(repo.list(kind, owner))
    except (AttributeError, KeyError, ValueError):
        return []


def _domain_error(exc):
    if isinstance(exc, InstanceConflict):
        return APIError("INSTANCE_CONFLICT", str(exc), 409, True)
    if isinstance(exc, BudgetError):
        return APIError("BUDGET_REJECTED", str(exc), 409)
    if isinstance(exc, AllocationError):
        return APIError("INSTANCE_OPERATION_REJECTED", str(exc), 422)
    if isinstance(exc, KeyError):
        return APIError("NOT_FOUND", "实例不存在或不属于当前用户", 404)
    if isinstance(exc, InstanceServiceError):
        return APIError("INSTANCE_OPERATION_FAILED", str(exc), 503, True)
    return None


def _get(repo, kind, record_id, owner):
    try:
        return repo.get(kind, record_id, owner)
    except (AttributeError, KeyError, ValueError):
        for row in _list(repo, kind, owner):
            if row.get("id") == record_id:
                return row
        return None


def _instance(request: Request, service, instance_id: str, owner: str):
    repo = _repo(request, service)
    getter = getattr(service, "get", None)
    row = None
    if callable(getter):
        try:
            row = getter(owner, instance_id)
        except TypeError:
            row = getter(instance_id, owner)
        except (InstanceServiceError, KeyError) as exc:
            mapped = _domain_error(exc)
            if mapped and mapped.code != "NOT_FOUND":
                raise mapped from exc
    if row is None:
        row = _get(repo, "instances", instance_id, owner)
    if row is None:
        # A service may have a non-Repository read model.  It is still required
        # to return owner-bound records; never accept a client-supplied owner.
        getter = getattr(service, "get", None)
        if callable(getter):
            try:
                row = getter(owner, instance_id)
            except TypeError:
                row = getter(instance_id, owner)
    if not isinstance(row, dict):
        raise APIError("NOT_FOUND", "实例不存在或不属于当前用户", 404)
    if row.get("owner_id") not in (None, owner):
        raise APIError("NOT_FOUND", "实例不存在或不属于当前用户", 404)
    return row


def _public(service, row):
    public = getattr(service, "public", None)
    value = public(row) if callable(public) else row
    return _safe_public(copy.deepcopy(value))


def _related(request: Request, instance_id: str, owner: str):
    service = _service(request)
    repo = _repo(request, service)
    jobs = [row for row in _list(repo, "jobs", owner) if row.get("instance_id") == instance_id]
    event_reader = getattr(service, "events", None)
    if callable(event_reader):
        try:
            events = list(event_reader(owner, instance_id))
        except (InstanceServiceError, KeyError) as exc:
            raise _domain_error(exc) from exc
    else:
        events = [
            row
            for kind in ("instance_events", "events")
            for row in _list(repo, kind, owner)
            if row.get("instance_id") == instance_id
        ]
    return jobs, events


def _public_job_item(job: Any):
    if isinstance(job, dict):
        return _safe_public(public_job(job))
    return _safe_public(job)


@router.get("/instances")
def instances(
    request: Request,
    state: str | None = None,
    detail: bool = False,
    user=Depends(current_user),
):
    service = _service(request)
    repo = _repo(request, service)
    listed = getattr(service, "list", None)
    if callable(listed):
        try:
            rows = list(listed(user["id"], state=state))
        except TypeError:
            rows = list(listed(user["id"], state))
        except (InstanceServiceError, KeyError) as exc:
            raise _domain_error(exc) from exc
    else:
        rows = _list(repo, "instances", user["id"])
    if state:
        rows = [row for row in rows if row.get("state") == state]
    result = []
    for row in rows:
        item = _public(service, row)
        if detail:
            jobs, events = _related(request, row["id"], user["id"])
            item["jobs"] = [_public_job_item(job) for job in jobs]
            item["events"] = [_safe_public(event) for event in events]
        result.append(item)
    return ok(result, request)


@router.get("/instances/{instance_id}")
def instance_detail(instance_id: str, request: Request, user=Depends(current_user)):
    service = _service(request)
    row = _instance(request, service, instance_id, user["id"])
    jobs, events = _related(request, instance_id, user["id"])
    item = _public(service, row)
    item["jobs"] = [_public_job_item(job) for job in jobs]
    item["events"] = [_safe_public(event) for event in events]
    return ok(item, request)


@router.get("/instances/{instance_id}/jobs")
def instance_jobs(instance_id: str, request: Request, user=Depends(current_user)):
    service = _service(request)
    _instance(request, service, instance_id, user["id"])
    jobs, _ = _related(request, instance_id, user["id"])
    return ok([_public_job_item(job) for job in jobs], request)


@router.get("/instances/{instance_id}/events")
def instance_events(instance_id: str, request: Request, user=Depends(current_user)):
    service = _service(request)
    _instance(request, service, instance_id, user["id"])
    _, events = _related(request, instance_id, user["id"])
    return ok([_safe_public(event) for event in events], request)


@router.patch("/instances/{instance_id}/retention", status_code=202)
def retention(instance_id: str, body: RetentionInput, request: Request, user=Depends(current_user)):
    service = _service(request)
    row = _instance(request, service, instance_id, user["id"])
    if row.get("state") in {"deleted", "unknown", "cleanup_pending", "quarantined"}:
        raise APIError("INSTANCE_READ_ONLY", "实例已失效，不能修改保留策略", 409)
    mode = body.mode or body.retention_mode
    key = body.key or request.headers.get("Idempotency-Key")
    if not key:
        raise APIError("IDEMPOTENCY_REQUIRED", "保留策略更新必须提供固定幂等键")
    if mode not in {"retain", "release", "save", "none", "cancel", "smart"}:
        raise APIError("RETENTION_MODE_INVALID", "保留模式只能是 retain 或 release", 422)
    mode = "release" if mode in {"cancel", "none"} else mode
    try:
        value = service.retention(
            user["id"], instance_id, mode, body.idle_minutes, key, expected_version=body.expected_version
        )
    except TypeError:
        value = service.retention(user["id"], instance_id, mode, body.idle_minutes, key, body.expected_version)
    except (InstanceServiceError, KeyError) as exc:
        raise _domain_error(exc) from exc
    return ok(_public(service, value) if isinstance(value, dict) else value, request, 202)


@router.post("/instances/{instance_id}/stop", status_code=202)
def stop_instance(instance_id: str, body: StopInput, request: Request, user=Depends(current_user)):
    service = _service(request)
    row = _instance(request, service, instance_id, user["id"])
    if body.confirmed is not True:
        raise APIError("CONFIRMATION_REQUIRED", "停止实例必须明确 confirmed=true", 422)
    key = body.key or request.headers.get("Idempotency-Key")
    if not key:
        raise APIError("IDEMPOTENCY_REQUIRED", "停止实例必须提供固定幂等键")
    if row.get("state") == "deleted":
        return ok({"id": instance_id, "state": "deleted", "operation": "already_deleted"}, request, 202)
    try:
        value = service.stop(user["id"], instance_id, key)
    except TypeError:
        value = service.stop(user["id"], instance_id, key, confirmed=True)
    except (InstanceServiceError, KeyError) as exc:
        raise _domain_error(exc) from exc
    # A request to stop is not evidence of remote deletion.
    if isinstance(value, dict) and value.get("state") == "deleted" and row.get("state") != "deleted":
        value = {**value, "state": "releasing", "remote_delete_confirmed": False}
    return ok(_public(service, value) if isinstance(value, dict) else value, request, 202)


def _workbench(request: Request, service):
    wb = getattr(request.app.state, "workbench_service", None)
    if wb is not None:
        return wb
    repo = _repo(request, service)
    wb = WorkbenchService(repo=repo)
    request.app.state.workbench_service = wb
    return wb


@router.get("/instances/{instance_id}/workbench")
def workbench_info(instance_id: str, request: Request, user=Depends(current_user)):
    service = _service(request)
    row = _instance(request, service, instance_id, user["id"])
    return ok(_workbench(request, service).describe(user["id"], row), request)


@router.post("/instances/{instance_id}/workbench/actions")
def workbench_action(instance_id: str, body: WorkbenchActionInput, request: Request, user=Depends(current_user)):
    service = _service(request)
    row = _instance(request, service, instance_id, user["id"])
    payload = body.model_dump(exclude={"action", "key"})
    key = request.headers.get("Idempotency-Key") or body.key
    if body.key and request.headers.get("Idempotency-Key") and body.key != request.headers["Idempotency-Key"]:
        raise APIError("IDEMPOTENCY_CONFLICT", "请求头与正文幂等键不一致", 409)
    try:
        result = _workbench(request, service).execute(user["id"], row, body.action, payload, key)
    except WorkbenchError as exc:
        raise APIError(exc.code, str(exc), exc.status, exc.retryable) from exc
    return ok(_safe_public(result), request, 202 if body.action in {"set_reviewed_params", "queue_reviewed_workflow", "stop_current_job"} else 200)


@router.get("/workflows/{workflow_key}/preview")
def workflow_graph_preview(
    workflow_key: str,
    request: Request,
    version: int,
    user=Depends(current_user),
):
    """Owner-scoped offline graph preview; no instance or Comfy connection."""
    wb = _workbench(request, None)
    try:
        result = wb.get_reviewed_graph(
            user["id"], {"workflow_key": workflow_key, "workflow_version": version}
        )
    except WorkbenchError as exc:
        raise APIError(exc.code, str(exc), exc.status, exc.retryable) from exc
    return ok(_safe_public(result), request)


__all__ = ["router", "RetentionInput", "StopInput", "WorkbenchActionInput"]
