from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from app import account_routes, oauth_config_routes
from app.common import APIError, current_user, get_store
from app.config import settings
from app.db import Store
from app.gpu.accounts import AccountService
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


@pytest.mark.parametrize("purpose", ["colab", "drive"])
def test_new_account_auth_uses_owner_web_config(monkeypatch, tmp_path, purpose):
    for name in ("GPU_GOOGLE_CLIENT_ID", "GPU_GOOGLE_CLIENT_SECRET", "GPU_GOOGLE_SECRET"):
        monkeypatch.delenv(name, raising=False)
    c, store = client(monkeypatch, tmp_path, {"id": "admin", "role": "admin"})
    c.app.include_router(account_routes.router)
    c.app.state.store = store
    saved = c.post("/api/gpu/auth/google/config", json={
        "confirmed": True, "client_id": CLIENT_ID, "client_secret": "owner-web-secret",
    })
    assert saved.status_code == 200
    assert c.get("/api/gpu/auth/google/config").json()["data"]["configured"] is True
    repo = Repository(store)
    account = AccountService(repo, "admin").create({"alias": "New account"}, "new-account")
    path = f"/api/gpu/accounts/{account['account_id']}/auth/{purpose}/start"
    headers = {"Idempotency-Key": f"auth-{purpose}"}
    response = c.post(path, json={"purpose": purpose}, headers=headers)
    assert response.status_code == 200, response.text
    started = response.json()["data"]
    assert parse_qs(urlparse(started["authorization_url"]).query)["client_id"] == [CLIENT_ID]
    assert c.post(path, json={"purpose": purpose}, headers=headers).json()["data"] == started
    seen = {}

    def handler(request):
        seen.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600})

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        result = GoogleService(repo, None, client=transport).callback("code", started["state"])
    assert result["account_id"] == account["account_id"]
    assert seen["client_id"] == CLIENT_ID
    assert seen["client_secret"] == "owner-web-secret"


def test_account_specific_oauth_application_takes_precedence(monkeypatch, tmp_path):
    c, store = client(monkeypatch, tmp_path, {"id": "admin", "role": "admin"})
    assert c.post("/api/gpu/auth/google/config", json={
        "confirmed": True, "client_id": CLIENT_ID, "client_secret": "owner-web-secret",
    }).status_code == 200
    repo = Repository(store)
    repo.create("credentials", "admin", {
        "provider": "google", "purpose": "oauth_client_config", "account_id": "account-a",
        "client_id": "account-specific.apps.googleusercontent.com",
        "encrypted_client_secret": encrypt_secret("account-secret"),
    })
    service = GoogleService(repo, "admin", account_id="account-a")
    try:
        started = service.start("drive")
        assert parse_qs(urlparse(started["authorization_url"]).query)["client_id"] == [
            "account-specific.apps.googleusercontent.com",
        ]
    finally:
        service.close()


@pytest.mark.parametrize("owner, binding", [("other-owner", {}), ("admin", {"account_id": "other-account"}), ("admin", {"revoked": True})])
def test_shared_oauth_config_does_not_cross_security_boundaries(monkeypatch, tmp_path, owner, binding):
    from app.gpu.google import GoogleConfigurationError

    for name in ("GPU_GOOGLE_CLIENT_ID", "GPU_GOOGLE_CLIENT_SECRET", "GPU_GOOGLE_SECRET"):
        monkeypatch.delenv(name, raising=False)
    _, store = client(monkeypatch, tmp_path, {"id": "admin", "role": "admin"})
    repo = Repository(store)
    repo.create("credentials", owner, {
        "provider": "google", "purpose": "oauth_client_config", "client_id": CLIENT_ID,
        "encrypted_client_secret": encrypt_secret("not-shared"), **binding,
    })
    service = GoogleService(repo, "admin", account_id="new-account")
    try:
        with pytest.raises(GoogleConfigurationError):
            service.start("drive")
    finally:
        service.close()


def test_connected_google_client_cannot_be_replaced(monkeypatch, tmp_path):
    c, store = client(monkeypatch, tmp_path, {"id": "admin", "role": "admin"})
    assert c.post("/api/gpu/auth/google/config", json={"confirmed": True, "client_id": CLIENT_ID, "client_secret": "secret-123"}).status_code == 200
    Repository(store).create("credentials", "admin", {"provider": "google", "purpose": "drive", "encrypted_refresh_token": encrypt_secret("refresh")})
    replacement = c.post("/api/gpu/auth/google/config", json={"confirmed": True, "client_id": "1234567890-other.apps.googleusercontent.com", "client_secret": "secret-456"})
    assert replacement.status_code == 409


def _account(repo, owner, account_id):
    return repo.create(
        "accounts",
        owner,
        {"account_id": account_id, "alias": account_id, "enabled": True},
        record_id=account_id,
    )


def _config(c, client_id=CLIENT_ID, secret="secret-123", account_id=None):
    body = {"confirmed": True, "client_id": client_id, "client_secret": secret}
    if account_id is not None:
        body["account_id"] = account_id
    return c.post("/api/gpu/auth/google/config", json=body)


