import json
import uuid

import httpx
import pytest
from app.gpu.mcp import MCPError, MCPGPUExecutor


class Google:
    def access_token(self, purpose):
        return "private-test-value"


def test_official_session_resource_and_execute_stdout_not_python_repr():
    calls = []
    execution_id = str(uuid.uuid4())
    job = {
        "id": "1-job-with-digit",
        "execution_id": execution_id,
        "snapshot": {
            "system_smoke": True,
            "manifest": {"executor": "cuda-smoke", "state": "published", "reviewed": True},
            "bound_graph": {},
        },
    }
    runtime = {"runtime_name": "runtimes/studio-1", "owner_id": "owner"}

    def handler(req):
        data = json.loads(req.content)
        name, args = data["params"]["name"], data["params"]["arguments"]
        calls.append((name, args))
        if name == "create_session":
            assert args["sessionId"].startswith("studio-") and len(args["sessionId"]) <= 63
            return httpx.Response(
                200,
                json={
                    "result": {
                        "structuredContent": {"name": "runtimes/studio-1/sessions/" + args["sessionId"]}
                    }
                },
            )
        assert args["session"].startswith("runtimes/studio-1/sessions/")
        assert "print(_json.dumps(_result" in args["code"]
        text = json.dumps({"state": "running", "existing": True}) + "\n"
        return httpx.Response(
            200,
            json={
                "result": {
                    "structuredContent": {
                        "executionId": args["executionId"],
                        "stdout": text,
                        "result": "",
                        "outputTruncated": False,
                    }
                }
            },
        )

    ex = MCPGPUExecutor(
        object(), "owner", google=Google(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert ex.start(runtime, job, execution_id)["execution_id"] == execution_id
    assert ex.poll(runtime, job)["state"] == "running"


def test_execution_error_and_truncated_output_are_fail_closed():
    for result in [
        {"stdout": "", "executionError": {"name": "RuntimeError", "value": "secret-do-not-return"}},
        {"stdout": "broken", "outputTruncated": True},
    ]:
        with pytest.raises(MCPError) as error:
            MCPGPUExecutor._tool_value({"structuredContent": result})
        assert "secret-do-not-return" not in str(error.value)
