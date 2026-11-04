"""Recoverable executor for the official Google Colab MCP endpoint.

The adapter has no in-memory execution registry.  Colab's execution directory
(status.json, meta.json and output files) is the source of truth, so a process
restart or a lost JSON-RPC response cannot cause a second worker launch.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .google import GoogleService
from .runtime_worker import MAX_OUTPUT_BYTES

log = logging.getLogger("studio.mcp")
MCP_ENDPOINT = "https://colaboratory.googleapis.com/mcp"
_RUNTIME = re.compile(r"^runtimes/[a-zA-Z0-9][a-zA-Z0-9._~-]{0,127}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


_NETWORK_ATTEMPTS = 3


class MCPError(RuntimeError):
    """Safe Colab MCP adapter failure; upstream bodies are never exposed."""


class MCPTransient(MCPError):
    """Colab answered "unavailable"; every call made here is idempotent, so it is retried."""


_TRANSIENT = re.compile(r"unavailable|\b503\b|deadline|timed? ?out|temporar", re.I)
_SECRET = re.compile(r"(ya29\.[\w.-]+|Bearer\s+\S+|[A-Za-z0-9_-]{40,})")


def _error_detail(result: dict) -> str:
    """Secret-redacted, truncated reason from a failed tool result; goes to the backend log only."""
    parts = [result.get("executionError")]
    parts += [c.get("text") for c in result.get("content", []) if isinstance(c, dict)]
    text = " ".join(p if isinstance(p, str) else json.dumps(p) for p in parts if p)
    return _SECRET.sub("***", text).strip()[:300]


def _uuid4(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise MCPError("execution_id必须是UUID4") from exc
    if parsed.version != 4:
        raise MCPError("execution_id必须是UUID4")
    return str(parsed)


def _b64_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return base64.b64encode(raw).decode("ascii")


def build_bootstrap_code(execution_id: str, payload: dict, worker_source: str | None = None) -> str:
    """Return the one trusted code string accepted by ``execute_code``.

    Caller data is transported as base64 JSON, never interpolated as Python.
    The worker source is the checked-in runtime_worker.py, not user supplied
    code.  The O_EXCL lock and marker make repeated starts harmless.
    """
    execution_id = _uuid4(execution_id)
    if not isinstance(payload, dict):
        raise MCPError("worker payload必须是对象")
    if worker_source is None:
        worker_source = Path(__file__).with_name("runtime_worker.py").read_text(encoding="utf-8")
    if not isinstance(worker_source, str) or not worker_source:
        raise MCPError("trusted worker source is unavailable")
    source_b64 = base64.b64encode(worker_source.encode("utf-8")).decode("ascii")
    payload_b64 = _b64_json(payload)
    execution_literal = repr(execution_id)
    return f"""# Colab GPU Studio fixed trusted bootstrap; no caller Python is executed.
import base64 as _base64
import json as _json
import os as _os
from pathlib import Path as _Path
import subprocess as _subprocess
import sys as _sys

_execution_id = {execution_literal}
_root = _Path("/content/ai-studio")
_dir = _root / _execution_id
_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
_lock = _dir / ".worker.lock"
_marker = _dir / "marker.json"
_status = _dir / "status.json"
_worker = _dir / "runtime_worker.py"
_payload = _dir / "payload.json"
_log = _dir / "worker.log"
_result = {{"execution_id": _execution_id, "directory": str(_dir), "started": False, "existing": False}}
if _marker.exists():
    _result["existing"] = True
elif _status.exists() or _lock.exists():
    _result["existing"] = True
else:
    try:
        # mode=x maps to an O_EXCL|O_CREAT exclusive lock.
        _lock.open("x").close()
    except FileExistsError:
        _result["existing"] = True
    else:
        _worker.write_bytes(_base64.b64decode({source_b64!r}))
        _payload.write_bytes(_base64.b64decode({payload_b64!r}))
        _marker.write_text(_json.dumps({{"execution_id": _execution_id, "state": "reserved"}}, sort_keys=True), encoding="utf-8")
        _log_handle = _log.open("ab")
        _proc = _subprocess.Popen([_sys.executable, str(_worker), str(_payload)], cwd=str(_dir),
                                  stdin=_subprocess.DEVNULL, stdout=_log_handle, stderr=_subprocess.STDOUT,
                                  shell=False, start_new_session=True)
        _log_handle.close()
        _result.update({{"started": True, "pid": _proc.pid}})
