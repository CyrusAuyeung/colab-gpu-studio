from __future__ import annotations

import sys
import types

import pytest
from app.gpu.compute import GpuConfig, ProviderUnavailable, default_executor_factory
from app.gpu.newgpu.executor_plugins import TrustedExecutorError

PLUGIN_NAME = "trusted_contract_plugin"


class FakeTrustedPlugin:
    contract_version = "1"
    plugin_version = "1.2.0"

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    @staticmethod
    def _result(payload: dict, state: str, **extra) -> dict:
        return {
            "schema": "trusted-executor-output-v1",
            "contract_version": "1",
            "execution_id": payload["execution_id"],
            "state": state,
            "cache": payload["cache"],
            **extra,
        }

    def start(self, payload: dict) -> dict:
        self.calls.append(("start", payload))
        return self._result(payload, "running")

    def poll(self, payload: dict) -> dict:
        self.calls.append(("poll", payload))
        return self._result(
            payload,
            "completed",
            outputs=[
                {
                    "filename": "trusted.json",
                    "mime": "application/json",
                    "content_base64": "eyJvayI6dHJ1ZX0=",
                    "metadata": {"trusted": True},
                }
            ],
        )

    def cancel(self, payload: dict) -> dict:
        self.calls.append(("cancel", payload))
        return self._result(payload, "cancelled")

    def cleanup(self, payload: dict) -> dict:
        self.calls.append(("cleanup", payload))
        return self._result(payload, "cleaned")


@pytest.fixture
def trusted_module(monkeypatch):
    plugin = FakeTrustedPlugin()
    module = types.ModuleType(PLUGIN_NAME)
    module.create_plugin = lambda: plugin
    monkeypatch.setitem(sys.modules, PLUGIN_NAME, module)
    return plugin


def _config(**overrides) -> GpuConfig:
    values = {
        "real_enabled": True,
        "trusted_executor_enabled": True,
        "trusted_executor_module": PLUGIN_NAME,
        "trusted_executor_entrypoint": "create_plugin",
        "trusted_executor_version": "1.2.0",
        "trusted_executor_allowlist": (f"{PLUGIN_NAME}:create_plugin@1.2.0",),
        "trusted_executor_cache_namespace": "admin-contract",
    }
    values.update(overrides)
    return GpuConfig(**values)


def _job(**overrides) -> dict:
    value = {
        "id": "job-1",
        "owner_id": "owner-1",
        "provider": "colab",
        "executor_type": "trusted",
        "model_id": "laya-demo",
        "workflow_key": "laya-json",
        "workflow_version": "1.0.0",
        "params": {"prompt": "hello"},
        "snapshot": {"model_revision": "model-rev-1", "workflow_sha256": "a" * 64},
        "deadline_epoch": 4_000_000_000,
        "execution_id": "exec-1",
    }
    value.update(overrides)
    return value


def _handle() -> dict:
    return {
        "runtime_name": "runtimes/trusted-runtime",
        "runtime_id": "runtime-1",
        "request_id": "request-1",
        "owner_id": "owner-1",
    }


def test_trusted_executor_is_disabled_by_default():
    executor = default_executor_factory({"provider": "colab"}, GpuConfig())
    with pytest.raises(ProviderUnavailable):
        executor.start(_handle(), _job(), "exec-1")


def test_enabled_plugin_does_not_override_ordinary_colab_jobs(monkeypatch, trusted_module):
    sentinel = object()
    monkeypatch.setattr('app.gpu.compute._mcp_executor', lambda *args, **kwargs: sentinel)
    executor = default_executor_factory(_job(executor_type=None), _config())
    assert executor is sentinel
    assert trusted_module.calls == []


def test_snapshot_executor_type_selects_registered_plugin(trusted_module):
    job = _job(executor_type=None, snapshot={"executor_type": "trusted", "model_revision": "model-rev-1"})
    executor = default_executor_factory(job, _config())
    assert type(executor).__name__ == "TrustedExecutorAdapter"
    executor.start(_handle(), job, "exec-1")
    assert trusted_module.calls[0][0] == "start"


