import pytest

from app.api.middleware import parse_cors_origins


def test_cors_origin_parser_accepts_explicit_http_and_https_origins():
    assert parse_cors_origins("https://app.example.com, http://localhost:3000") == [
        "https://app.example.com",
        "http://localhost:3000",
    ]


def test_cors_origin_parser_normalizes_and_deduplicates_origins():
    assert parse_cors_origins("HTTPS://APP.EXAMPLE.COM/, https://app.example.com") == [
        "https://app.example.com",
    ]


def test_cors_origin_parser_returns_empty_list_when_unconfigured():
    assert parse_cors_origins("") == []
    assert parse_cors_origins(" , ") == []


@pytest.mark.parametrize(
    "value",
    [
        "*",
        "https://*.example.com",
        "https://example.com/path",
        "https://user:password@example.com",
        "ftp://example.com",
        "https://example.com?query=1",
        "https://example.com#fragment",
        "https://example.com:invalid",
    ],
)
def test_cors_origin_parser_rejects_wildcards_and_non_origins(value):
    with pytest.raises(ValueError):
        parse_cors_origins(value)


def test_application_cors_preflight_allows_only_configured_origin(monkeypatch):
    import importlib
    import os

    from fastapi.testclient import TestClient

    import app.main as main_module

    previous = os.environ.get("JOBPUNTER_CORS_ALLOWED_ORIGINS")
    try:
        monkeypatch.setenv("JOBPUNTER_CORS_ALLOWED_ORIGINS", "https://app.example.com")
        importlib.reload(main_module)
        client = TestClient(main_module.app)
        headers = {
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "GET",
        }
        allowed = client.options("/api/v1/cors-preflight-test", headers=headers)
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == "https://app.example.com"
        assert allowed.headers.get("access-control-allow-credentials") is None

        denied = client.options(
            "/api/v1/cors-preflight-test",
            headers={**headers, "Origin": "https://unapproved.example.com"},
        )
        assert denied.status_code == 400
        assert "access-control-allow-origin" not in denied.headers
    finally:
        if previous is None:
            os.environ.pop("JOBPUNTER_CORS_ALLOWED_ORIGINS", None)
        else:
            os.environ["JOBPUNTER_CORS_ALLOWED_ORIGINS"] = previous
        importlib.reload(main_module)
