from fastapi.testclient import TestClient

from app.main import app


def test_body_limit_rejects_oversized_actual_body_even_when_content_length_lies():
    response = TestClient(app).post(
        "/api/v1/ai/generate",
        content=b" " * 100_001,
        headers={"Content-Type": "application/json", "Content-Length": "2"},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "REQUEST_TOO_LARGE"


def test_body_limit_rejects_oversized_declared_body_before_reading():
    response = TestClient(app).post(
        "/api/v1/ai/generate",
        content=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": "100001"},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "REQUEST_TOO_LARGE"
