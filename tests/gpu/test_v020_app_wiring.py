import pytest
from app.common import current_user
from app.db import Store
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    app.state.store = Store('sqlite:///' + str(tmp_path / 'wiring.db'))
    app.dependency_overrides[current_user] = lambda: {'id': 'owner', 'role': 'admin'}
    with TestClient(app) as session:
        yield session
    app.dependency_overrides.clear()


def test_instance_routes_are_mounted_and_have_real_repository_service(client):
    response = client.get('/api/gpu/instances')
    assert response.status_code == 200, response.text
    assert response.json()['data'] == []


def test_account_routes_mounted(client):
    response = client.get('/api/gpu/accounts')
    assert response.status_code == 200, response.text
    assert isinstance(response.json()['data'], list)


def test_instance_routes_require_authentication(client):
    app.dependency_overrides.pop(current_user, None)
    response = client.get('/api/gpu/instances')
    assert response.status_code == 401
