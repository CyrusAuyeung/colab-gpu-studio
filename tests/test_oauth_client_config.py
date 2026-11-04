from __future__ import annotations

import httpx
from app import oauth_config_routes
from app.common import APIError, current_user, get_store
from app.config import settings
from app.db import Store
from app.gpu.google import GoogleService
from app.gpu.repository import Repository
from app.security import decrypt_secret, encrypt_secret
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

CLIENT_ID = "1234567890-example.apps.googleusercontent.com"
CALLBACK = "http://localhost:8788/api/gpu/auth/google/callback"


def client(monkeypatch, tmp_path, user=None):
    monkeypatch.setattr(settings, "encryption_key", "X" * 43 + "=")
    monkeypatch.setattr(settings, "site_url", "http://localhost:8788")
    store = Store(f"sqlite:///{tmp_path / 'oauth.sqlite'}")
    app = FastAPI()
    async def api_error(_request: Request, exc: APIError):
        return JSONResponse({"error": exc.message}, status_code=exc.status)
    app.add_exception_handler(APIError, api_error)
    app.include_router(oauth_config_routes.router)
    app.dependency_overrides[get_store] = lambda: store
    if user is not None:
        app.dependency_overrides[current_user] = lambda: user
    return TestClient(app), store


def test_config_requires_login_and_admin(monkeypatch, tmp_path):
    unauthenticated, _ = client(monkeypatch, tmp_path)
    assert unauthenticated.get("/api/gpu/auth/google/config").status_code == 401
    non_admin, _ = client(monkeypatch, tmp_path, {"id": "u1", "role": "user"})
    response = non_admin.post("/api/gpu/auth/google/config", json={"confirmed": True, "client_id": CLIENT_ID, "client_secret": "secret-123"})
    assert response.status_code == 403


def test_web_client_is_validated_and_secret_is_never_returned(monkeypatch, tmp_path):
    c, store = client(monkeypatch, tmp_path, {"id": "admin", "role": "admin"})
    bad = c.post("/api/gpu/auth/google/config", json={"confirmed": True, "client_json": {"installed": {}}})
    assert bad.status_code == 422
    bad = c.post("/api/gpu/auth/google/config", json={"confirmed": True, "client_json": {"web": {"client_id": CLIENT_ID, "client_secret": "secret-123", "redirect_uris": ["https://evil.invalid/callback"]}}})
    assert bad.status_code == 422
    saved = c.post("/api/gpu/auth/google/config", json={"confirmed": True, "client_json": {"web": {"client_id": CLIENT_ID, "client_secret": "secret-123", "redirect_uris": [CALLBACK]}, "auth_uri": "https://evil.invalid", "token_uri": "https://evil.invalid"}})
    assert saved.status_code == 200
    assert "secret-123" not in saved.text
    got = c.get("/api/gpu/auth/google/config")
    assert got.json()["data"] == {"configured": True, "client_id": CLIENT_ID, "redirect_uri": CALLBACK, "source": "encrypted_database"}
    row = Repository(store).get("credentials", oauth_config_routes.config_id("admin"), "admin")
    assert decrypt_secret(row["encrypted_client_secret"]) == "secret-123"
    assert "encrypted_client_secret" not in got.text


def test_callback_uses_state_owner_database_client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "site_url", "http://localhost:8788")
    c, store = client(monkeypatch, tmp_path, {"id": "admin", "role": "admin"})
    assert c.post("/api/gpu/auth/google/config", json={"confirmed": True, "client_id": CLIENT_ID, "client_secret": "db-secret"}).status_code == 200
    repo = Repository(store)
    service = GoogleService(repo, "admin")
    started = service.start("drive")
    monkeypatch.setenv("GPU_GOOGLE_CLIENT_ID", "9999999999-env.apps.googleusercontent.com")
    monkeypatch.setenv("GPU_GOOGLE_CLIENT_SECRET", "wrong-secret")
    seen = {}

    def handler(request):
        seen.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600})

    service.client = httpx.Client(transport=httpx.MockTransport(handler))
    service.callback("code", started["state"])
    assert seen["client_id"] == CLIENT_ID
    assert seen["client_secret"] == "db-secret"


def test_connected_google_client_cannot_be_replaced(monkeypatch, tmp_path):
    c, store = client(monkeypatch, tmp_path, {"id": "admin", "role": "admin"})
    assert c.post("/api/gpu/auth/google/config", json={"confirmed": True, "client_id": CLIENT_ID, "client_secret": "secret-123"}).status_code == 200
    Repository(store).create("credentials", "admin", {"provider": "google", "purpose": "drive", "encrypted_refresh_token": encrypt_secret("refresh")})
    replacement = c.post("/api/gpu/auth/google/config", json={"confirmed": True, "client_id": "1234567890-other.apps.googleusercontent.com", "client_secret": "secret-456"})
    assert replacement.status_code == 409