def test_unregistered_executor_type_is_rejected(trusted_module):
    with pytest.raises(TrustedExecutorError, match="registered"):
        default_executor_factory(_job(executor_type="not-registered"), _config())


def test_builtin_system_and_prep_jobs_always_use_mcp(monkeypatch, trusted_module):
    class SentinelMCP:
        def start(self, *args):
            return {"mcp": True}

    monkeypatch.setattr(
        "app.gpu.mcp.MCPGPUExecutor.from_environment",
        classmethod(lambda cls, **kwargs: SentinelMCP()),
    )
    for job in (
        _job(executor_type="trusted", kind="system-cuda-smoke", model_id="system-cuda-smoke", snapshot={}),
        _job(executor_type="trusted", kind="system-cuda-prep", model_id="system-cuda-prep", snapshot={}),
        _job(executor_type="trusted", model_id="regular", snapshot={"prep": True}),
    ):
        executor = default_executor_factory(job, _config())
        assert isinstance(executor, SentinelMCP)
    assert trusted_module.calls == []


def test_factory_loads_allowlisted_plugin_and_runs_start_poll(trusted_module):
    executor = default_executor_factory(_job(), _config())

    started = executor.start(_handle(), _job(), "exec-1")
    completed = executor.poll(_handle(), _job())

    assert started["execution_id"] == "exec-1"
    assert completed["state"] == "completed"
    assert completed["outputs"][0]["content"] == b'{"ok":true}'
    assert [name for name, _ in trusted_module.calls] == ["start", "poll"]
    payload = trusted_module.calls[0][1]
    assert payload["cache"]["model_revision"] == "model-rev-1"
    assert payload["cache"]["namespace"].startswith("admin-contract/")
    assert "module" not in payload and "script" not in payload and "url" not in payload


def test_plugin_selection_is_admin_only_and_allowlist_is_exact(trusted_module):
    job = _job(executor_plugin={"module": "attacker.module", "entrypoint": "run"}, script="rm -rf /")
    executor = default_executor_factory(job, _config())

    executor.start(_handle(), job, "exec-1")
    assert trusted_module.calls[0][1]["plugin"] == {
        "module": PLUGIN_NAME,
        "entrypoint": "create_plugin",
        "version": "1.2.0",
    }

    with pytest.raises(TrustedExecutorError, match="allowlist"):
        default_executor_factory(
            _job(),
            _config(trusted_executor_allowlist=("other.module:create_plugin@1.2.0",)),
        )


def test_input_rejects_client_script_url_and_requires_model_revision(trusted_module):
    executor = default_executor_factory(_job(params={"script": "print(1)"}), _config())
    with pytest.raises(TrustedExecutorError, match="forbidden"):
        executor.start(_handle(), _job(params={"script": "print(1)"}), "exec-1")

    with pytest.raises(TrustedExecutorError, match="model_revision"):
        executor.start(_handle(), _job(snapshot={}), "exec-1")


def test_output_cache_revision_mismatch_is_rejected(trusted_module):
    class BadCache(FakeTrustedPlugin):
        def poll(self, payload):
            self.calls.append(("poll", payload))
            return self._result(
                payload,
                "completed",
                cache={"namespace": "other/namespace", "model_revision": "other-revision"},
                outputs=[],
            )

    bad = BadCache()
    sys.modules[PLUGIN_NAME].create_plugin = lambda: bad
    executor = default_executor_factory(_job(), _config())
    executor.start(_handle(), _job(), "exec-1")
    with pytest.raises(TrustedExecutorError, match="cache"):
        executor.poll(_handle(), _job())


def test_cancel_cleanup_are_bounded_by_same_lease_contract(trusted_module):
    executor = default_executor_factory(_job(), _config())
    expired = _job(deadline_epoch=1)

    with pytest.raises(TrustedExecutorError, match="deadline"):
        executor.start(_handle(), expired, "exec-1")

    cancelled = executor.cancel(_handle(), expired)
    cleaned = executor.cleanup(_handle(), expired)
    assert cancelled["state"] == "cancelled"
    assert cleaned["state"] == "cleaned"
    assert [name for name, _ in trusted_module.calls] == ["cancel", "cleanup"]
