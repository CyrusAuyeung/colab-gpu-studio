from __future__ import annotations

import pytest
from app.config import settings
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def test_encryption_key(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
