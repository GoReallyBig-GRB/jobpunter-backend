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
