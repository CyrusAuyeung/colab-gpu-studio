from __future__ import annotations

from app import main
from app.common import APIError, current_user, get_store
from app.db import Store
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


def client(tmp_path):
    app = FastAPI()

    async def api_error(_request: Request, exc: APIError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    app.add_exception_handler(APIError, api_error)
    app.include_router(main.router, prefix="/api/gpu")
    store = Store(f"sqlite:///{tmp_path / 's.sqlite'}")
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[current_user] = lambda: {"id": "u1", "role": "user"}
    return TestClient(app)


def test_colab_specs_offline_is_clear_409_not_500(monkeypatch, tmp_path):
    monkeypatch.setenv("STUDIO_OFFLINE_TEST", "1")
    r = client(tmp_path).get("/api/gpu/compute/specs?provider=colab")
    assert r.status_code == 409 and r.json()["error"]["code"] == "OFFLINE_MODE"


def test_colab_specs_real_disabled_is_clear_409(monkeypatch, tmp_path):
    monkeypatch.delenv("STUDIO_OFFLINE_TEST", raising=False)
    monkeypatch.setenv("GPU_REAL_ENABLED", "0")
    r = client(tmp_path).get("/api/gpu/compute/specs?provider=colab")
    assert r.status_code == 409 and r.json()["error"]["code"] == "REAL_GPU_DISABLED"


def test_mock_specs_still_work(tmp_path):
    assert client(tmp_path).get("/api/gpu/compute/specs?provider=mock").status_code == 200
