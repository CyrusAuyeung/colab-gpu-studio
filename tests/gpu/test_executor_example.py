from __future__ import annotations

import json
import socket

from app.gpu.compute import GpuConfig, default_executor_factory

PLUGIN = "app.gpu.newgpu.example_executor"


def _job() -> dict:
    return {
        "id": "offline-example-job",
        "owner_id": "owner-1",
        "provider": "colab",
        "executor_type": "trusted",
        "model_id": "offline-fixture-model",
        "workflow_key": "trusted-json-fixture",
        "workflow_version": "1.0.0",
        "params": {"prompt": "contract fixture"},
        "snapshot": {"model_revision": "fixture-revision-1"},
        "deadline_epoch": 4_000_000_000,
        "execution_id": "offline-example-execution",
    }


def _handle() -> dict:
    return {
        "runtime_name": "runtimes/offline-fixture",
        "runtime_id": "offline-runtime-1",
        "request_id": "offline-request-1",
        "owner_id": "owner-1",
    }


def test_default_factory_runs_allowlisted_offline_fixture_lifecycle(monkeypatch):
    """Exercise the real factory/adapter and collect the plugin's JSON bytes."""
    def fail_network(*args, **kwargs):
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.delenv("STUDIO_OFFLINE_TEST", raising=False)
    monkeypatch.setenv("GPU_REAL_ENABLED", "1")
    monkeypatch.setenv("GPU_TRUSTED_EXECUTOR_ENABLED", "1")
    monkeypatch.setenv("GPU_TRUSTED_EXECUTOR_MODULE", PLUGIN)
    monkeypatch.setenv("GPU_TRUSTED_EXECUTOR_ENTRYPOINT", "create_plugin")
    monkeypatch.setenv("GPU_TRUSTED_EXECUTOR_VERSION", "1.0.0")
    monkeypatch.setenv(
        "GPU_TRUSTED_EXECUTOR_ALLOWLIST",
        f"{PLUGIN}:create_plugin@1.0.0",
    )
    monkeypatch.setenv("GPU_TRUSTED_EXECUTOR_CACHE_NAMESPACE", "offline-example")

    job = _job()
    executor = default_executor_factory(job, GpuConfig.from_env())

    started = executor.start(_handle(), job, job["execution_id"])
    polled = executor.poll(_handle(), job)
    collected = polled["outputs"][0]["content"]
    artifact = json.loads(collected)

    assert started["execution_id"] == job["execution_id"]
    assert polled["state"] == "completed"
    assert isinstance(collected, bytes)
    assert artifact == {
        "artifact_type": "trusted-executor-offline-fixture",
        "description": "Deterministic JSON contract fixture; not a model result.",
        "execution_id": job["execution_id"],
        "model_invoked": False,
        "network_used": False,
        "offline": True,
    }
    assert polled["outputs"][0]["metadata"] == {
        "offline_fixture": True,
        "model_result": False,
    }

    cancelled = executor.cancel(_handle(), job)
    cleaned = executor.cleanup(_handle(), job)
    assert cancelled["state"] == "cancelled"
    assert cleaned["state"] == "cleaned"
