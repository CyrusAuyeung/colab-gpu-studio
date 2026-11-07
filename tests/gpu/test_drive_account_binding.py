from __future__ import annotations

import hashlib
import time

import httpx
import pytest
from app.common import APIError
from app.db import Store
from app.gpu.accounts import AccountService
from app.gpu.drive import DriveStorage
from app.gpu.google import GoogleService
from app.gpu.repository import Repository
from app.security import encrypt_secret


def _add_token(repo, owner_id: str, account_id: str, purpose: str, access: str, refresh: str | None = None):
    return repo.create(
        "credentials",
        owner_id,
        {
            "provider": "google",
            "purpose": purpose,
            "account_id": account_id,
            "encrypted_access_token": encrypt_secret(access),
            "encrypted_refresh_token": encrypt_secret(refresh or f"refresh-{account_id}-{purpose}"),
            "expires_at": time.time() + 3600,
            "scope": (
                "https://www.googleapis.com/auth/colaboratory"
                if purpose == "colab"
                else "https://www.googleapis.com/auth/drive.file"
            ),
        },
        record_id=f"cred:{owner_id}:{account_id}:{purpose}",
    )


def test_account_binds_drive_to_existing_owner_account_and_reports_delegated_status(tmp_path, monkeypatch):
    monkeypatch.setenv("GPU_GOOGLE_CLIENT_ID", "123-abc.apps.googleusercontent.com")
    monkeypatch.setenv("GPU_GOOGLE_CLIENT_SECRET", "secret-123456")
    repo = Repository(Store("sqlite:///" + str(tmp_path / "split.db")))
    service = AccountService(repo, "owner-1")

    old_acc = service.create({"alias": "主账号", "allow_allocation": True}, "create-old")
    _add_token(repo, "owner-1", old_acc["account_id"], "colab", "access-old-colab")
    _add_token(repo, "owner-1", old_acc["account_id"], "drive", "access-old-drive")

    new_acc = service.create(
        {
            "alias": "Colab算力号2",
            "allow_allocation": True,
            "drive_account_id": old_acc["account_id"],
            "budget": {"daily": 200.0, "monthly": 200.0},
        },
        "create-new",
    )
    _add_token(repo, "owner-1", new_acc["account_id"], "colab", "access-new-colab")

    accounts = {item["account_id"]: item for item in service.list()}
    got_new = accounts[new_acc["account_id"]]
    assert got_new["drive_account_id"] == old_acc["account_id"]
    assert got_new["drive_account_alias"] == "主账号"
    assert got_new["authorization"]["colab"]["connected"] is True
    assert got_new["authorization"]["drive"]["connected"] is True
    assert got_new["authorization"]["drive"]["delegated_to"] == old_acc["account_id"]
    assert got_new["status"] == "connected"


def test_drive_storage_routes_bound_compute_account_to_target_drive_account(tmp_path, monkeypatch):
    monkeypatch.setenv("GPU_GOOGLE_CLIENT_ID", "123-abc.apps.googleusercontent.com")
    monkeypatch.setenv("GPU_GOOGLE_CLIENT_SECRET", "secret-123456")
    repo = Repository(Store("sqlite:///" + str(tmp_path / "drive_route.db")))
    service = AccountService(repo, "owner-1")

    old_acc = service.create({"alias": "主账号"}, "old-k")
    _add_token(repo, "owner-1", old_acc["account_id"], "drive", "token-old-drive")

    new_acc = service.create(
        {"alias": "Colab算力号2", "drive_account_id": old_acc["account_id"]},
        "new-k",
    )
    _add_token(repo, "owner-1", new_acc["account_id"], "colab", "token-new-colab")

    # Seed a verified drive_file record under the old account.
    content = b"cached-workflow-output"
    sha = hashlib.sha256(content).hexdigest()
    key_hash = hashlib.sha256(b"outputs/test.bin").hexdigest()
    repo.create(
        "files",
        "owner-1",
        {
            "kind": "drive_file",
            "account_id": old_acc["account_id"],
            "key_hash": key_hash,
            "studio_key_hash": key_hash,
            "sha256": sha,
            "remote_id": "remote-old-file-1",
            "name": "test.bin",
            "size": len(content),
            "mime": "application/octet-stream",
            "state": "verified",
        },
        record_id="file-old-1",
    )

    # A job executing on new_acc must resolve DriveStorage to old_acc's Drive token and file scope!
    storage = DriveStorage(
        {"id": "job-1", "owner_id": "owner-1", "account_id": new_acc["account_id"]},
        repo,
    )
    assert storage.compute_account_id == new_acc["account_id"]
    assert storage.account_id == old_acc["account_id"]
    assert storage.google.access_token("drive") == "token-old-drive"
    matched = storage.put("outputs/test.bin", content, "application/octet-stream")
    assert matched["id"] == "remote-old-file-1"


