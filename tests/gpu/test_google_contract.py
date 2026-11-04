from __future__ import annotations

import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from app.db import Store
from app.gpu.google import GoogleConfigurationError, GoogleOAuthError, GoogleService
from app.gpu.repository import Repository
from app.security import decrypt_secret, encrypt_secret


class MemoryRepository:
    def __init__(self):
        self.rows = {"credentials": [], "leases": []}

    def create(self, kind, owner_id, data, **kwargs):
        row = {"id": f"{kind}-{len(self.rows[kind]) + 1}", "owner_id": owner_id, **data}
        self.rows.setdefault(kind, []).append(row)
        return dict(row)

    def get(self, kind, record_id, owner_id=None):
        return next(
            (
                dict(row)
                for row in self.rows.get(kind, [])
                if row["id"] == record_id and (owner_id is None or row["owner_id"] == owner_id)
            ),
            None,
        )

    def list(self, kind, owner_id=None, **kwargs):
        return [
            dict(row) for row in self.rows.get(kind, []) if owner_id is None or row["owner_id"] == owner_id
        ]

    def update(self, kind, record_id, patch, owner_id=None):
        row = self.get(kind, record_id, owner_id)
        if row is None:
            raise KeyError(record_id)
        for current in self.rows[kind]:
            if current["id"] == record_id:
                current.update(patch)
                return dict(current)
        raise KeyError(record_id)

    def delete(self, kind, record_id, owner_id=None):
        old = len(self.rows.get(kind, []))
        self.rows[kind] = [
            r
            for r in self.rows[kind]
            if not (r["id"] == record_id and (owner_id is None or r["owner_id"] == owner_id))
        ]
        return len(self.rows[kind]) != old


def configured(monkeypatch):
    monkeypatch.setenv("GPU_GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GPU_GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GPU_GOOGLE_REDIRECT_URI", "https://studio.example/api/gpu/auth/google/callback")


def test_oauth_start_uses_pkce_and_persists_owner_bound_state(monkeypatch):
    configured(monkeypatch)
    repo = MemoryRepository()
    result = GoogleService(repo, "owner-1").start("drive")
    params = parse_qs(urlparse(result["authorization_url"]).query)
    assert params["client_id"] == ["client-id"]
    assert params["scope"] == ["https://www.googleapis.com/auth/drive.file"]
    assert params["code_challenge_method"] == ["S256"]
    assert repo.rows["credentials"][0]["owner_id"] == "owner-1"
    assert repo.rows["credentials"][0]["purpose"] == "_oauth_state"
    assert repo.rows["credentials"][0]["oauth_purpose"] == "drive"


def test_callback_derives_owner_from_single_use_state_and_encrypts_tokens(monkeypatch):
    configured(monkeypatch)
    repo = MemoryRepository()

    def handler(request):
        form = parse_qs(request.content.decode())
        assert form["code"] == ["code-1"]
        assert "code_verifier" in form
        return httpx.Response(
            200, json={"access_token": "access-secret", "refresh_token": "refresh-secret", "expires_in": 3600}
        )

    service = GoogleService(repo, "owner-1", client=httpx.Client(transport=httpx.MockTransport(handler)))
    started = service.start("colab")
    result = service.callback("code-1", started["state"])
    assert result == {"purpose": "colab", "connected": True, "owner_id": "owner-1"}
    credential = next(row for row in repo.rows["credentials"] if row.get("purpose") == "colab")
    assert decrypt_secret(credential["encrypted_access_token"]) == "access-secret"
    assert decrypt_secret(credential["encrypted_refresh_token"]) == "refresh-secret"
    assert "access-secret" not in str(result)
    with pytest.raises(GoogleOAuthError, match="state"):
        service.callback("code-1", started["state"])


