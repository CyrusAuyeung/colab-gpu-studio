from __future__ import annotations

import json

import pytest
from app.gpu import runtime_worker as worker

INSTANCE = "11111111-1111-4111-8111-111111111111"
COMMIT = "a" * 40
SHA = "b" * 64


def _manifest(*, dependency_signature="deps-v1"):
    return {
        "state": "published",
        "reviewed": True,
        "comfy_commit": COMMIT,
        "node_allowlist": ["CheckpointLoaderSimple"],
        "custom_nodes": [],
        "dependency_signature": dependency_signature,
        "weight_files": [
            {
                "source": "drive",
                "drive_file_id": "1" * 33,
                "target": "checkpoints/model.safetensors",
                "size": 4,
                "sha256": SHA,
            }
        ],
    }


class FakeProcess:
    next_pid = 4100

    def __init__(self, cwd):
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.cwd = str(cwd)
        self.alive = True
        self.terminated = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True
        self.alive = False

    def wait(self, timeout=None):
        self.alive = False
        return 0

    def kill(self):
        self.alive = False


def test_service_dir_is_uuid_derived_under_fixed_root(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "COMFY_SERVICE_ROOT", tmp_path / "services")

    service = worker._comfy_service_dir(INSTANCE)

    assert service.parent == (tmp_path / "services").resolve()
    assert service.name != "../../arbitrary"
    assert service.name == worker._instance_service_name(INSTANCE)
    with pytest.raises(worker.WorkerError):
        worker._comfy_service_dir(str(tmp_path / "not-a-uuid"))


def test_service_process_is_hot_reused_only_after_real_health_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "COMFY_SERVICE_ROOT", tmp_path / "services")
    (worker._comfy_service_dir(INSTANCE) / "ComfyUI").mkdir(parents=True)
    processes = []

    def fake_popen(args, **kwargs):
        process = FakeProcess(kwargs["cwd"])
        processes.append(process)
        return process

    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(worker, "_process_metadata", lambda pid: {
        "start_id": f"start-{pid}",
        "cmdline": [worker.sys.executable, "main.py", "--listen", "127.0.0.1", "--port", "8188"],
        "cwd": str(worker._comfy_service_dir(INSTANCE) / "ComfyUI"),
    })
    probes = []

    def fake_http(url, *args, **kwargs):
        probes.append(url)
        return {"system": "ok"}

    monkeypatch.setattr(worker, "_http_json", fake_http)
    manifest = _manifest()
    weights = worker._validate_weights(manifest)
    first = worker._ensure_comfy_service(
        INSTANCE,
        COMMIT,
        [],
        weights,
        manifest=manifest,
        expected_fingerprint=worker._service_fingerprint(COMMIT, [], weights, manifest),
    )
    second = worker._ensure_comfy_service(
        INSTANCE,
        COMMIT,
        [],
        weights,
        manifest=manifest,
        expected_fingerprint=worker._service_fingerprint(COMMIT, [], weights, manifest),
    )

    assert first["cache_reason"] == "cold_start"
    assert second["cache_reason"] == "hot_reuse"
    assert len(processes) == 1
    assert probes == ["http://127.0.0.1:8188/system_stats"] * 2
    state = json.loads((worker._comfy_service_dir(INSTANCE) / "state.json").read_text())
    assert state["pid"] == processes[0].pid
    assert state["process_start_id"] == f"start-{processes[0].pid}"
    assert state["env_fingerprint"] == first["env_fingerprint"]


def test_fingerprint_mismatch_stops_only_owned_process_and_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "COMFY_SERVICE_ROOT", tmp_path / "services")
    (worker._comfy_service_dir(INSTANCE) / "ComfyUI").mkdir(parents=True)
    processes = []

    def fake_popen(args, **kwargs):
        process = FakeProcess(kwargs["cwd"])
        processes.append(process)
        return process

    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(worker, "_process_metadata", lambda pid: {
        "start_id": f"start-{pid}",
        "cmdline": [worker.sys.executable, "main.py", "--listen", "127.0.0.1", "--port", "8188"],
        "cwd": str(worker._comfy_service_dir(INSTANCE) / "ComfyUI"),
    })
    monkeypatch.setattr(worker, "_http_json", lambda *args, **kwargs: {"system": "ok"})

    old_manifest = _manifest(dependency_signature="deps-v1")
    old_weights = worker._validate_weights(old_manifest)
    first = worker._ensure_comfy_service(
        INSTANCE, COMMIT, [], old_weights, manifest=old_manifest,
        expected_fingerprint=worker._service_fingerprint(COMMIT, [], old_weights, old_manifest),
    )
    new_manifest = _manifest(dependency_signature="deps-v2")
    new_weights = worker._validate_weights(new_manifest)
    second = worker._ensure_comfy_service(
        INSTANCE, COMMIT, [], new_weights, manifest=new_manifest,
        expected_fingerprint=worker._service_fingerprint(COMMIT, [], new_weights, new_manifest),
    )

    assert first["pid"] != second["pid"]
    assert processes[0].terminated is True
    assert second["cache_reason"] == "restart_fingerprint_mismatch"


def test_unowned_pid_is_never_killed(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "COMFY_SERVICE_ROOT", tmp_path / "services")
    service = worker._comfy_service_dir(INSTANCE)
    service.mkdir(parents=True)
    (service / "state.json").write_text(json.dumps({
        "pid": 9999,
        "process_start_id": "foreign",
        "env_fingerprint": "old",
    }))
    monkeypatch.setattr(worker, "_process_metadata", lambda pid: {
        "start_id": "foreign",
        "cmdline": ["/bin/sh", "-c", "evil"],
        "cwd": "/tmp",
    })
    with pytest.raises(worker.WorkerError, match="ownership"):
        worker._stop_service_if_owned(service)


def test_workbench_actions_are_fixed_json_and_logs_are_redacted(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(worker, "COMFY_SERVICE_ROOT", tmp_path / "services")
    service = worker._comfy_service_dir(INSTANCE)
    service.mkdir(parents=True)
    (service / "comfy.log").write_text("Authorization: Bearer secret-token\npassword=hunter2\nready\n")
    monkeypatch.setattr(worker, "_read_service_state", lambda path: {
        "pid": 1,
        "process_start_id": "s",
        "env_fingerprint": "fp",
        "cache_reason": "cold_start",
    })
    monkeypatch.setattr(worker, "_process_metadata", lambda pid: {
        "start_id": "s",
        "cmdline": [worker.sys.executable, "main.py", "--listen", "127.0.0.1", "--port", "8188"],
        "cwd": str(service / "ComfyUI"),
    })

    result = worker._handle_workbench_action({"action": "read_safe_log", "instance_id": INSTANCE, "max_bytes": 4096})

    assert result["state"] == "ok"
    assert "secret-token" not in result["log"]
    assert "hunter2" not in result["log"]
    assert "[redacted]" in result["log"]
    with pytest.raises(worker.WorkerError):
        worker._handle_workbench_action({"action": "shell", "instance_id": INSTANCE, "command": "id"})

    action_file = tmp_path / "action.json"
    action_file.write_text(json.dumps({"action": "health", "instance_id": INSTANCE}))
    monkeypatch.setattr(worker, "_handle_workbench_action", lambda request: {"state": "unknown", "reason": "probe failed"})
    assert worker.main(["workbench", str(action_file)]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "unknown"


def test_workbench_action_rejects_arbitrary_action_file_path_fields():
    with pytest.raises(worker.WorkerError):
        worker._validate_workbench_request({"action": "health", "instance_id": INSTANCE, "url": "http://evil"})
