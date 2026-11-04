from __future__ import annotations

import base64
import hashlib
import json
import uuid

import httpx
import pytest
from app.gpu.mcp import MCPError, MCPGPUExecutor

TOKEN = "access-token"
RUNTIME = {"runtime_name": "runtimes/runtime-1", "runtime_id": "runtime-1", "owner_id": "owner-1"}


def _text_result(value: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(value)}]}


def _job() -> dict:
    return {
        "id": "job-1",
        "owner_id": "owner-1",
        "execution_id": "11111111-1111-4111-8111-111111111111",
        "max_runtime_minutes": 10,
        "snapshot": {
            "manifest": {"state": "published", "reviewed": True},
            "bound_graph": {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {}}},
        },
    }


class Google:
    def access_token(self, purpose: str) -> str:
        assert purpose == "colab"
        return TOKEN


def test_start_calls_official_mcp_tools_and_embeds_only_trusted_bootstrap():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        assert request.url == "https://colaboratory.googleapis.com/mcp"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["accept"] == "application/json,text/event-stream"
        body = calls[-1]
        if body["params"]["name"] == "create_session":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": _text_result(
                        {
                            "session": (
                                "runtimes/runtime-1/sessions/studio-"
                                + hashlib.sha256(b"job-1").hexdigest()[:32]
                            )
                        }
                    ),
                },
            )
        arguments = body["params"]["arguments"]
        assert body["params"]["name"] == "execute_code"
        assert arguments["session"] == (
            "runtimes/runtime-1/sessions/studio-" + hashlib.sha256(b"job-1").hexdigest()[:32]
        )
        assert arguments["executionId"] == _job()["execution_id"]
        assert "Popen" in arguments["code"] and "shell=False" in arguments["code"]
        assert "/content/ai-studio" in arguments["code"]
        assert "user_code" not in arguments["code"]
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": _text_result({"started": True})}
        )

    executor = MCPGPUExecutor(
        object(), "owner-1", google=Google(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = executor.start(RUNTIME, _job(), _job()["execution_id"])
    assert result["execution_id"] == _job()["execution_id"]
    assert [call["params"]["name"] for call in calls] == ["create_session", "execute_code"]
    assert calls[0]["params"]["arguments"] == {
        "parent": "runtimes/runtime-1",
        "sessionId": "studio-" + hashlib.sha256(b"job-1").hexdigest()[:32],
    }


def test_poll_uses_new_execution_id_and_recovers_sse_status_with_bounded_output():
    seen = []
    output = base64.b64encode(b"png").decode()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        name = body["params"]["name"]
        if name == "create_session":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "session": (
                            "runtimes/runtime-1/sessions/studio-" + hashlib.sha256(b"job-1").hexdigest()[:32]
                        )
                    },
                },
            )
        assert name == "execute_code"
        code = body["params"]["arguments"]["code"]
        assert "status.json" in code and "base64" in code and "100 * 1024 * 1024" in code
        execution_id = body["params"]["arguments"]["executionId"]
        assert uuid.UUID(execution_id).version == 4
        payload = {
            "state": "completed",
            "outputs": [{"filename": "x.png", "mime": "image/png", "content": output}],
        }
        raw = f"data: {json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': {'content': [{'type': 'text', 'text': json.dumps(payload)}]}})}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=raw)

    executor = MCPGPUExecutor(
        object(), "owner-1", google=Google(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = executor.poll(RUNTIME, _job())
    assert result["state"] == "completed"
    assert result["outputs"][0]["content"] == b"png"
    assert seen[0]["params"]["name"] == "create_session"
    assert seen[1]["params"]["name"] == "execute_code"
    assert seen[1]["params"]["arguments"]["executionId"] != _job()["execution_id"]


def test_invalid_execution_id_and_untrusted_runtime_fail_closed():
    executor = MCPGPUExecutor(
        object(),
        "owner-1",
        google=Google(),
        client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))),
    )
    with pytest.raises(MCPError):
        executor.start(RUNTIME, _job(), "not-a-uuid")
    with pytest.raises(MCPError):
        executor.start({**RUNTIME, "owner_id": "other"}, _job(), _job()["execution_id"])


def test_repeated_start_script_is_marker_idempotent_not_restart():
    scripts = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["params"]["name"] == "create_session":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "session": (
                            "runtimes/runtime-1/sessions/studio-" + hashlib.sha256(b"job-1").hexdigest()[:32]
                        )
                    }
                },
            )
        scripts.append(body["params"]["arguments"]["code"])
        return httpx.Response(
            200, json={"result": {"content": [{"type": "text", "text": json.dumps({"existing": True})}]}}
        )

    executor = MCPGPUExecutor(
        object(), "owner-1", google=Google(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    first = executor.start(RUNTIME, _job(), _job()["execution_id"])
    second = executor.start(RUNTIME, _job(), _job()["execution_id"])
    assert first["execution_id"] == second["execution_id"]
    assert all("O_EXCL" in script and "Popen" in script for script in scripts)
    assert all("if _marker.exists()" in script for script in scripts)