def test_cross_owner_or_unconnected_drive_account_rejected_and_disconnect_leaves_target_drive_intact(tmp_path, monkeypatch):
    monkeypatch.setenv("GPU_GOOGLE_CLIENT_ID", "123-abc.apps.googleusercontent.com")
    monkeypatch.setenv("GPU_GOOGLE_CLIENT_SECRET", "secret-123456")
    monkeypatch.setenv("STUDIO_OFFLINE_TEST", "1")
    repo = Repository(Store("sqlite:///" + str(tmp_path / "security.db")))
    s1 = AccountService(repo, "owner-1")
    s2 = AccountService(repo, "owner-2")

    other_owner_acc = s2.create({"alias": "他人账号"}, "other-1")
    _add_token(repo, "owner-2", other_owner_acc["account_id"], "drive", "other-drive")

    with pytest.raises(APIError) as exc_info:
        s1.create({"alias": "越权号", "drive_account_id": other_owner_acc["account_id"]}, "bad-1")
    assert exc_info.value.status in {404, 422}

    no_drive_acc = s1.create({"alias": "无Drive号"}, "nodrive-1")
    with pytest.raises(APIError) as exc_info2:
        s1.create({"alias": "绑定无Drive", "drive_account_id": no_drive_acc["account_id"]}, "bad-2")
    assert exc_info2.value.status in {409, 422}

    old_acc = s1.create({"alias": "老Drive号"}, "old-1")
    _add_token(repo, "owner-1", old_acc["account_id"], "drive", "old-drive-token")
    new_acc = s1.create({"alias": "新Colab号", "drive_account_id": old_acc["account_id"]}, "new-1")
    _add_token(repo, "owner-1", new_acc["account_id"], "colab", "new-colab-token")

    # Disconnecting the compute-only account must NEVER revoke or wipe old_acc's Drive token.
    s1.disconnect(new_acc["account_id"], "disc-new")
    assert GoogleService(repo, "owner-1", account_id=old_acc["account_id"]).status()["drive"]["connected"] is True


