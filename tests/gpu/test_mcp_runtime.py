from __future__ import annotations

import json
from pathlib import Path

from app.gpu import runtime_worker


def _payload(root: Path, *, smoke: bool = True, executor: str = "cuda-smoke") -> dict:
    return {
        "execution_id": "11111111-1111-4111-8111-111111111111",
        "execution_dir": str(root),
        "system_smoke": smoke,
        "snapshot": {
            "system_smoke": smoke,
            "manifest": {"state": "published", "reviewed": True, "executor": executor},
            "bound_graph": {},
        },
    }


def test_system_smoke_requires_snapshot_flag_and_emits_real_json(monkeypatch, tmp_path):
    def fake_smoke(root):
        output = root / "outputs" / "cuda-smoke.json"
        output.parent.mkdir()
        output.write_text('{"cuda_available":true}', encoding="utf-8")
        return {
            "filename": output.name,
            "mime": "application/json",
            "path": "outputs/cuda-smoke.json",
            "size": output.stat().st_size,
        }

    monkeypatch.setattr(runtime_worker, "run_cuda_smoke", fake_smoke)
    runtime_worker.run_job(_payload(tmp_path), tmp_path)
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["state"] == "completed"
    assert status["outputs"] == [
        {
            "filename": "cuda-smoke.json",
            "mime": "application/json",
            "path": "outputs/cuda-smoke.json",
            "size": 23,
        }
    ]


def test_cuda_smoke_manifest_cannot_be_enabled_by_manifest_alone(tmp_path):
    payload = _payload(tmp_path, smoke=False, executor="cuda-smoke")
    payload["snapshot"]["manifest"] = {"state": "published", "reviewed": True, "executor": "cuda-smoke"}
    runtime_worker.run_job(payload, tmp_path)
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["state"] == "failed"
    assert "Comfy" in status["error_message"]


def test_weight_manifest_rejects_urls_and_path_traversal():
    try:
        runtime_worker._validate_weights(
            {"weights": [{"repo_id": "x/y", "filename": "../x", "revision": "0" * 40, "sha256": "0" * 64}]}
        )
    except runtime_worker.WorkerError as exc:
        assert "path traversal" in str(exc)
    else:
        raise AssertionError("unsafe weight path was accepted")
