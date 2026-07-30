"""Unit tests for FastAPI Web Server endpoints."""

from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from server import app

client = TestClient(app)


def test_root_endpoint_returns_html_or_json():
    response = client.get("/")
    assert response.status_code == 200


def test_results_endpoint_handles_missing_session():
    response = client.get("/api/results/non-existent-session-id")
    assert response.status_code == 404
    assert "Session not found" in response.json()["detail"]


def test_download_excel_endpoint_handles_missing_session():
    response = client.get("/api/download-excel/non-existent-session-id")
    assert response.status_code == 404
    assert "Session not found" in response.json()["detail"]
