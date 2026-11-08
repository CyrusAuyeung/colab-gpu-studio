"""Admin-only trusted executor plugin contract.

The scheduler remains the owner of leases, budget reservation, cancellation state,
and remote runtime cleanup.  This module only adapts the existing executor
``start``/``poll`` calls to a fixed, administrator-loaded Python plugin,
prepares hash-pinned weights when declared, and exposes bounded
``cancel``/``cleanup`` hooks for deployment adapters.

A plugin receives JSON-safe input selected from an immutable admitted job.  It
never receives caller Python, a module name, a URL, a shell command, or the full
workflow graph.  Plugin selection is entirely configuration-driven.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import importlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

PLUGIN_INPUT_SCHEMA = "trusted-executor-input-v1"
PLUGIN_OUTPUT_SCHEMA = "trusted-executor-output-v1"
PLUGIN_CONTRACT_VERSION = "1"
MAX_OUTPUT_BYTES = 100 * 1024 * 1024
_MAX_OUTPUTS = 100
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_SAFE_WEIGHT_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,399}$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_EXECUTOR_TYPE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_MISSING = object()
_FORBIDDEN_KEYS = {
    "code",
    "command",
    "entrypoint",
    "exec",
    "module",
    "python",
    "script",
    "shell",
    "uri",
    "url",
}


class TrustedExecutorError(RuntimeError):
    """Fail-closed error raised by plugin loading or contract validation."""


class TrustedExecutorPlugin(Protocol):
    contract_version: str
    plugin_version: str

    def start(self, payload: dict) -> dict: ...
    def prepare(self, payload: dict) -> dict: ...
    def poll(self, payload: dict) -> dict: ...
    def cancel(self, payload: dict) -> dict: ...
    def cleanup(self, payload: dict) -> dict: ...


class TrustedExecutorPreparedPlugin(TrustedExecutorPlugin, Protocol):
    """Optional extension required when the admitted snapshot has weights."""

    def prepare(self, payload: dict) -> dict: ...


@dataclass(frozen=True)
class PluginAllowlistEntry:
    module: str
    entrypoint: str
    version: str

    @property
    def identity(self) -> dict[str, str]:
        return {"module": self.module, "entrypoint": self.entrypoint, "version": self.version}

    @classmethod
    def parse(cls, value: str) -> "PluginAllowlistEntry":
        if not isinstance(value, str) or value.count(":") != 1 or value.count("@") != 1:
            raise TrustedExecutorError("trusted executor allowlist entry is invalid")
        target, version = value.split("@", 1)
        module, entrypoint = target.split(":", 1)
        if not _IDENTIFIER.fullmatch(module) or not _IDENTIFIER.fullmatch(entrypoint):
            raise TrustedExecutorError("trusted executor module or entrypoint is invalid")
        if not _VERSION.fullmatch(version):
            raise TrustedExecutorError("trusted executor version is invalid")
        return cls(module, entrypoint, version)


def _scan_forbidden(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in _FORBIDDEN_KEYS:
                raise TrustedExecutorError("trusted executor input contains a forbidden field")
            _scan_forbidden(child)
    elif isinstance(value, list):
        for child in value:
            _scan_forbidden(child)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _weight_manifest(job: dict) -> list[dict]:
    """Return the immutable, hash-pinned weight manifest for a trusted job."""
    raw_snapshot = job.get("snapshot")
    snapshot: dict = raw_snapshot if isinstance(raw_snapshot, dict) else {}
    raw_weights = snapshot.get("weight_manifest", _MISSING)
    if raw_weights is _MISSING:
        raw_weights = snapshot.get("trusted_executor_weights")
    if raw_weights is None:
        manifest = snapshot.get("manifest")
        if isinstance(manifest, dict):
            raw_weights = manifest.get("weight_files")
            if not raw_weights and isinstance(manifest.get("dependencies"), dict):
                raw_weights = manifest["dependencies"].get("weight_files")
        else:
            raw_weights = None
    if raw_weights is None:
        # Contract v1 jobs created before weight preparation existed remain
        # loadable. New jobs that declare a manifest must make it non-empty.
        return []
    if not isinstance(raw_weights, list) or not raw_weights:
        raise TrustedExecutorError("trusted executor requires a non-empty weight manifest")

    normalized: list[dict] = []
    seen: set[str] = set()
    for item in raw_weights:
        if not isinstance(item, dict):
            raise TrustedExecutorError("trusted executor weight manifest item is invalid")
        filename = item.get("target") or item.get("filename")
        if (
            not isinstance(filename, str)
            or not _SAFE_WEIGHT_PATH.fullmatch(filename)
            or filename.startswith("/")
            or "\\" in filename
            or any(part in {"", ".", ".."} for part in filename.split("/"))
        ):
            raise TrustedExecutorError("trusted executor weight filename is invalid")
        if filename in seen:
            raise TrustedExecutorError("trusted executor weight manifest contains duplicate filenames")
        seen.add(filename)
        revision = item.get("revision")
        if not isinstance(revision, str) or not revision or len(revision) > 300:
            raise TrustedExecutorError("trusted executor weight revision is required")
        sha256 = item.get("sha256")
        if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
            raise TrustedExecutorError("trusted executor weight sha256 is invalid")
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise TrustedExecutorError("trusted executor weight size is invalid")
        normalized_item = {
            "filename": filename,
            "revision": revision,
            "sha256": sha256.lower(),
            "size": size,
        }
        if item.get("repo_id") is not None:
            repo_id = item.get("repo_id")
            if not isinstance(repo_id, str) or not repo_id or len(repo_id) > 200:
                raise TrustedExecutorError("trusted executor weight repo_id is invalid")
            normalized_item["repo_id"] = repo_id
        normalized.append(normalized_item)
    return sorted(normalized, key=lambda item: item["filename"])


def _weight_manifest_digest(weights: list[dict]) -> str:
    return hashlib.sha256(json.dumps(weights, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _cache_identity(
    config: Any, job: dict, *, validate_weights: bool = True
) -> tuple[str, str, list[dict], str | None]:
    model_id = job.get("model_id")
    snapshot = job.get("snapshot")
    revision = snapshot.get("model_revision") if isinstance(snapshot, dict) else None
    if not isinstance(model_id, str) or not model_id or len(model_id) > 200:
        raise TrustedExecutorError("trusted executor requires model_id")
    if not isinstance(revision, str) or not revision or len(revision) > 300:
        raise TrustedExecutorError("trusted executor requires model_revision")
    namespace = getattr(config, "trusted_executor_cache_namespace", "trusted")
    if not isinstance(namespace, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", namespace):
        raise TrustedExecutorError("trusted executor cache namespace is invalid")
    try:
        weights = _weight_manifest(job)
    except TrustedExecutorError:
        if validate_weights:
            raise
        # Cancellation/cleanup must remain available even when a malformed
        # preparation declaration is found; do not make release depend on it.
        weights = []
    weights_digest = _weight_manifest_digest(weights) if weights else None
    suffix = f"/{weights_digest[:24]}" if weights_digest else ""
    return f"{namespace}/{_sha(model_id)}/{_sha(revision)}{suffix}", revision, weights, weights_digest


def build_trusted_executor_input(
    config: Any, handle: dict, job: dict, execution_id: str | None, operation: str
) -> dict:
    """Build the only payload a trusted plugin may receive."""
    if operation not in {"prepare", "start", "poll", "cancel", "cleanup"}:
        raise TrustedExecutorError("trusted executor operation is invalid")
    if not isinstance(handle, dict):
        raise TrustedExecutorError("trusted executor lease handle is invalid")
    if handle.get("owner_id") != job.get("owner_id"):
        raise TrustedExecutorError("trusted executor lease owner mismatch")
    for key in ("runtime_name", "runtime_id", "request_id"):
        if not isinstance(handle.get(key), str) or not handle[key]:
            raise TrustedExecutorError("trusted executor lease handle is incomplete")
    if not isinstance(job, dict) or not isinstance(job.get("id"), str) or not job["id"]:
        raise TrustedExecutorError("trusted executor job is invalid")
    if execution_id is None:
        execution_id = job.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id:
        raise TrustedExecutorError("trusted executor execution_id is required")
    cache_namespace, revision, weights, weights_digest = _cache_identity(
        config, job, validate_weights=operation not in {"cancel", "cleanup"}
    )
    if operation == "prepare" and not weights:
        raise TrustedExecutorError("trusted executor prepare requires a weight manifest")
    params = job.get("params")
    if not isinstance(params, dict):
        params = (job.get("snapshot") or {}).get("resolved_params", {})
    if not isinstance(params, dict):
        raise TrustedExecutorError("trusted executor parameters must be an object")
    _scan_forbidden(params)
    deadline = job.get("deadline_epoch")
    if operation in {"prepare", "start", "poll"}:
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
            raise TrustedExecutorError("trusted executor lease deadline is required")
        if time.time() > float(deadline):
            raise TrustedExecutorError("trusted executor lease deadline exceeded")
    plugin = getattr(config, "trusted_executor_identity", None)
    if not isinstance(plugin, dict):
        plugin = {
            "module": getattr(config, "trusted_executor_module", ""),
            "entrypoint": getattr(config, "trusted_executor_entrypoint", ""),
            "version": getattr(config, "trusted_executor_version", ""),
        }
    if not all(isinstance(value, str) and value for value in plugin.values()):
        raise TrustedExecutorError("trusted executor identity is unavailable")
    cache = {"namespace": cache_namespace, "model_revision": revision}
    if weights_digest is not None:
        cache["weights_manifest_sha256"] = weights_digest
    return {
        "schema": PLUGIN_INPUT_SCHEMA,
        "contract_version": PLUGIN_CONTRACT_VERSION,
        "operation": operation,
        "executor_type": getattr(config, "trusted_executor_type", "trusted"),
        "execution_id": execution_id,
        "job_id": job["id"],
        "owner_id": job.get("owner_id"),
        "lease": {
            "runtime_name": handle["runtime_name"],
            "runtime_id": handle["runtime_id"],
            "request_id": handle["request_id"],
        },
        "deadline_epoch": deadline,
        "model": {"id": job["model_id"], "revision": revision},
        "workflow": {
            "key": job.get("workflow_key"),
            "version": job.get("workflow_version"),
        },
        "parameters": params,
        "cache": cache,
        "preparation": {
            "required": bool(weights),
            "cache_namespace": cache_namespace,
            "model_revision": revision,
            "weights_manifest_sha256": weights_digest,
            "weights": weights,
        },
        "budget": {
            "reserved_units": job.get("budget_reserved"),
            "estimated_units": job.get("budget_units", job.get("estimated_units")),
        },
        "lifecycle": {
            "cancel_requested": bool(job.get("cancel_requested")),
            "requested_outcome": job.get("requested_outcome"),
            "cleanup_final_state": job.get("cleanup_final_state"),
        },
        "plugin": plugin,
    }


def _validate_preparation_result(payload: dict, preparation: Any) -> dict:
    if not isinstance(preparation, dict):
        raise TrustedExecutorError("trusted executor preparation report is invalid")
    expected = payload["preparation"]
    if preparation.get("cache_namespace") != expected["cache_namespace"]:
        raise TrustedExecutorError("trusted executor preparation cache namespace mismatch")
    if preparation.get("model_revision") != expected["model_revision"]:
        raise TrustedExecutorError("trusted executor preparation model revision mismatch")
    if preparation.get("weights_manifest_sha256") != expected["weights_manifest_sha256"]:
        raise TrustedExecutorError("trusted executor preparation manifest digest mismatch")
    reported = preparation.get("weights")
    if not isinstance(reported, list) or len(reported) != len(expected["weights"]):
        raise TrustedExecutorError("trusted executor preparation weight report is invalid")
    normalized = []
    for wanted, actual in zip(expected["weights"], reported, strict=True):
        if not isinstance(actual, dict):
            raise TrustedExecutorError("trusted executor preparation weight report is invalid")
        if any(actual.get(key) != wanted[key] for key in wanted):
            raise TrustedExecutorError("trusted executor preparation weight identity mismatch")
        if actual.get("verified_sha256") != wanted["sha256"]:
            raise TrustedExecutorError("trusted executor preparation sha256 was not verified")
        normalized.append({**wanted, "verified_sha256": wanted["sha256"]})
    return {
        "cache_namespace": expected["cache_namespace"],
        "model_revision": expected["model_revision"],
        "weights_manifest_sha256": expected["weights_manifest_sha256"],
        "weights": normalized,
    }


def _validate_plugin_result(payload: dict, result: Any) -> dict:
    if not isinstance(result, dict):
        raise TrustedExecutorError("trusted executor returned a non-object")
    if result.get("schema") != PLUGIN_OUTPUT_SCHEMA:
        raise TrustedExecutorError("trusted executor output schema is invalid")
    if result.get("contract_version") != PLUGIN_CONTRACT_VERSION:
        raise TrustedExecutorError("trusted executor output contract version is incompatible")
    if result.get("execution_id") != payload["execution_id"]:
        raise TrustedExecutorError("trusted executor output execution identity mismatch")
    expected_cache = payload["cache"]
    if result.get("cache") != expected_cache:
        raise TrustedExecutorError("trusted executor output cache identity mismatch")
    state = result.get("state")
    if state not in {
        "prepared",
        "running",
        "completed",
        "failed",
        "cancelled",
        "cancelling",
        "cleaned",
        "cleanup_pending",
    }:
        raise TrustedExecutorError("trusted executor output state is invalid")
    preparation = None
    if payload.get("operation") == "prepare":
        if state != "prepared":
            raise TrustedExecutorError("trusted executor preparation state is invalid")
        preparation = _validate_preparation_result(payload, result.get("preparation"))
    outputs = result.get("outputs", [])
    if not isinstance(outputs, list) or len(outputs) > _MAX_OUTPUTS:
        raise TrustedExecutorError("trusted executor output list is invalid")
    total = 0
    normalized = []
    for item in outputs:
        if not isinstance(item, dict):
            raise TrustedExecutorError("trusted executor output item is invalid")
        filename = item.get("filename")
        mime = item.get("mime")
        encoded = item.get("content_base64")
        if not isinstance(filename, str) or not _SAFE_FILENAME.fullmatch(filename):
            raise TrustedExecutorError("trusted executor output filename is invalid")
        if not isinstance(mime, str) or not mime or len(mime) > 120:
            raise TrustedExecutorError("trusted executor output mime is invalid")
        if not isinstance(encoded, str):
            raise TrustedExecutorError("trusted executor output must use base64 JSON")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise TrustedExecutorError("trusted executor output base64 is invalid") from exc
        total += len(content)
        if total > MAX_OUTPUT_BYTES:
            raise TrustedExecutorError("trusted executor output exceeds 100MB")
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            raise TrustedExecutorError("trusted executor output metadata is invalid")
        _scan_forbidden(metadata)
        normalized.append({"filename": filename, "mime": mime, "content": content, "metadata": metadata})
    return {
        "state": state,
        "execution_id": payload["execution_id"],
        "outputs": normalized,
        "timings": result.get("timings") if isinstance(result.get("timings"), dict) else {},
        "error_code": result.get("error_code"),
        "message": str(result.get("message", ""))[:1400],
        "cache": expected_cache,
        **({"preparation": preparation} if preparation is not None else {}),
    }


class TrustedExecutorAdapter:
    """Existing ``GPUExecutor`` lifecycle adapter around one admin plugin."""

    def __init__(self, plugin: TrustedExecutorPlugin, config: Any, identity: PluginAllowlistEntry):
        self.plugin = plugin
        self.config = config
        self.identity = identity

    def _call(self, method: str, payload: dict) -> dict:
        target = getattr(self.plugin, method, None)
        if not callable(target):
            raise TrustedExecutorError(f"trusted executor plugin does not implement {method}")
        try:
            result = target(payload)
        except TrustedExecutorError:
            raise
        except Exception as exc:
            raise TrustedExecutorError(f"trusted executor {method} failed: {type(exc).__name__}") from exc
        return _validate_plugin_result(payload, result)

    def start(self, handle: dict, job: dict, execution_id: str) -> dict:
        payload = build_trusted_executor_input(self.config, handle, job, execution_id, "start")
        if payload["preparation"]["required"]:
            preparation_payload = build_trusted_executor_input(self.config, handle, job, execution_id, "prepare")
            self._call("prepare", preparation_payload)
        result = self._call("start", payload)
        return {
            "execution_id": execution_id,
            "idempotent": result["state"] == "running" and bool(getattr(self.plugin, "idempotent", False)),
            "trusted_plugin": self.identity.identity,
        }

    def poll(self, handle: dict, job: dict) -> dict:
        payload = build_trusted_executor_input(self.config, handle, job, job.get("execution_id"), "poll")
        return self._call("poll", payload)

    def cancel(self, handle: dict, job: dict) -> dict:
        payload = build_trusted_executor_input(self.config, handle, job, job.get("execution_id"), "cancel")
        return self._call("cancel", payload)

    def cleanup(self, handle: dict, job: dict) -> dict:
        payload = build_trusted_executor_input(self.config, handle, job, job.get("execution_id"), "cleanup")
        return self._call("cleanup", payload)


def select_registered_executor_type(config: Any, job: dict) -> str | None:
    """Return the explicitly selected, administrator-registered executor type.

    A job may opt into a trusted plugin from either its admitted top-level
    fields or its immutable snapshot.  The two selectors must agree; absence
    is intentional and means that no trusted plugin is selected.
    """
    snapshot = job.get("snapshot") if isinstance(job, dict) else None
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    selected = [
        value
        for value in (job.get("executor_type"), snapshot.get("executor_type"))
        if value is not None
    ]
    if not selected:
        return None
    if len(set(selected)) != 1:
        raise TrustedExecutorError("trusted executor_type selectors conflict")
    executor_type = selected[0]
    if not isinstance(executor_type, str) or not _EXECUTOR_TYPE.fullmatch(executor_type):
        raise TrustedExecutorError("trusted executor_type is not registered")
    registered = getattr(config, "trusted_executor_type", "trusted")
    if not isinstance(registered, str) or not _EXECUTOR_TYPE.fullmatch(registered):
        raise TrustedExecutorError("trusted executor registration is invalid")
    if executor_type != registered:
        raise TrustedExecutorError("trusted executor_type is not registered")
    return executor_type


def load_trusted_executor(config: Any) -> TrustedExecutorAdapter:
    """Load exactly the administrator-selected, allowlisted Python entrypoint."""
    if not getattr(config, "trusted_executor_enabled", False):
        raise TrustedExecutorError("trusted executor is disabled")
    selected = PluginAllowlistEntry(
        str(getattr(config, "trusted_executor_module", "")),
        str(getattr(config, "trusted_executor_entrypoint", "")),
        str(getattr(config, "trusted_executor_version", "")),
    )
    if not _IDENTIFIER.fullmatch(selected.module) or not _IDENTIFIER.fullmatch(selected.entrypoint):
        raise TrustedExecutorError("trusted executor module or entrypoint is invalid")
    if not _VERSION.fullmatch(selected.version):
        raise TrustedExecutorError("trusted executor version is invalid")
    allowlist = tuple(PluginAllowlistEntry.parse(item) for item in getattr(config, "trusted_executor_allowlist", ()))
    if selected not in allowlist:
        raise TrustedExecutorError("trusted executor selection is not in the administrator allowlist")
    try:
        module = importlib.import_module(selected.module)
        factory = getattr(module, selected.entrypoint)
    except (ImportError, AttributeError) as exc:
        raise TrustedExecutorError("trusted executor entrypoint could not be loaded") from exc
    if not callable(factory):
        raise TrustedExecutorError("trusted executor entrypoint is not callable")
    try:
        plugin = factory()
    except Exception as exc:
        raise TrustedExecutorError("trusted executor plugin initialization failed") from exc
    if getattr(plugin, "contract_version", None) != PLUGIN_CONTRACT_VERSION:
        raise TrustedExecutorError("trusted executor contract version is incompatible")
    if getattr(plugin, "plugin_version", None) != selected.version:
        raise TrustedExecutorError("trusted executor plugin version is not allowlisted")
    for method in ("start", "poll", "cancel", "cleanup"):
        if not callable(getattr(plugin, method, None)):
            raise TrustedExecutorError(f"trusted executor plugin does not implement {method}")
    return TrustedExecutorAdapter(plugin, config, selected)


__all__ = [
    "PLUGIN_CONTRACT_VERSION",
    "PLUGIN_INPUT_SCHEMA",
    "PLUGIN_OUTPUT_SCHEMA",
    "PluginAllowlistEntry",
    "TrustedExecutorAdapter",
    "TrustedExecutorError",
    "TrustedExecutorPreparedPlugin",
    "build_trusted_executor_input",
    "select_registered_executor_type",
    "load_trusted_executor",
]