def test_refresh_preserves_existing_refresh_token(monkeypatch):
    configured(monkeypatch)
    from app.security import encrypt_secret

    repo = MemoryRepository()
    repo.create(
        "credentials",
        "owner-1",
        {
            "provider": "google",
            "purpose": "colab",
            "encrypted_access_token": encrypt_secret("old"),
            "encrypted_refresh_token": encrypt_secret("keep"),
            "expires_at": time.time() - 1,
        },
    )

    def handler(request):
        assert parse_qs(request.content.decode())["refresh_token"] == ["keep"]
        return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})

    service = GoogleService(repo, "owner-1", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert service.access_token("colab") == "new"
    row = repo.rows["credentials"][0]
    assert decrypt_secret(row["encrypted_refresh_token"]) == "keep"


def test_unconfigured_google_fails_closed(monkeypatch):
    monkeypatch.delenv("GPU_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GPU_GOOGLE_CLIENT_SECRET", raising=False)
    with pytest.raises(GoogleConfigurationError):
        GoogleService(MemoryRepository(), "owner-1").start("drive")


def test_oauth_state_is_consumed_once_under_concurrent_callbacks(monkeypatch):
    configured(monkeypatch)
    repo = MemoryRepository()
    started = GoogleService(repo, "owner-1").start("drive")
    start_barrier = threading.Barrier(2)
    exchanges = 0
    exchange_lock = threading.Lock()

    def handler(request):
        nonlocal exchanges
        with exchange_lock:
            exchanges += 1
        return httpx.Response(
            200, json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}
        )

    clients = [httpx.Client(transport=httpx.MockTransport(handler)) for _ in range(2)]
    services = [GoogleService(repo, "owner-1", client=client) for client in clients]
    results = []

    def consume(service):
        try:
            start_barrier.wait(timeout=2)
            results.append(service.callback("code", started["state"]))
        except (GoogleOAuthError, KeyError):
            results.append(None)

    threads = [threading.Thread(target=consume, args=(service,)) for service in services]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert sum(result is not None for result in results) == 1
    assert exchanges == 1


def test_insufficient_scope_is_not_reported_as_connected(monkeypatch):
    configured(monkeypatch)
    repo = MemoryRepository()
    repo.create(
        "credentials",
        "owner-1",
        {
            "provider": "google",
            "purpose": "drive",
            "encrypted_access_token": "a",
            "encrypted_refresh_token": "r",
            "expires_at": 9999999999,
            "scope": "openid",
        },
    )
    status = GoogleService(repo, "owner-1").status()
    assert status["drive"]["connected"] is False


def test_real_sqlite_state_is_exchanged_once_concurrently(monkeypatch, tmp_path):
    configured(monkeypatch)
    database = tmp_path / "oauth.sqlite"
    repo = Repository(Store(f"sqlite:///{database}"))
    started = GoogleService(repo, "owner-1").start("drive")
    exchanges = 0
    exchange_lock = threading.Lock()

    def handler(request):
        nonlocal exchanges
        with exchange_lock:
            exchanges += 1
        return httpx.Response(
            200, json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}
        )

    services = [
        GoogleService(
            Repository(Store(f"sqlite:///{database}")),
            "owner-1",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        for _ in range(2)
    ]
    start_barrier = threading.Barrier(2)
    results = []

    def consume(service):
        try:
            start_barrier.wait(timeout=2)
            results.append(service.callback("code", started["state"]))
        except GoogleOAuthError:
            results.append(None)

    threads = [threading.Thread(target=consume, args=(service,)) for service in services]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert sum(result is not None for result in results) == 1
    assert exchanges == 1


def test_disconnect_clears_local_tokens_when_remote_revoke_fails(monkeypatch):
    configured(monkeypatch)
    repo = MemoryRepository()
    repo.create(
        "credentials",
        "owner-1",
        {
            "provider": "google",
            "purpose": "drive",
            "encrypted_access_token": encrypt_secret("access-secret"),
            "encrypted_refresh_token": encrypt_secret("refresh-secret"),
            "expires_at": 9999999999,
        },
    )

    def handler(request):
        assert parse_qs(request.content.decode())["token"] == ["refresh-secret"]
        return httpx.Response(503, text="upstream body must not escape")

    service = GoogleService(repo, "owner-1", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert service.disconnect("drive") is True
    row = repo.rows["credentials"][0]
    assert row["encrypted_access_token"] is None and row["encrypted_refresh_token"] is None
    assert row["disconnect_status"] == "remote_unconfirmed"
    assert service.status()["drive"]["connected"] is False
