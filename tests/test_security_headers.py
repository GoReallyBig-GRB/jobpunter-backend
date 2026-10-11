from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def assert_baseline_security_headers(response):
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


def test_security_headers_are_added_to_application_responses():
    response = client.get("/__security_headers_test_route_that_does_not_exist__")

    assert response.status_code == 404
    assert_baseline_security_headers(response)


def test_security_headers_cover_early_request_body_limit_rejections():
    response = client.post("/api/v1/ai/generate", content=b"x" * 100_001)

    assert response.status_code == 413
    assert_baseline_security_headers(response)