def test_scoped_save_does_not_block_connected_sibling_or_touch_its_token(monkeypatch, tmp_path):
    c, store = client(monkeypatch, tmp_path, {"id": "admin", "role": "admin"})
    repo = Repository(store)
    _account(repo, "admin", "account-a")
    _account(repo, "admin", "account-b")
    token = encrypt_secret("refresh-a")
    repo.create("credentials", "admin", {
        "provider": "google", "purpose": "drive", "account_id": "account-a",
        "encrypted_refresh_token": token,
    })

    saved = _config(c, "1234567890-sibling.apps.googleusercontent.com", "sibling-secret", "account-b")

    assert saved.status_code == 200, saved.text
    assert repo.get("credentials", "account-a", "admin") is None
    connected = [row for row in repo.list("credentials", "admin") if row.get("purpose") == "drive"]
    assert len(connected) == 1
    assert connected[0]["account_id"] == "account-a"
    assert decrypt_secret(connected[0]["encrypted_refresh_token"]) == "refresh-a"
    config = repo.get("credentials", oauth_config_routes.config_id("admin", "account-b"), "admin")
    assert config["account_id"] == "account-b"
    assert c.get("/api/gpu/auth/google/config?account_id=account-b").json()["data"]["client_id"] == "1234567890-sibling.apps.googleusercontent.com"


def test_connected_target_rejects_changed_app_but_accepts_same_effective_config(monkeypatch, tmp_path):
    c, store = client(monkeypatch, tmp_path, {"id": "admin", "role": "admin"})
    repo = Repository(store)
    _account(repo, "admin", "account-b")
    assert _config(c, account_id="account-b").status_code == 200
    repo.create("credentials", "admin", {
        "provider": "google", "purpose": "colab", "account_id": "account-b",
        "encrypted_refresh_token": encrypt_secret("refresh-b"),
    })

    same = _config(c, account_id="account-b")
    changed = _config(c, "1234567890-replacement.apps.googleusercontent.com", "different-secret", "account-b")

    assert same.status_code == 200, same.text
    assert changed.status_code == 409


def test_scoped_config_rejects_account_owned_by_another_owner(monkeypatch, tmp_path):
    c, store = client(monkeypatch, tmp_path, {"id": "admin", "role": "admin"})
    repo = Repository(store)
    _account(repo, "other-owner", "foreign-account")

    response = _config(c, account_id="foreign-account")

    assert response.status_code == 404


def test_migrated_default_bound_app_same_save_is_not_blocked(monkeypatch, tmp_path):
    c, store = client(monkeypatch, tmp_path, {"id": "admin", "role": "admin"})
    repo = Repository(store)
    default_id = AccountService.default_account_id("admin")
    _account(repo, "admin", default_id)
    repo.update("accounts", default_id, {"legacy_default": True}, "admin")
    repo.create("credentials", "admin", {
        "provider": "google", "purpose": "oauth_client_config", "account_id": default_id,
        "client_id": CLIENT_ID, "encrypted_client_secret": encrypt_secret("legacy-secret"),
    }, record_id=oauth_config_routes.config_id("admin"))
    repo.create("credentials", "admin", {
        "provider": "google", "purpose": "drive", "account_id": default_id,
        "encrypted_refresh_token": encrypt_secret("refresh-default"),
    })

    response = _config(c, CLIENT_ID, "legacy-secret", default_id)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["client_id"] == CLIENT_ID


def test_shared_change_ignores_dedicated_connected_account(monkeypatch, tmp_path):
    c, store = client(monkeypatch, tmp_path, {"id": "admin", "role": "admin"})
    repo = Repository(store)
    _account(repo, "admin", "account-a")
    assert _config(c, account_id="account-a").status_code == 200
    repo.create("credentials", "admin", {"provider": "google", "purpose": "colab", "account_id": "account-a", "encrypted_refresh_token": encrypt_secret("refresh-a")})
    assert _config(c, "1234567890-shared.apps.googleusercontent.com", "new-shared-secret").status_code == 200
    assert GoogleService(repo, "admin", account_id="account-a")._config()[0] == CLIENT_ID


def test_shared_change_still_protects_migrated_default_token(monkeypatch, tmp_path):
    c, store = client(monkeypatch, tmp_path, {"id": "admin", "role": "admin"})
    repo = Repository(store)
    aid = AccountService.default_account_id("admin")
    _account(repo, "admin", aid)
    repo.update("accounts", aid, {"legacy_default": True}, "admin")
    repo.create("credentials", "admin", {"provider": "google", "purpose": "oauth_client_config", "account_id": aid, "client_id": CLIENT_ID, "encrypted_client_secret": encrypt_secret("old-secret")}, record_id=oauth_config_routes.config_id("admin"))
    repo.create("credentials", "admin", {"provider": "google", "purpose": "colab", "account_id": aid, "encrypted_refresh_token": encrypt_secret("refresh-a")})
    assert _config(c, "1234567890-changed.apps.googleusercontent.com", "new-shared-secret").status_code == 409