def test_credential_bound_client_config_used_for_cli_colab_refresh_without_env_config(tmp_path, monkeypatch):
    monkeypatch.delenv("GPU_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GPU_GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GPU_GOOGLE_SECRET", raising=False)
    repo = Repository(Store("sqlite:///" + str(tmp_path / "cli_cred.db")))
    s1 = AccountService(repo, "owner-1")
    cli_acc = s1.create({"alias": "CLI算力号"}, "cli-1")
    repo.create(
        "credentials",
        "owner-1",
        {
            "provider": "google",
            "purpose": "colab",
            "account_id": cli_acc["account_id"],
            "client_id": "764086051850-cli.apps.googleusercontent.com",
            "encrypted_client_secret": encrypt_secret("cli-secret-xyz"),
            "encrypted_access_token": encrypt_secret("expired-access"),
            "encrypted_refresh_token": encrypt_secret("cli-refresh-token"),
            "expires_at": time.time() - 10,
            "scope": "openid https://www.googleapis.com/auth/userinfo.profile https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/colaboratory",
        },
        record_id="cred:cli",
    )
    captured = {}

    def handler(request):
        from urllib.parse import parse_qs

        captured.update(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "fresh-cli-access", "expires_in": 3600})

    google = GoogleService(
        repo,
        "owner-1",
        account_id=cli_acc["account_id"],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert google.status()["colab"]["connected"] is True
    assert google.status()["colab"]["configured"] is True
    assert google.access_token("colab") == "fresh-cli-access"
    assert captured["client_id"] == ["764086051850-cli.apps.googleusercontent.com"]
    assert captured["client_secret"] == ["cli-secret-xyz"]
    assert captured["refresh_token"] == ["cli-refresh-token"]


def test_explicit_account_admission_checks_that_account_and_freezes_drive_account_id(tmp_path, monkeypatch):
    from app.admission import admit_job
    from app.gpu.registry import Registry

    monkeypatch.setenv("GPU_REAL_ENABLED", "1")
    monkeypatch.setenv("GPU_UNIT_RATE", "0.02")
    monkeypatch.setenv("GPU_DAILY_UNITS", "200")
    monkeypatch.setenv("GPU_MONTHLY_UNITS", "200")
    monkeypatch.setenv("GPU_MAX_JOB_UNITS", "20")
    monkeypatch.setenv("GPU_GOOGLE_CLIENT_ID", "123-abc.apps.googleusercontent.com")
    monkeypatch.setenv("GPU_GOOGLE_CLIENT_SECRET", "secret-123456")

    st = Store("sqlite:///" + str(tmp_path / "admit.db"))
    repo = Repository(st)
    reg = Registry(repo)
    reg.bootstrap("owner-1")
    st.create("projects", "owner-1", {"title": "P1"}, record_id="p1")

    # Enable a real model and workflow for admission test
    models = repo.list("models", "owner-1")
    real_model = next(m for m in models if not m.get("is_mock"))
    repo.update("models", real_model["id"], {"enabled": True}, "owner-1")
    workflows = [w for w in repo.list("workflows", "owner-1") if w["model_id"] == real_model["model_id"]]
    wf = workflows[0]
    repo.update(
        "workflows",
        wf["id"],
        {"state": "published", "review_approved": True, "smoke_test_kind": "real"},
        "owner-1",
    )

    acc_service = AccountService(repo, "owner-1")
    old_acc = acc_service.create(
        {"alias": "老账号", "allow_allocation": True, "budget": {"daily": 200.0}},
        "old-a",
    )
    # Deliberately do NOT give old_acc a colab token; only give it a drive token!
    _add_token(repo, "owner-1", old_acc["account_id"], "drive", "old-drive-access")

    new_acc = acc_service.create(
        {
            "alias": "新算力号",
            "allow_allocation": True,
            "drive_account_id": old_acc["account_id"],
            "budget": {"daily": 200.0},
        },
        "new-a",
    )
    _add_token(repo, "owner-1", new_acc["account_id"], "colab", "new-colab-access")

    body = {
        "project_id": "p1",
        "model_id": real_model["model_id"],
        "workflow_key": wf["workflow_key"],
        "workflow_version": wf["workflow_version"],
        "params": {},
        "asset_ids": [],
        "execution_policy": {
            "provider": "colab",
            "lifecycle": "save",
            "max_runtime_minutes": 10,
            "allocation": "auto",
            "account_id": new_acc["account_id"],
        },
    }
    job = admit_job(st, {"id": "owner-1", "role": "admin"}, body, "job-admit-1")
    assert job["requested_account_id"] == new_acc["account_id"]
    assert job["drive_account_id"] == old_acc["account_id"]


def test_prep_job_created_by_scheduler_inherits_account_and_drive_account_id(tmp_path):
    from app.gpu.prep import create_prep_job, prep_idempotency_key

    repo = Repository(Store("sqlite:///" + str(tmp_path / "prep_acc.db")))
    prep = create_prep_job(
        repo,
        "owner-1",
        "p1",
        {"manifest": {"weight_files": [{"repo_id": "a/b", "filename": "c.bin", "revision": "1", "sha256": "0" * 64}]}},
        {
            "workflow_sha256": "f" * 64,
            "model_id": "m1",
            "workflow_key": "wf1",
            "workflow_version": "1.0.0",
            "account_id": "acc-compute-2",
            "drive_account_id": "acc-drive-1",
        },
        prep_idempotency_key("k1", "acc-drive-1"),
        for_job_id="job-parent-1",
    )
    assert prep["account_id"] == "acc-compute-2"
    assert prep["requested_account_id"] == "acc-compute-2"
    assert prep["drive_account_id"] == "acc-drive-1"
    assert prep["execution_policy"]["account_id"] == "acc-compute-2"


def test_cli_tunnel_colab_provider_lists_specs_assigns_inspects_and_unassigns(tmp_path):
    from app.gpu.colab import GoogleColabProvider
    from app.security import decrypt_secret

    repo = Repository(Store("sqlite:///" + str(tmp_path / "tunnel.db")))
    repo.create(
        "credentials",
        "owner-1",
        {
            "provider": "google",
            "purpose": "colab",
            "account_id": "acc-cli",
            "client_id": "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com",
            "encrypted_client_secret": encrypt_secret("cli-secret"),
            "encrypted_access_token": encrypt_secret("cli-access-token"),
            "encrypted_refresh_token": encrypt_secret("cli-refresh-token"),
            "expires_at": time.time() + 3600,
            "scope": "openid https://www.googleapis.com/auth/userinfo.profile https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/colaboratory",
        },
        record_id="cred:cli:1",
    )
    lease = repo.create(
        "leases",
        "owner-1",
        {
            "provider": "colab",
            "account_id": "acc-cli",
            "runtime_id": "studio-testruntime1",
            "request_id": "11111111-1111-4111-8111-111111111111",
            "owner_job_id": "job-1",
            "state": "creating",
        },
        record_id="lease-1",
    )
    state = {"assigned": False}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "colab.research.google.com"
        assert request.headers.get("X-Colab-Client-Agent") == "colab-cli"
        if request.url.path == "/tun/m/ccu-info":
            return httpx.Response(
                200,
                text=")]}'\n"
                + '{"currentBalance": 2500.0, "consumptionRateHourly": 0.0, "assignmentsCount": 0, "eligibleGpus": ["T4", "L4", "A100"], "eligibleTpus": ["V5E1"]}',
            )
        if request.url.path == "/tun/m/assignments":
            items = (
                [
                    {
                        "accelerator": "L4",
                        "endpoint": "m-s-foo123",
                        "variant": 1,
                        "machineShape": 0,
                        "runtimeProxyInfo": {
                            "token": "proxy-secret-token-xyz",
                            "tokenExpiresInSeconds": 3600,
                            "url": "https://8080-m-s-foo123.us-central1.prod.colab.dev",
                        },
                    }
                ]
                if state["assigned"]
                else []
            )
            return httpx.Response(200, text=")]}'\n" + __import__("json").dumps({"assignments": items}))
        if request.url.path == "/tun/m/assign" and request.method == "GET":
            assert request.url.params.get("variant") == "GPU"
            assert request.url.params.get("accelerator") == "L4"
            return httpx.Response(
                200,
                text=")]}'\n" + '{"acc": "L4", "nbh": "x", "token": "xsrf-assign-1", "variant": "GPU"}',
            )
        if request.url.path == "/tun/m/assign" and request.method == "POST":
            assert request.headers.get("X-Goog-Colab-Token") == "xsrf-assign-1"
            state["assigned"] = True
            return httpx.Response(
                200,
                text=")]}'\n"
                + '{"accelerator": "L4", "endpoint": "m-s-foo123", "variant": 1, "runtimeProxyInfo": {"token": "proxy-secret-token-xyz", "tokenExpiresInSeconds": 3600, "url": "https://8080-m-s-foo123.us-central1.prod.colab.dev"}}',
            )
        if request.url.path == "/tun/m/unassign/m-s-foo123" and request.method == "GET":
            return httpx.Response(200, text=")]}'\n" + '{"token": "xsrf-unassign-1"}')
        if request.url.path == "/tun/m/unassign/m-s-foo123" and request.method == "POST":
            assert request.headers.get("X-Goog-Colab-Token") == "xsrf-unassign-1"
            state["assigned"] = False
            return httpx.Response(200, text=")]}'\n{}")
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    provider = GoogleColabProvider(
        repo,
        "owner-1",
        account_id="acc-cli",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    specs = provider.list_specs()
    keys = [s["key"] for s in specs if s.get("eligible")]
    assert {"variant": "VARIANT_CPU", "accelerator": "NONE", "shape": "SHAPE_STANDARD"} in keys
    assert {"variant": "VARIANT_GPU", "accelerator": "L4", "shape": "SHAPE_STANDARD"} in keys
    assert {"variant": "VARIANT_GPU", "accelerator": "T4", "shape": "SHAPE_STANDARD"} in keys

    handle = provider.create(
        {"variant": "VARIANT_GPU", "accelerator": "L4", "shape": "SHAPE_STANDARD"},
        lease["request_id"],
        "job-1",
        lease["runtime_id"],
    )
    assert handle["runtime_name"] == "runtimes/studio-testruntime1"
    assert handle["ready"] is True
    assert "proxy-secret-token-xyz" not in str(handle)

    saved_lease = repo.get("leases", lease["id"], "owner-1")
    assert saved_lease["tunnel_endpoint"] == "m-s-foo123"
    assert decrypt_secret(saved_lease["encrypted_proxy_token"]) == "proxy-secret-token-xyz"

    inspected = provider.inspect(handle)
    assert inspected["ready"] is True
    assert inspected["deleted"] is False

    conn = provider.connection_info(handle)
    assert conn["url"] == "https://8080-m-s-foo123.us-central1.prod.colab.dev"
    assert conn["token"] == "proxy-secret-token-xyz"

    deleted = provider.delete(handle)
    assert deleted is True
    assert provider.inspect(handle)["deleted"] is True


def test_cli_tunnel_mcp_executor_dispatches_to_kernel_runner_without_calling_mcp_http(tmp_path, monkeypatch):
    from app.gpu.mcp import MCPGPUExecutor
    from app.security import encrypt_secret

    repo = Repository(Store("sqlite:///" + str(tmp_path / "mcp_tun.db")))
    repo.create(
        "credentials",
        "owner-1",
        {
            "provider": "google",
            "purpose": "colab",
            "account_id": "acc-cli",
            "client_id": "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com",
            "encrypted_client_secret": encrypt_secret("cli-secret"),
            "encrypted_access_token": encrypt_secret("cli-access-token"),
            "encrypted_refresh_token": encrypt_secret("cli-refresh-token"),
            "expires_at": time.time() + 3600,
        },
        record_id="cred:cli:mcp",
    )
    repo.create(
        "leases",
        "owner-1",
        {
            "provider": "colab",
            "account_id": "acc-cli",
            "runtime_id": "studio-testruntime2",
            "request_id": "22222222-2222-4222-8222-222222222222",
            "owner_job_id": "job-2",
            "state": "active",
            "tunnel_endpoint": "m-s-bar456",
            "proxy_url": "https://8080-m-s-bar456.us-central1.prod.colab.dev",
            "encrypted_proxy_token": encrypt_secret("proxy-token-bar"),
        },
        record_id="lease-2",
    )

    executed_codes = []

    def fake_kernel_exec(self, lease, code):
        executed_codes.append((lease["tunnel_endpoint"], code))
        execution_id = "33333333-3333-4333-8333-333333333333"
        if "runtime_worker.py" in code:
            return {"stdout": __import__("json").dumps({"execution_id": execution_id, "started": True, "existing": False})}
        return {
            "stdout": __import__("json").dumps(
                {
                    "execution_id": execution_id,
                    "state": "completed",
                    "outputs": [
                        {
                            "filename": "out.png",
                            "mime": "image/png",
                            "content": __import__("base64").b64encode(b"png-bytes").decode("ascii"),
                            "content_encoding": "base64",
                        }
                    ],
                    "timings": {"sampling": 1.2},
                }
            )
        }

    monkeypatch.setattr(MCPGPUExecutor, "_execute_tunnel_code", fake_kernel_exec)

    executor = MCPGPUExecutor(
        repo,
        "owner-1",
        account_id="acc-cli",
        client=httpx.Client(transport=httpx.MockTransport(lambda req: (_ for _ in ()).throw(AssertionError("MCP HTTP should not be called for tunnel lease")))),
    )
    handle = {
        "runtime_name": "runtimes/studio-testruntime2",
        "runtime_id": "studio-testruntime2",
        "request_id": "22222222-2222-4222-8222-222222222222",
        "job_id": "job-2",
        "owner_job_id": "job-2",
        "owner_id": "owner-1",
    }
    job = {
        "id": "job-2",
        "owner_id": "owner-1",
        "account_id": "acc-cli",
        "execution_id": "33333333-3333-4333-8333-333333333333",
        "snapshot": {"manifest": {"mock": False}},
    }
    started = executor.start(handle, job, job["execution_id"])
    assert started["execution_id"] == job["execution_id"]
    polled = executor.poll(handle, job)
    assert polled["state"] == "completed"
    assert polled["outputs"][0]["content"] == b"png-bytes"
    assert len(executed_codes) == 2