print(_json.dumps(_result, separators=(",", ":")))
"""


def build_poll_code(execution_id: str) -> str:
    """Return a read-only status reader with a hard 100MB output bound."""
    execution_id = _uuid4(execution_id)
    encoded = base64.b64encode(execution_id.encode()).decode("ascii")
    return f"""# Colab GPU Studio fixed read-only recovery probe.
import base64 as _base64
import json as _json
from pathlib import Path as _Path

_execution_id = _base64.b64decode({encoded!r}).decode("ascii")
_dir = _Path("/content/ai-studio") / _execution_id
_status_path = _dir / "status.json"
_meta_path = _dir / "meta.json"
_limit = 100 * 1024 * 1024
_result = {{"execution_id": _execution_id, "state": "running", "outputs": []}}
if not _status_path.exists():
    _result["marker"] = (_dir / "marker.json").exists()
else:
    _status = _json.loads(_status_path.read_text(encoding="utf-8"))
    if isinstance(_status, dict):
        _result.update({{key: _status.get(key) for key in ("state", "error_code", "error_message", "stage", "timings", "stage_started") if key in _status}})
    if _result.get("state") == "completed" and _meta_path.exists():
        _meta = _json.loads(_meta_path.read_text(encoding="utf-8"))
        _total = 0
        for _item in (_meta.get("outputs", []) if isinstance(_meta, dict) else []):
            if not isinstance(_item, dict):
                continue
            _relative = _item.get("path")
            if not isinstance(_relative, str):
                continue
            _file = (_dir / _relative).resolve()
            if _dir.resolve() not in _file.parents or not _file.is_file():
                continue
            _size = _file.stat().st_size
            if _size > _limit or _total + _size > _limit:
                _result = {{"execution_id": _execution_id, "state": "failed", "error_code": "gpu_output_too_large", "error_message": "output exceeds 100MB"}}
                break
            _content = _file.read_bytes()
            _total += len(_content)
            _result.setdefault("outputs", []).append({{**_item, "content": _base64.b64encode(_content).decode("ascii"), "content_encoding": "base64"}})
