"""Tests for FastAPI endpoints."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import issue_dev_jwt


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def auth_headers(client: TestClient) -> dict:
    res = client.post("/api/v1/auth/dev-login", json={"email": "tester@local", "display_name": "Tester"})
    assert res.status_code == 200
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_health(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_languages(client: TestClient):
    r = client.get("/api/v1/languages")
    assert r.status_code == 200
    langs = r.json()["languages"]
    codes = [l["code"] for l in langs]
    assert "en" in codes
    assert "hi" in codes
    assert "bn" in codes


def test_me_unauthorized(client: TestClient):
    r = client.get("/api/v1/me")
    assert r.status_code in (401, 403)


def test_me_authorized(client: TestClient, auth_headers: dict):
    r = client.get("/api/v1/me", headers=auth_headers)
    assert r.status_code == 200
    assert "id" in r.json()


def test_dev_login_returns_jwt(client: TestClient):
    r = client.post("/api/v1/auth/dev-login", json={"email": "u@local"})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"].count(".") == 2  # JWT has 3 parts
    assert body["user"]["email"] == "u@local"
    assert body["user"]["id"].startswith("user_")


def test_jwt_round_trip():
    token = issue_dev_jwt(user_id="u1", email="u1@local")
    # Decoding should work with our own secret
    from app.auth import decode_dev_jwt
    claims = decode_dev_jwt(token)
    assert claims["sub"] == "u1"
    assert claims["email"] == "u1@local"
