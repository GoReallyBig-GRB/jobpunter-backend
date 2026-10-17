from app.main import app


def _operation(spec, path, method):
    operation = spec["paths"][path][method.lower()]
    assert operation is not None
    return operation


def _request_schema(spec, operation):
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[1]
        return spec["components"]["schemas"][name]
    return schema


def test_critical_candidate_routes_and_create_status_codes_are_in_openapi():
    spec = app.openapi()
    expected = {
        ("/api/v1/candidate/profile", "get", "200"),
        ("/api/v1/candidate/profile", "patch", "200"),
        ("/api/v1/candidate/preferences", "get", "200"),
        ("/api/v1/candidate/preferences", "put", "200"),
        ("/api/v1/candidate/career-pathways", "get", "200"),
        ("/api/v1/candidate/career-pathways", "post", "201"),
        ("/api/v1/candidate/document-imports", "post", "201"),
        ("/api/v1/candidate/discovery/missions", "post", "201"),
        ("/api/v1/candidate/jobs/pool", "get", "200"),
        ("/api/v1/candidate/opportunities", "get", "200"),
        ("/api/v1/candidate/evaluations", "post", "201"),
        ("/api/v1/candidate/punts", "post", "201"),
        ("/api/v1/candidate/applications", "post", "201"),
        ("/api/v1/candidate/applications/{application_id}/transition", "post", "200"),
        ("/api/v1/candidate/applications/{application_id}/packages", "post", "201"),
        ("/api/v1/candidate/notifications", "get", "200"),
        ("/api/v1/candidate/notifications/email-preferences", "put", "200"),
        ("/api/v1/candidate/support/tickets", "post", "201"),
    }

    for path, method, status in expected:
        operation = _operation(spec, path, method)
        assert status in operation["responses"], f"{method.upper()} {path} missing {status}"


def test_candidate_collection_pagination_defaults_and_bounds_are_documented():
    spec = app.openapi()
    expected = {
        "/api/v1/candidate/jobs/pool": {"limit": ("20", "1", "50"), "offset": ("0", "0", None)},
        "/api/v1/candidate/opportunities": {"limit": ("20", "1", "50"), "offset": ("0", "0", None)},
        "/api/v1/candidate/notifications": {"limit": ("20", "1", "100"), "offset": ("0", "0", None)},
        "/api/v1/candidate/support/tickets": {"limit": ("20", "1", "100"), "offset": ("0", "0", None)},
    }

    for path, parameters in expected.items():
        operation = _operation(spec, path, "get")
        actual = {item["name"]: item["schema"] for item in operation.get("parameters", [])}
        for name, (default, minimum, maximum) in parameters.items():
            assert name in actual, f"{path} is missing {name}"
            assert str(actual[name].get("default")) == default
            assert str(actual[name].get("minimum")) == minimum
            if maximum is not None:
                assert str(actual[name].get("maximum")) == maximum


def test_idempotency_and_lifecycle_request_fields_are_in_openapi_contract():
    spec = app.openapi()

    ticket_create = _request_schema(
        spec, _operation(spec, "/api/v1/candidate/support/tickets", "post")
    )
    assert {"idempotency_key", "title", "issue_details"} <= set(ticket_create["required"])

    discovery_create = _request_schema(
        spec, _operation(spec, "/api/v1/candidate/discovery/missions", "post")
    )
    assert "idempotency_key" in discovery_create["required"]

    transition = _request_schema(
        spec,
        _operation(
            spec,
            "/api/v1/candidate/applications/{application_id}/transition",
            "post",
        ),
    )
    assert {"target_status", "expected_revision", "reason"} <= set(transition["required"])
    assert transition.get("additionalProperties") is False