print(_json.dumps(_result, separators=(",", ":")))
"""


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


class MCPGPUExecutor:
    is_mock = False

    def __init__(
        self,
        store,
        owner_id: str,
        *,
        google: GoogleService | None = None,
        client: httpx.Client | None = None,
        endpoint: str = MCP_ENDPOINT,
        timeout: float = 30.0,
    ):
        if store is None or not owner_id:
            raise MCPError("MCPGPUExecutor需要Repository和owner_id")
        self.store = store
        self.owner_id = owner_id
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout), follow_redirects=False)
        self._owns_client = client is None
        self.google = google or GoogleService(store, owner_id, client=self.client)
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        if self.endpoint != MCP_ENDPOINT:
            # Tests may inject a MockTransport endpoint, but production code must
            # not turn this adapter into an arbitrary outbound proxy.
            parsed = httpx.URL(self.endpoint)
            if parsed.scheme != "https" or not parsed.host:
                raise MCPError("Colab MCP endpoint未验证")

    @classmethod
    def from_environment(cls, store=None, owner_id=None, **kwargs):
        return cls(store, owner_id, **kwargs)

    def _rpc(self, method: str, params: dict) -> Any:
        token = self.google.access_token("colab")
        request_id = str(uuid.uuid4())
        body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json,text/event-stream",
            "Content-Type": "application/json",
        }
        # Every call made here is safe to repeat (create_session has a fixed session id,
        # execute_code has an execution id, status probes only read), so a dropped or
        # timed-out connection is retried a few times instead of killing a running job.
        for attempt in range(_NETWORK_ATTEMPTS):
            try:
                response = self.client.post(self.endpoint, json=body, headers=headers)
                break
            except httpx.TransportError as exc:
                if attempt == _NETWORK_ATTEMPTS - 1:
                    raise MCPError(f"Colab MCP网络请求失败（{type(exc).__name__}，已重试{attempt}次）") from exc
                time.sleep(2 * (attempt + 1))
            except httpx.HTTPError as exc:
                raise MCPError(f"Colab MCP网络请求失败（{type(exc).__name__}）") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise MCPError(f"Colab MCP请求失败（HTTP {response.status_code}）")
        envelope = self._parse_response(response)
        if not isinstance(envelope, dict):
            raise MCPError("Colab MCP响应格式无效")
        if envelope.get("error"):
            raise MCPError("Colab MCP工具调用失败")
        return envelope.get("result")

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict:
        content_type = response.headers.get("content-type", "").lower()
        if "text/event-stream" not in content_type:
            try:
                value = response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise MCPError("Colab MCP JSON响应无效") from exc
            if isinstance(value, dict):
                return value
            raise MCPError("Colab MCP JSON响应不是对象")
        found = None
        for line in response.text.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                candidate = json.loads(data)
            except json.JSONDecodeError as exc:
                raise MCPError("Colab MCP SSE响应无效") from exc
            if isinstance(candidate, dict) and ("result" in candidate or "error" in candidate):
                found = candidate
        if found is None:
            raise MCPError("Colab MCP SSE响应为空")
        return found

    @staticmethod
    def _tool_value(result: Any) -> Any:
        if not isinstance(result, dict):
            raise MCPError("Colab MCP工具返回无效结构")
        if result.get("isError") or result.get("executionError"):
            detail = _error_detail(result)
            log.warning("Colab executor error: %s", detail)  # restricted backend log only
            name = (result.get("executionError") or {}).get("name") if isinstance(result.get("executionError"), dict) else None
            error = MCPTransient if _TRANSIENT.search(detail) else MCPError
            raise error(f"Colab固定执行程序报错（{name or '工具错误'}），详见后台日志")
        if result.get("outputTruncated"):
            raise MCPError("Colab文本传输被截断，结果未保存；请使用受控文件传输或降低产物大小")
        if isinstance(result.get("structuredContent"), dict):
            return MCPGPUExecutor._tool_value(result["structuredContent"])
        if "stdout" in result:
            text = result.get("stdout", "")
            if not isinstance(text, str):
                raise MCPError("Colab stdout结构无效")
            for line in reversed(text.splitlines()):
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        return value
                except json.JSONDecodeError:
                    continue
            raise MCPError("固定执行程序没有返回完整JSON状态")
        if "content" in result:
            for item in result.get("content", []):
                if (
                    isinstance(item, dict)
                    and item.get("type") == "text"
                    and isinstance(item.get("text"), str)
                ):
                    try:
                        return MCPGPUExecutor._tool_value(json.loads(item["text"]))
                    except json.JSONDecodeError:
                        continue
            raise MCPError("MCP工具没有返回可校验的JSON内容")
        return result

    def _tool(self, name: str, arguments: dict) -> Any:
        for attempt in range(_NETWORK_ATTEMPTS):
            try:
                return self._tool_value(self._rpc("tools/call", {"name": name, "arguments": arguments}))
            except MCPTransient:
                if attempt == _NETWORK_ATTEMPTS - 1:
                    raise
                time.sleep(3 * (attempt + 1))

    def _session(self, handle: dict, job_id: str) -> str:
        if not isinstance(handle, dict) or handle.get("owner_id") != self.owner_id:
            raise MCPError("拒绝使用不属于当前owner的Colab租约")
        parent = handle.get("runtime_name")
        if not isinstance(parent, str) or not _RUNTIME.fullmatch(parent):
            raise MCPError("Colab runtime句柄未验证")
        if not isinstance(job_id, str) or not _SAFE_ID.fullmatch(job_id):
            raise MCPError("job id不适合作为稳定MCP sessionId")
        session_id = "studio-" + hashlib.sha256(job_id.encode()).hexdigest()[:32]
        result = self._tool("create_session", {"parent": parent, "sessionId": session_id})
        session = next(
            (
                value.get("name") or value.get("session")
                for value in _walk(result)
                if isinstance(value, dict)
                and isinstance(value.get("name") or value.get("session"), str)
                and (value.get("name") or value.get("session")).startswith(parent + "/sessions/")
            ),
            None,
        )
        if not session or not re.fullmatch(re.escape(parent) + r"/sessions/[a-z][a-z0-9-]{0,62}", session):
            raise MCPError("Colab MCP create_session未返回合法完整session资源名")
        return session

    @staticmethod
    def _payload(job: dict, execution_id: str, root: str) -> dict:
        snapshot = job.get("snapshot")
        if not isinstance(snapshot, dict):
            raise MCPError("job缺少不可变snapshot")
        # Explicit allow-list prevents credentials or arbitrary caller fields from
        # crossing into the remote runtime.
        payload = {
            "execution_id": execution_id,
            "job_id": job.get("id"),
            "execution_dir": root,
            "max_runtime_minutes": job.get("max_runtime_minutes", 10),
            "download_timeout_seconds": min(float(job.get("download_timeout_seconds", 600)), 600),
            "system_smoke": snapshot.get("system_smoke") is True,
            "snapshot": snapshot,
        }
        cache = job.get("drive_cache")
        if isinstance(cache, dict):
            payload["drive_cache"] = {
                "folder_id": cache.get("folder_id"),
                "runtime_folder_id": cache.get("runtime_folder_id"),
                "access_token": cache.get("access_token"),
            }
        return payload

    def start(self, handle: dict, job: dict, execution_id: str) -> dict:
        execution_id = _uuid4(execution_id)
        if not isinstance(job, dict) or not isinstance(job.get("id"), str):
            raise MCPError("job无效")
        session = self._session(handle, job["id"])
        root = "/content/ai-studio/" + execution_id
        code = build_bootstrap_code(execution_id, self._payload(job, execution_id, root))
        result = self._tool("execute_code", {"session": session, "executionId": execution_id, "code": code})
        return {
            "execution_id": execution_id,
            "session": session,
            "idempotent": bool(isinstance(result, dict) and result.get("existing")),
            "remote": result if isinstance(result, dict) else {},
        }

    def poll(self, handle: dict, job: dict) -> dict:
        if not isinstance(job, dict) or not isinstance(job.get("id"), str):
            raise MCPError("job无效")
        execution_id = _uuid4(job.get("execution_id"))
        session = self._session(handle, job["id"])
        probe_id = str(uuid.uuid4())
        code = build_poll_code(execution_id)
        result = self._tool("execute_code", {"session": session, "executionId": probe_id, "code": code})
        if not isinstance(result, dict):
            raise MCPError("Colab状态响应无效")
        state = result.get("state", "running")
        if state in {"reserved", "starting", "running", None}:
            stage = result.get("stage")
            return {
                "state": "running",
                "outputs": [],
                "stage": stage if isinstance(stage, str) else None,
                "timings": result.get("timings") if isinstance(result.get("timings"), dict) else {},
            }
        if state == "failed":
            return {
                "state": "failed",
                "outputs": [],
                "error_code": result.get("error_code", "gpu_worker_failed"),
                "message": str(result.get("error_message", "GPU worker failed"))[:1400],
                "timings": result.get("timings") if isinstance(result.get("timings"), dict) else {},
            }
        if state != "completed":
            raise MCPError("Colab状态值无效")
        outputs = []
        total = 0
        for item in result.get("outputs", []):
            if not isinstance(item, dict) or not isinstance(item.get("content"), str):
                continue
            try:
                content = base64.b64decode(item["content"], validate=True)
            except (ValueError, TypeError) as exc:
                raise MCPError("Colab输出编码无效") from exc
            total += len(content)
            if total > MAX_OUTPUT_BYTES:
                raise MCPError("Colab输出超过100MB上限")
            outputs.append(
                {
                    "filename": str(item.get("filename", "output.bin")),
                    "content": content,
                    "mime": str(item.get("mime", "application/octet-stream")),
                    "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                }
            )
        timings = result.get("timings") if isinstance(result.get("timings"), dict) else {}
        return {"state": "completed", "outputs": outputs, "timings": timings}

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


__all__ = ["MCP_ENDPOINT", "MCPError", "MCPGPUExecutor", "build_bootstrap_code", "build_poll_code"]
