from pathlib import Path

from app.main import app

ROOT = Path(__file__).resolve().parents[1]


def route_methods():
    return {
        (path, method.upper())
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }


def test_standalone_api_has_no_atelier_routes():
    paths = {path.lower() for path in app.openapi()["paths"]}
    assert not any("atelier" in path for path in paths)


def test_standalone_env_templates_have_no_atelier_variables():
    for relative in ("scripts/init_env.py", ".env.example"):
        assert "ATELIER_" not in (ROOT / relative).read_text()


def test_standalone_frontend_has_no_atelier_surface():
    for relative in ("web/src/App.tsx", "web/src/lib/gpu-api.ts"):
        assert "atelier" not in (ROOT / relative).read_text().lower()


def test_core_project_and_job_routes_remain():
    methods = route_methods()
    assert ("/api/gpu/projects", "GET") in methods
    assert ("/api/gpu/projects", "POST") in methods
    assert ("/api/gpu/projects/{project_id}", "PATCH") in methods
    assert ("/api/gpu/jobs", "POST") in methods
    assert ("/api/gpu/jobs", "GET") in methods
