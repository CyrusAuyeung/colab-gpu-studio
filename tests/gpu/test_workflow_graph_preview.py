from __future__ import annotations

import copy

import pytest
from app.common import APIError, current_user, envelope
from app.gpu.workbench import WorkbenchService
from app.instance_routes import router
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


class PreviewRepo:
    def __init__(self):
        self.rows = {
            "workflows": [
                {
                    "id": "wf-owned",
                    "owner_id": "owner",
                    "workflow_key": "offline-demo",
                    "workflow_version": 1,
                    "model_id": "demo-model",
                    "state": "published",
                    "description": "Offline preview",
                    "manifest": {
                        "parameters": {
                            "prompt": {"type": "string", "default": "hello"},
                            "steps": {"type": "integer", "default": 4, "min": 1},
                        }
                    },
                    "api_graph": {
                        "1": {"class_type": "Prompt", "inputs": {"text": "hello"}},
                        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
                    },
                    "editable_graph": {
                        "nodes": [{"id": 1, "type": "Prompt", "pos": [20, 40]}],
                        "links": [],
                        "script": "must not cross preview boundary",
                    },
                },
                {
                    "id": "wf-other",
                    "owner_id": "other",
                    "workflow_key": "other-only",
                    "workflow_version": 1,
                    "model_id": "other-model",
                    "state": "published",
                    "manifest": {"parameters": {}},
                    "api_graph": {"1": {"class_type": "Other", "inputs": {}}},
                    "editable_graph": {},
                },
            ],
            "models": [
                {
                    "id": "model-owned",
                    "owner_id": "owner",
                    "model_id": "demo-model",
                    "name": "Demo Model",
                    "enabled": True,
                    "source": "https://secret.example.invalid/model",
                }
            ],
        }

    def list(self, kind, owner_id=None, project_id=None):
        return [
            copy.deepcopy(row)
            for row in self.rows.get(kind, [])
            if owner_id is None or row.get("owner_id") == owner_id
        ]


def test_reviewed_graph_preview_is_offline_and_returns_ui_api_parameters_and_models():
    service = WorkbenchService(repo=PreviewRepo())

    result = service.get_reviewed_graph("owner", {"workflow_key": "offline-demo", "workflow_version": 1})

    assert result["workflow_key"] == "offline-demo"
    assert result["ui_graph"]["nodes"][0]["type"] == "Prompt"
    assert result["api_graph"]["2"]["class_type"] == "SaveImage"
    assert {item["key"] for item in result["parameters"]} == {"prompt", "steps"}
    assert result["models"] == [{"model_id": "demo-model", "name": "Demo Model", "enabled": True}]
    assert "script" not in result["ui_graph"]
    assert "source" not in result["models"][0]


def test_public_and_graph_preview_sanitize_nested_metadata_credentials():
    public = WorkbenchService._safe_public_value({
        "metadata": {
            "label": "safe",
            "api_key": "nested-api-secret",
            "items": [{"private_key": "nested-private-secret", "value": 1}],
        }
    })
    graph = WorkbenchService._safe_preview_graph({
        "nodes": [{
            "id": "node-1",
            "metadata": {
                "client_secret": "nested-client-secret",
                "label": "safe",
            },
        }],
        "metadata": {"private_key": "graph-private-secret", "label": "safe"},
    })

    assert public == {"metadata": {"label": "safe", "items": [{"value": 1}]}}
    assert graph == {
        "nodes": [{"id": "node-1", "metadata": {"label": "safe"}}],
        "metadata": {"label": "safe"},
    }
    assert "nested-api-secret" not in repr(public)
    assert "nested-client-secret" not in repr(graph)


def test_reviewed_graph_action_is_readable_from_unverified_instance():
    service = WorkbenchService(repo=PreviewRepo())

    result = service.execute(
        "owner",
        {"id": "instance-1", "owner_id": "owner", "state": "ready", "provider": "colab"},
        "get_reviewed_graph",
        {"workflow_key": "offline-demo", "workflow_version": 1},
    )

    assert result["reviewed"] is True
    assert result["graph"]["1"]["class_type"] == "Prompt"


def test_reviewed_graph_preview_is_owner_scoped_and_does_not_need_instance():
    service = WorkbenchService(repo=PreviewRepo())

    with pytest.raises(Exception) as exc:
        service.get_reviewed_graph("other", {"workflow_key": "offline-demo", "workflow_version": 1})

    assert getattr(exc.value, "code", None) == "WORKFLOW_UNPUBLISHED"


def test_draft_graph_preview_is_owner_scoped_and_read_only():
    repo = PreviewRepo()
    repo.rows["workflows"].append({
        "id": "wf-draft", "owner_id": "owner", "workflow_key": "draft-demo",
        "workflow_version": 2, "model_id": "demo-model", "state": "draft",
        "manifest": {"parameters": {}},
        "api_graph": {"1": {"class_type": "DraftNode", "inputs": {}}},
    })
    service = WorkbenchService(repo=repo)
    result = service.get_reviewed_graph("owner", {
        "workflow_key": "draft-demo", "workflow_version": 2, "allow_draft": True,
    })
    assert result["published"] is False
    assert result["reviewed"] is False
    assert result["state"] == "draft"
    with pytest.raises(Exception) as exc:
        service.get_reviewed_graph("other", {
            "workflow_key": "draft-demo", "workflow_version": 2, "allow_draft": True,
        })
    assert getattr(exc.value, "code", None) == "WORKFLOW_UNPUBLISHED"


def test_workflow_preview_route_uses_authenticated_owner_and_version():
    class PreviewWorkbench:
        def get_reviewed_graph(self, owner, payload):
            assert owner == "owner"
            assert payload == {"workflow_key": "offline-demo", "workflow_version": 1, "allow_draft": False}
            return {"workflow_key": "offline-demo", "workflow_version": 1, "api_graph": {}}

    app = FastAPI()

    @app.exception_handler(APIError)
    async def api_error(_request: Request, exc: APIError):
        return JSONResponse(envelope(error={"code": exc.code, "message": exc.message}), status_code=exc.status)

    app.state.workbench_service = PreviewWorkbench()
    app.dependency_overrides[current_user] = lambda: {"id": "owner", "role": "user"}
    app.include_router(router)

    with TestClient(app) as client:
        response = client.get("/api/gpu/workflows/offline-demo/preview?version=1")

    assert response.status_code == 200
    assert response.json()["data"]["workflow_key"] == "offline-demo"
