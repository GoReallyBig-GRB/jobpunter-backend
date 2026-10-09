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


def test_body_limit_counts_chunked_body_when_content_length_is_absent():
    import asyncio

    from app.api.middleware import RequestBodyLimitMiddleware

    messages = iter([
        {"type": "http.request", "body": b"123456", "more_body": True},
        {"type": "http.request", "body": b"78901", "more_body": False},
    ])
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    async def downstream(_scope, _receive, _send):
        raise AssertionError("oversized body must be rejected before the route")

    middleware = RequestBodyLimitMiddleware(downstream, max_bytes=10)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/ai/generate",
        "headers": [],
    }
    asyncio.run(middleware(scope, receive, send))

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    assert b"REQUEST_TOO_LARGE" in sent[1]["body"]
