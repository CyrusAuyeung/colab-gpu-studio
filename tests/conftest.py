"""Credential-free offline gate: actual transports and sockets fail closed."""
import json
import os
import socket
import tempfile
import traceback
from pathlib import Path

import httpx
import pytest

_ROOT = tempfile.TemporaryDirectory(prefix="studio-offline-")
os.environ.update(GPU_REAL_ENABLED="0", GPU_STORAGE="local", DATABASE_URL="sqlite:///:memory:",
                  DATA_DIR=_ROOT.name, ENVIRONMENT="offline-test", GOOGLE_APPLICATION_CREDENTIALS="")
COUNTS = dict(real_runtime_create_count=0, real_runtime_delete_count=0,
              real_mcp_call_count=0, real_colab_oauth_call_count=0)
ATTEMPTS = []


def deny(*args, **kwargs):
    request = next((arg for arg in args if isinstance(arg, httpx.Request)), None)
    if request is not None:
        url = str(request.url)
        if "colaboratory.googleapis.com" in url and "/mcp" in url:
            COUNTS["real_mcp_call_count"] += 1
        elif "colaboratory.googleapis.com" in url and request.method == "DELETE":
            COUNTS["real_runtime_delete_count"] += 1
        elif "colaboratory.googleapis.com" in url and request.method == "POST":
            COUNTS["real_runtime_create_count"] += 1
        elif any(host in url for host in ("oauth2.googleapis.com", "accounts.google.com")):
            COUNTS["real_colab_oauth_call_count"] += 1
    ATTEMPTS.append({"stack": traceback.format_stack(limit=12)})
    raise AssertionError("Offline test forbids real outbound network: " + "".join(ATTEMPTS[-1]["stack"]))


@pytest.fixture(autouse=True)
def offline_gate(monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setenv("GPU_REAL_ENABLED", "0")
    monkeypatch.setenv("GPU_STORAGE", "local")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny)
    start = len(ATTEMPTS)
    yield
    assert len(ATTEMPTS) == start, "Real network was attempted (even if caught by application)"
    assert all(count == 0 for count in COUNTS.values())


def pytest_sessionfinish(session, exitstatus):
    target = Path("docs/offline-evidence/network-audit.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({**COUNTS, "blocked_attempts": ATTEMPTS,
                                  "test_exit_status": int(exitstatus)}, indent=2))
