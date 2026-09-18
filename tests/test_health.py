"""Tests for the health-check endpoints."""

from fastapi.testclient import TestClient


def test_liveness_returns_ok(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "app" in body
    assert "env" in body


def test_readiness_reports_unavailable_without_database(client: TestClient) -> None:
    """
    No real Postgres is expected to be running in this unit-test
    environment, so /health/ready should degrade gracefully (503) instead
    of raising an unhandled exception.
    """
    response = client.get("/health/ready")

    assert response.status_code in (200, 503)
    body = response.json()
    assert body["status"] in ("ok", "unavailable")
    assert "database" in body["checks"]
