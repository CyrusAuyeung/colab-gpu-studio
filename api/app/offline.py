"""Explicit offline application safety boundary, separate from production settings."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import threading
import traceback
from pathlib import Path

COUNTS = {
    name: 0
    for name in (
        "real_runtime_create_count",
        "real_runtime_delete_count",
        "real_mcp_call_count",
        "real_colab_oauth_call_count",
    )
}
ATTEMPTS: list[dict] = []
_LOCK = threading.RLock()
_INSTALLED = False


def enabled():
    return os.getenv("STUDIO_OFFLINE_TEST") == "1"


def audit():
    target = os.getenv("STUDIO_OFFLINE_AUDIT")
    if target:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            path.write_text(json.dumps({**COUNTS, "blocked_attempts": ATTEMPTS}, indent=2))


def block(destination, request=None):
    with _LOCK:
        if request is not None:
            host = request.url.host
            if host == "colaboratory.googleapis.com":
                if "/mcp" in request.url.path:
                    COUNTS["real_mcp_call_count"] += 1
                elif request.method == "DELETE":
                    COUNTS["real_runtime_delete_count"] += 1
                elif request.method == "POST":
                    COUNTS["real_runtime_create_count"] += 1
            elif host in {"oauth2.googleapis.com", "accounts.google.com"}:
                COUNTS["real_colab_oauth_call_count"] += 1
        ATTEMPTS.append({"destination": str(destination), "stack": traceback.format_stack(limit=16)})
        audit()
    raise RuntimeError("OFFLINE_OUTBOUND_DENIED: external network is forbidden")


def install():
    global _INSTALLED
    if not enabled() or _INSTALLED:
        return
    import httpx

    _INSTALLED = True
    os.environ.update(
        GPU_REAL_ENABLED="0",
        GPU_STORAGE="local",
        STUDIO_ATELIER_ENABLED="0",
        GPU_PREP="off",
        GPU_MODEL_CACHE="off",
    )
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_http = httpx.HTTPTransport.handle_request
    original_async = httpx.AsyncHTTPTransport.handle_async_request

    def local(address):
        if isinstance(address, str):  # Unix sockets cannot reach Google.
            return True
        try:
            return ipaddress.ip_address(address[0]).is_loopback
        except (ValueError, TypeError):
            return False

    def connect(sock, address):
        if not local(address):
            block(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if not local(address):
            block(address)
        return original_connect_ex(sock, address)

    def http(transport, request):
        if request.url.host not in {"127.0.0.1", "::1", "localhost"}:
            block(request.url.host, request)
        return original_http(transport, request)

    async def async_http(transport, request):
        if request.url.host not in {"127.0.0.1", "::1", "localhost"}:
            block(request.url.host, request)
        return await original_async(transport, request)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    httpx.HTTPTransport.handle_request = http
    httpx.AsyncHTTPTransport.handle_async_request = async_http
    audit()
