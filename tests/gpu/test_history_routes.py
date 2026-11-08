from app.common import APIError, current_user
from app.history_routes import router
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


def api_error_handler(request, exc):
    return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)


class Repo:
    def __init__(self):
        self.rows = {
            "instances": [{"id": "instance-1", "owner_id": "owner", "state": "deleted"}],
            "jobs": [
                {
                    "id": "job-1",
                    "owner_id": "owner",
                    "instance_id": "instance-1",
                    "state": "failed",
                    "provider": "colab",
                    "error_code": "gpu_failed",
                    "error_message": "failed https://private.example/token?x=secret",
                    "worker_timings": {"sampling": 4.2},
                    "output_asset_ids": ["asset-1"],
                },
                {"id": "job-other", "owner_id": "other", "instance_id": "instance-1", "state": "completed"},
            ],
            "events": [
                {"id": "event-1", "owner_id": "owner", "job_id": "job-1", "state": "failed", "created_at": "2026-11-08T00:00:02Z", "message": "failed https://private.example/run"},
                {"id": "event-other", "owner_id": "other", "job_id": "job-1", "state": "failed", "message": "cross-owner"},
            ],
            "assets": [{"id": "asset-1", "owner_id": "owner", "filename": "result.png", "mime": "image/png", "storage_key": "private/key"}],
        }

    def get(self, kind, record_id, owner_id=None):
        return next((row for row in self.rows.get(kind, []) if row.get("id") == record_id and (owner_id is None or row.get("owner_id") == owner_id)), None)

    def list(self, kind, owner_id=None, project_id=None):
        return [row for row in self.rows.get(kind, []) if owner_id is None or row.get("owner_id") == owner_id]


def test_history_is_owner_scoped_persisted_only_and_redacts_error_logs():
    app = FastAPI()
    app.add_exception_handler(APIError, api_error_handler)
    app.state.repo = Repo()
    app.state.store = object()
    app.dependency_overrides[current_user] = lambda: {"id": "owner", "role": "user"}
    app.include_router(router)

    with TestClient(app) as client:
        response = client.get("/api/gpu/instances/instance-1/history")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["offline"] is True
    assert [job["job_id"] for job in data["jobs"]] == ["job-1"]
    job = data["jobs"][0]
    assert job["error"]["code"] == "gpu_failed"
    assert "https://" not in str(job)
    assert "token" not in str(job).lower()
    assert job["artifacts"][0]["filename"] == "result.png"
    assert "storage_key" not in str(job)


def test_history_requires_the_instance_to_belong_to_the_current_owner():
    app = FastAPI()
    app.add_exception_handler(APIError, api_error_handler)
    app.state.repo = Repo()
    app.state.store = object()
    app.dependency_overrides[current_user] = lambda: {"id": "other", "role": "user"}
    app.include_router(router)

    with TestClient(app) as client:
        response = client.get("/api/gpu/instances/instance-1/history")

    assert response.status_code == 404
