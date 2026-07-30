"""Real authentication boundary tests."""
import pytest
from fastapi.testclient import TestClient

from src.api.main import app


pytestmark=pytest.mark.real_auth


def test_protected_chat_rejects_missing_token():
    response=TestClient(app,raise_server_exceptions=False).post(
        "/api/chat",json={"message":"What is RUL?"},
    )
    assert response.status_code==401


def test_invalid_bearer_token_is_rejected():
    response=TestClient(app,raise_server_exceptions=False).post(
        "/api/chat",
        headers={"Authorization":"Bearer invalid-token"},
        json={"message":"What is RUL?"},
    )
    assert response.status_code==401
