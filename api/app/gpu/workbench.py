"""A narrow, offline-safe Comfy workbench domain service.

The workbench is deliberately not a proxy.  It accepts a fixed set of domain
operations and can only operate on an instance-owned, published workflow
snapshot.  A real runtime needs a separately verified MCP adapter; without one
all real operations fail closed with ``COMFY_UNVERIFIED``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from collections.abc import Mapping

from ..common import APIError
from ..gpu.manifest import bind_parameters, resolve_parameters

WORKBENCH_ACTIONS = frozenset(
    {
        "health",
        "list_allowed_workflows",
        "get_reviewed_graph",
        "set_reviewed_params",
        "queue_reviewed_workflow",
        "job_status",
        "read_safe_log",
        "preview_artifacts",
        "stop_current_job",
    }
)
WRITE_ACTIONS = frozenset({"set_reviewed_params", "queue_reviewed_workflow", "stop_current_job"})
SENSITIVE_KEYS = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "connection_token",
        "connectioninfo",
        "connection_info",
        "authorization",
        "cookie",
        "secret",
        "password",
        "handle",
        "runtime_handle",
        "signed_url",
        "url",
        "code",
        "shell",
        "python",
        "node",
        "nodes",
        "api_graph",
        "editable_graph",
    }
)


class WorkbenchError(APIError):
    """A typed API error that also works for direct service callers."""

    def __init__(self, code: str, message: str, status: int = 422, retryable: bool = False):
        super().__init__(code, message, status, retryable)


class _OperationLedger:
    """Repository-backed idempotency ledger for workbench write actions."""

    def __init__(self, repo):
        self.repo = repo
        self._lock = threading.RLock()

    @staticmethod
    def digest(body: Mapping) -> str:
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()

    @staticmethod
    def _id(owner: str, instance_id: str, action: str, key: str) -> str:
        return hashlib.sha256(
            json.dumps([owner, instance_id, action, key], ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()

    def _load(self, owner: str, instance_id: str, action: str, key: str):
        if self.repo is None or not hasattr(self.repo, "get"):
            raise WorkbenchError("OPERATION_STORE_UNAVAILABLE", "工作台幂等存储尚未接入", 503, True)
        operation_id = self._id(owner, instance_id, action, key)
        try:
            row = self.repo.get("operations", operation_id, owner)
        except (AttributeError, TypeError):
            # Small offline repositories may only expose list().  The production
            # Repository supports deterministic record IDs, so this is not its path.
            rows = self.repo.list("operations", owner)
            row = next((row for row in rows if row.get("id") == operation_id), None)
        if row is not None:
            return row
        # A minimal injected repository may not support deterministic record IDs;
        # the identity fields still make its persisted row restart-readable.
        rows = self.repo.list("operations", owner)
        return next(
            (
                item
                for item in rows
                if item.get("instance_id") == instance_id
                and item.get("action") == action
                and item.get("key") == key
            ),
            None,
        )

    def existing(self, owner: str, instance_id: str, action: str, key: str, body: Mapping):
        body_hash = self.digest(body)
        with self._lock:
            row = self._load(owner, instance_id, action, key)
            if not row:
                return None
            if row.get("body_hash") != body_hash:
                raise WorkbenchError("IDEMPOTENCY_CONFLICT", "相同幂等键不能用于不同工作台请求", 409)
            return copy.deepcopy(row.get("result"))

    def put(self, owner: str, instance_id: str, action: str, key: str, body: Mapping, result):
        operation_id = self._id(owner, instance_id, action, key)
        data = {
            "instance_id": instance_id,
            "action": action,
            "key": key,
            "body_hash": self.digest(body),
            "result": copy.deepcopy(result),
        }
        with self._lock:
            try:
                self.repo.create("operations", owner, data, record_id=operation_id)
            except TypeError:
                # Compatibility for a minimal injected repository; persistent
                # repositories should implement record_id for cross-process dedupe.
                self.repo.create("operations", owner, data)
            except ValueError:
                existing = self._load(owner, instance_id, action, key)
                if not existing:
                    raise
                if existing.get("body_hash") != data["body_hash"]:
                    raise WorkbenchError("IDEMPOTENCY_CONFLICT", "相同幂等键不能用于不同工作台请求", 409)
        return result


class WorkbenchService:
    """Controlled workbench with a deterministic mock backend.

    ``repo`` is intentionally duck-typed so the service can be used by the
    existing Repository and by a small in-memory offline repository.  A trusted
    real adapter may be supplied by the server, but it is never selected from
    a client-provided URL.
    """

    def __init__(self, repo=None, *, registry=None, admit_job=None, trusted_mcp=None, clock=None):
        self.repo = repo
        self.registry = registry
        self.admit_job = admit_job
        self.trusted_mcp = trusted_mcp
        self.clock = clock or time.time
        self._ops = _OperationLedger(repo)

    @staticmethod
    def _is_mock(instance: Mapping) -> bool:
        return bool(instance.get("mock") or instance.get("is_mock") or instance.get("provider") == "mock")

    @staticmethod
    def _is_history(instance: Mapping) -> bool:
        return instance.get("state") in {"unknown", "deleted", "releasing", "cleanup_pending", "quarantined"}

    def _rows(self, kind: str, owner: str):
        if self.repo is None:
            return []
        source = self.repo.store if kind == "assets" and hasattr(self.repo, "store") else self.repo
        return list(source.list(kind, owner))

    def _workflows(self, owner: str):
        if self.registry is not None:
            return [row for row in self.registry.workflows(owner) if row.get("state") == "published"]
        return [row for row in self._rows("workflows", owner) if row.get("state") == "published"]

    @staticmethod
    def _workflow_identity(payload: Mapping):
        key = payload.get("workflow_key") or payload.get("key")
        version = payload.get("workflow_version", payload.get("version"))
        if not isinstance(key, str) or not key or version in (None, ""):
            raise WorkbenchError("VALIDATION_ERROR", "必须指定已发布工作流 key 和 version")
        return key, version

    def _workflow(self, owner: str, payload: Mapping):
        workflow_id = payload.get("workflow_id")
        if workflow_id:
            for row in self._workflows(owner):
                if row.get("id") == workflow_id:
                    return row
            raise WorkbenchError("WORKFLOW_UNPUBLISHED", "工作流未发布或不属于当前用户", 409)
        key, version = self._workflow_identity(payload)
        for row in self._workflows(owner):
            if row.get("workflow_key") == key and row.get("workflow_version") == version:
                return row
        raise WorkbenchError("WORKFLOW_UNPUBLISHED", "工作流未发布或不属于当前用户", 409)

    def _mock_or_unverified(self, instance: Mapping):
        if self._is_mock(instance):
            return
        if self.trusted_mcp is None:
            raise WorkbenchError("COMFY_UNVERIFIED", "真实实例尚未接入受信 Comfy MCP 协议", 503, True)

    def _forbid_client_surface(self, payload: Mapping):
        # These names are never accepted, even when nested.  Graphs are read
        # from the published registry and URLs are owned by the server.
        def scan(value):
            if isinstance(value, Mapping):
                for key, item in value.items():
                    if str(key).lower() in SENSITIVE_KEYS:
                        raise WorkbenchError("WORKBENCH_INPUT_FORBIDDEN", "工作台不接受任意代码、URL、节点或凭据", 422)
                    scan(item)
            elif isinstance(value, list):
                for item in value:
                    scan(item)

        scan(payload)

    def _write_draft(self, owner, instance, workflow, params, bound_graph):
        identity = f"{workflow['workflow_key']}@{workflow['workflow_version']}"
        value = {"params": copy.deepcopy(params), "bound_graph": copy.deepcopy(bound_graph), "draft": True}
        if self.repo is None or not hasattr(self.repo, "update"):
            raise WorkbenchError("DRAFT_PERSISTENCE_UNAVAILABLE", "工作台草稿存储尚未接入", 503, True)
        persisted = self.repo.get("instances", instance["id"], owner) if hasattr(self.repo, "get") else None
        source = persisted if isinstance(persisted, Mapping) else instance
        current = source.get("workbench_drafts") if isinstance(source, Mapping) else None
        drafts = dict(current) if isinstance(current, Mapping) else {}
        drafts[identity] = value
        self.repo.update("instances", instance["id"], {"workbench_drafts": drafts}, owner)
        return copy.deepcopy(value)

    @staticmethod
    def _is_verified(instance: Mapping) -> bool:
        """Only explicit runtime evidence can make a real instance writable."""
        return WorkbenchService._is_mock(instance) or bool(
            instance.get("verified") is True or instance.get("runtime_verified") is True
        )

    @classmethod
    def _safe_public_value(cls, value):
        """Copy public JSON while removing credential/URL-shaped fields."""
        if isinstance(value, Mapping):
            return {
                str(key): cls._safe_public_value(item)
                for key, item in value.items()
                if str(key).lower() not in SENSITIVE_KEYS and "url" not in str(key).lower()
            }
        if isinstance(value, list):
            return [cls._safe_public_value(item) for item in value]
        if isinstance(value, tuple):
            return [cls._safe_public_value(item) for item in value]
        if isinstance(value, str):
            return re.sub(r"(?:https?|wss?)://[^\\s\\\"'<>]+", "[REDACTED_URL]", value)
        return copy.deepcopy(value)

    @classmethod
    def _public_parameters(cls, manifest):
        raw = manifest.get("parameters") if isinstance(manifest, Mapping) else None
        if isinstance(raw, Mapping):
            result = []
            for key, definition in raw.items():
                if not isinstance(definition, Mapping):
                    continue
                item = cls._safe_public_value(definition)
                item.setdefault("key", str(key))
                result.append(item)
            return result
        if isinstance(raw, list):
            result = []
            for item in raw:
                if not isinstance(item, Mapping):
                    continue
                public = cls._safe_public_value(item)
                key = public.get("key") or public.get("id") or public.get("name")
                if key is not None:
                    public.setdefault("key", str(key))
                result.append(public)
            return result
        return []

    def _draft_values(self, owner, instance):
        if instance.get("owner_id") not in (None, owner):
            return {}
        source = instance
        if self.repo is not None and hasattr(self.repo, "get"):
            persisted = self.repo.get("instances", instance.get("id"), owner)
            if isinstance(persisted, Mapping):
                source = persisted
        raw = source.get("workbench_drafts") if isinstance(source, Mapping) else None
        if not isinstance(raw, Mapping):
            return {}
        result = {}
        for identity, draft in raw.items():
            params = draft.get("params") if isinstance(draft, Mapping) else draft
            if isinstance(params, Mapping):
                result[str(identity)] = self._safe_public_value(params)
        return result

    @staticmethod
    def _safe_artifact(asset: Mapping, job_id: str):
        result = {"job_id": job_id}
        allowed = {"id", "name", "filename", "mime", "status", "created_at"}
        for key in allowed:
            if key in asset and asset[key] is not None:
                result[key] = WorkbenchService._safe_public_value(asset[key])
        thumbnail = asset.get("thumbnail_url")
        # Only a server-relative path is safe to hand to the browser.  Drop
        # query strings so a signed URL/token cannot cross this boundary.
        if isinstance(thumbnail, str) and thumbnail.startswith("/") and not thumbnail.startswith("//"):
            result["thumbnail_url"] = thumbnail.split("?", 1)[0].split("#", 1)[0]
        return result

    def _describe_outputs(self, owner, instance):
        instance_id = instance.get("id")
        jobs = [
            job
            for job in self._rows("jobs", owner)
            if job.get("owner_id", owner) == owner and job.get("instance_id") == instance_id
        ]
        job_ids = {job.get("id") for job in jobs if isinstance(job.get("id"), str)}
        assets_by_id = {
            asset.get("id"): asset
            for asset in self._rows("assets", owner)
            if asset.get("owner_id", owner) == owner and asset.get("id") is not None
        }
        artifacts = []
        seen_assets = set()
        for job in jobs:
            output_ids = job.get("output_asset_ids", [])
            if not isinstance(output_ids, list):
                continue
            for asset_id in output_ids:
                asset = assets_by_id.get(asset_id)
                marker = (job.get("id"), asset_id)
                if not isinstance(asset, Mapping) or marker in seen_assets:
                    continue
                seen_assets.add(marker)
                artifacts.append(self._safe_artifact(asset, job["id"]))
        logs = []
        for event in self._rows("events", owner):
            if event.get("owner_id", owner) != owner or event.get("job_id") not in job_ids:
                continue
            log = {"job_id": event["job_id"], "message": self._safe_log(event.get("message", ""))}
            for key in ("id", "created_at"):
                if event.get(key) is not None:
                    log[key] = event[key]
            logs.append(log)
        return artifacts, logs

    def describe(self, owner: str, instance: Mapping) -> dict:
        drafts = self._draft_values(owner, instance)
        workflows = []
        for row in self._workflows(owner):
            identity = f"{row.get('workflow_key')}@{row.get('workflow_version')}"
            manifest = row.get("manifest") if isinstance(row.get("manifest"), Mapping) else {}
            item = {
                "workflow_key": row.get("workflow_key"),
                "workflow_version": row.get("workflow_version"),
                "model_id": row.get("model_id"),
                "description": row.get("description"),
                "mock": bool(row.get("smoke_test_kind") == "mock" or row.get("model_id") == "demo-mock"),
                "manifest": {"parameters": self._safe_public_value(manifest.get("parameters", {}))},
                "parameters": self._public_parameters(manifest),
            }
            if identity in drafts:
                item["draft"] = {"values": copy.deepcopy(drafts[identity])}
            workflows.append(item)
        parameters = self._public_parameters(
            self._workflows(owner)[0].get("manifest", {}) if self._workflows(owner) else {}
        )
        values = drafts.get(
            f"{workflows[0]['workflow_key']}@{workflows[0]['workflow_version']}"
        ) if workflows else None
        artifacts, logs = self._describe_outputs(owner, instance)
        read_only = self._is_history(instance) or not self._is_verified(instance)
        capabilities = set(WORKBENCH_ACTIONS)
        if read_only:
            capabilities.difference_update(WRITE_ACTIONS)
        actions = sorted(capabilities)
        result = {
            "instance_id": instance.get("id"),
            "state": instance.get("state"),
            "read_only": read_only,
            "mock": self._is_mock(instance),
            "comfy": {
                "available": self._is_mock(instance),
                "verified": bool(self._is_verified(instance) and not self._is_mock(instance)),
                "simulated": self._is_mock(instance),
            },
            # ``actions`` is the historical name; ``capabilities`` is the UI contract.
            "actions": actions,
            "capabilities": actions,
            "allowed_workflows": workflows,
            "parameters": parameters,
            "drafts": drafts,
            "values": copy.deepcopy(values) if isinstance(values, Mapping) else {},
            "artifacts": artifacts,
            "logs": logs,
        }
        return result

    def _operation(self, owner, instance, action, payload, key):
        if action not in WORKBENCH_ACTIONS:
            raise WorkbenchError("WORKBENCH_ACTION_FORBIDDEN", "工作台动作不在固定 allowlist", 403)
        if action in WRITE_ACTIONS:
            if not isinstance(key, str) or not key or len(key) > 200:
                raise WorkbenchError("IDEMPOTENCY_REQUIRED", "写入工作台必须提供固定幂等键", 422)
            previous = self._ops.existing(owner, str(instance["id"]), action, key, payload)
            if previous is not None:
                return previous
        self._forbid_client_surface(payload)
        if self._is_history(instance):
            if action in WRITE_ACTIONS:
                raise WorkbenchError("INSTANCE_READ_ONLY", "实例已失效，工作台仅可读取历史", 409)
            if action == "health":
                return {"available": False, "verified": False, "read_only": True, "state": instance.get("state")}

        handler = getattr(self, f"_action_{action}")
        result = handler(owner, instance, payload, key) if action == "queue_reviewed_workflow" else handler(owner, instance, payload)
        if action in WRITE_ACTIONS:
            self._ops.put(owner, str(instance["id"]), action, key, payload, result)
        return result

    def execute(self, owner: str, instance: Mapping, action: str, payload: Mapping | None = None, key: str | None = None):
        payload = dict(payload or {})
        if not isinstance(action, str):
            raise WorkbenchError("VALIDATION_ERROR", "action必须是字符串")
        return self._operation(owner, instance, action, payload, key)

    def _action_health(self, owner, instance, payload):
        self._mock_or_unverified(instance)
        if self._is_mock(instance):
            return {
                "available": True,
                "verified": False,
                "mock": True,
                "simulated": True,
                "status": "simulated",
                "capabilities": sorted(WORKBENCH_ACTIONS),
            }
        return self.trusted_mcp.health(owner, instance)

    def _action_list_allowed_workflows(self, owner, instance, payload):
        self._mock_or_unverified(instance)
        return {"workflows": self.describe(owner, instance)["allowed_workflows"], "mock": self._is_mock(instance)}

    def _action_get_reviewed_graph(self, owner, instance, payload):
        self._mock_or_unverified(instance)
        workflow = self._workflow(owner, payload)
        graph = workflow.get("api_graph")
        if not isinstance(graph, Mapping):
            raise WorkbenchError("WORKFLOW_INVALID", "已发布工作流缺少受审查图", 409)
        return {
            "workflow_key": workflow["workflow_key"],
            "workflow_version": workflow["workflow_version"],
            "graph": copy.deepcopy(dict(graph)),
            "reviewed": True,
            "published": True,
        }

    def _action_set_reviewed_params(self, owner, instance, payload):
        self._mock_or_unverified(instance)
        workflow = self._workflow(owner, payload)
        params = payload.get("params")
        if not isinstance(params, Mapping):
            raise WorkbenchError("VALIDATION_ERROR", "params必须是对象")
        try:
            resolved = resolve_parameters(workflow["manifest"], dict(params))
            bound = bind_parameters(workflow["manifest"], workflow["api_graph"], resolved)
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkbenchError("PARAMETERS_INVALID", str(exc)[:300]) from exc
        draft = self._write_draft(owner, instance, workflow, resolved, bound)
        return {"workflow_key": workflow["workflow_key"], "workflow_version": workflow["workflow_version"], **draft}

    def _action_queue_reviewed_workflow(self, owner, instance, payload, operation_key=None):
        self._mock_or_unverified(instance)
        if instance.get("state") in {"busy", "running"} or instance.get("current_job_id"):
            raise WorkbenchError("INSTANCE_BUSY", "实例正在执行任务，不能绕过实例锁", 409)
        workflow = self._workflow(owner, payload)
        job_input = payload.get("job_input")
        if job_input is None:
            job_input = {
                name: value
                for name, value in payload.items()
                if name not in {"workflow_id", "workflow_key", "workflow_version", "job_key", "idempotency_key"}
            }
        if not isinstance(job_input, Mapping):
            raise WorkbenchError("VALIDATION_ERROR", "queue的JobInput必须是对象")
        body = copy.deepcopy(dict(job_input))
        body.setdefault("project_id", instance.get("project_id"))
        body.setdefault("model_id", workflow.get("model_id"))
        body.setdefault("params", payload.get("params", {}))
        body.setdefault("asset_ids", [])
        body.setdefault("loras", [])
        body.setdefault("preset_id", None)
        policy = body.get("execution_policy") or {}
        if not isinstance(policy, Mapping):
            raise WorkbenchError("VALIDATION_ERROR", "JobInput的execution_policy必须是对象")
        body["workflow_key"] = workflow["workflow_key"]
        body["workflow_version"] = workflow["workflow_version"]
        body["execution_policy"] = {
            **dict(policy),
            "allocation": "existing",
            "instance_id": instance["id"],
            "account_id": instance.get("account_id"),
            "provider": "mock" if self._is_mock(instance) else dict(policy).get("provider", "colab"),
        }
        key = operation_key
        if not isinstance(key, str) or not key:
            raise WorkbenchError("IDEMPOTENCY_REQUIRED", "排队必须提供工作台幂等键")
        if self.admit_job is None:
            raise WorkbenchError("QUEUE_UNAVAILABLE", "实例队列服务未接入", 503, True)
        job = self.admit_job(owner, body, key, instance=instance)
        if not isinstance(job, Mapping):
            raise WorkbenchError("QUEUE_REJECTED", "任务排队响应无效", 503, True)
        return {"accepted": True, "mock": self._is_mock(instance), "job": copy.deepcopy(dict(job))}

    def _find_job(self, owner, instance, payload):
        job_id = payload.get("job_id") or instance.get("current_job_id")
        if not isinstance(job_id, str) or not job_id:
            raise WorkbenchError("JOB_NOT_FOUND", "未指定属于该实例的任务", 404)
        for job in self._rows("jobs", owner):
            if job.get("id") == job_id and job.get("instance_id") == instance.get("id"):
                return job
        raise WorkbenchError("JOB_NOT_FOUND", "任务不属于该实例", 404)

    def _action_job_status(self, owner, instance, payload):
        self._mock_or_unverified(instance)
        return {"job": copy.deepcopy(self._find_job(owner, instance, payload))}

    def _safe_log(self, value):
        text = str(value)
        for marker in ("token", "authorization", "password", "secret", "cookie"):
            if marker in text.lower():
                return "[REDACTED]"
        return re.sub(r"(?:https?|wss?)://[^\\s\\\"'<>]+", "[REDACTED_URL]", text[:4000])

    def _action_read_safe_log(self, owner, instance, payload):
        self._mock_or_unverified(instance)
        job_id = payload.get("job_id") or instance.get("current_job_id")
        rows = [event.get("message", "") for event in self._rows("events", owner) if event.get("job_id") == job_id]
        return {"job_id": job_id, "lines": [self._safe_log(item) for item in rows], "redacted": True}

    def _action_preview_artifacts(self, owner, instance, payload):
        self._mock_or_unverified(instance)
        job = self._find_job(owner, instance, payload)
        assets = []
        for asset_id in job.get("output_asset_ids", []) if isinstance(job, Mapping) else []:
            for row in self._rows("assets", owner):
                if row.get("id") == asset_id:
                    assets.append({k: v for k, v in row.items() if str(k).lower() not in SENSITIVE_KEYS})
        return {"job_id": job.get("id"), "artifacts": assets, "mock": self._is_mock(instance)}

    def _action_stop_current_job(self, owner, instance, payload):
        self._mock_or_unverified(instance)
        job = self._find_job(owner, instance, payload)
        if self._is_mock(instance):
            if self.repo is None or not hasattr(self.repo, "update"):
                raise WorkbenchError("QUEUE_UNAVAILABLE", "实例队列服务未接入", 503, True)
            self.repo.update(
                "jobs",
                job["id"],
                {"cancel_requested": True, "requested_outcome": "cancelled"},
                owner,
            )
            return {
                "accepted": True,
                "state": "stop_requested",
                "job_id": job["id"],
                "mock": True,
                "simulated": True,
            }
        if self.trusted_mcp is None:
            raise WorkbenchError("COMFY_UNVERIFIED", "真实实例尚未接入受信 Comfy MCP 协议", 503, True)
        return self.trusted_mcp.stop_current_job(owner, instance, job.get("id"))


# Names used by integrations and tests; keep one implementation.
ControlledWorkbench = WorkbenchService
Workbench = WorkbenchService

__all__ = [
    "WORKBENCH_ACTIONS",
    "WRITE_ACTIONS",
    "WorkbenchError",
    "WorkbenchService",
    "ControlledWorkbench",
    "Workbench",
]
