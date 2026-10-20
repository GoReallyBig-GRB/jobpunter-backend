"""PostgreSQL policy-mutation acceptance through the JSON API connector path."""
import json
import os
import socket
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.application.jobs.registered_acquisition import build_acquisition_policy_revalidator
from app.infrastructure.acquisition.json_api import JsonApiConnector
from app.infrastructure.db.models import Source, SourceConnector, User
from app.infrastructure.db.models.target_policy import ForbiddenTargetRule
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
from app.ports.acquisition import AcquisitionOutcome


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


@pytest.mark.parametrize(
    ("mutation", "expected_category"),
    [
        ("source_configuration", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("source_deactivation", "SOURCE_POLICY_CHANGED"),
        ("connector_configuration", "ACQUISITION_CONFIGURATION_CHANGED"),
        ("connector_deactivation", "CONNECTOR_POLICY_CHANGED"),
        ("permission_review_revocation", "PERMISSION_REVIEW_REQUIRED"),
        ("forbidden_rule_added", "FORBIDDEN_TARGET_RULE_MATCHED"),
    ],
)
def test_json_api_blocks_next_page_after_postgres_policy_mutation(
    monkeypatch, mutation, expected_category
):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))
        ],
    )
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=4)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    source_id = connector_id = user_id = None
    endpoint_url = f"https://api-{uuid4().hex}.example.com/jobs"
    second_page_url = f"{endpoint_url}?page=2"

    with SessionLocal() as seed_db:
        admin = User(display_name=f"JSON API policy admin {uuid4()}")
        seed_db.add(admin)
        seed_db.flush()
        user_id = admin.id
        source = Source(
            name=f"JSON API policy source {uuid4()}",
            source_type="JSON_API",
            base_url=endpoint_url,
            status="ACTIVE",
            permission_review_status="APPROVED",
            permission_review_reference="json-api-policy-test-review",
            permission_reviewed_at=datetime.now(timezone.utc),
            configuration={
                "permission_review": {
                    "access_method": "PUBLIC_JOB_BOARD_API",
                    "terms_reference": "https://example.com/terms",
                    "rate_limit_policy": "Respect documented provider rate limits",
                    "rate_limit_reference": "https://example.com/rate-limits",
                }
            },
        )
        seed_db.add(source)
        seed_db.flush()
        connector = SourceConnector(
            source_id=source.id,
            connector_type="JSON_API",
            connector_version="1.0.0",
            status="ACTIVE",
            configuration={"request_method": "GET", "policy_marker": "before"},
            capabilities={"read_only_post": False},
            limits={},
        )
        seed_db.add(connector)
        seed_db.commit()
        source_id, connector_id = source.id, connector.id

    class MutatingClient:
        def __init__(self, mutate):
            self.mutate = mutate
            self.calls = []
            self.mutated = False

        @contextmanager
        def stream(
            self, method, url, *, params=None, headers=None, timeout=None, follow_redirects=None
        ):
            assert method == "GET"
            assert follow_redirects is False
            self.calls.append((url, params))
            if not self.mutated:
                self.mutated = True
                self.mutate()
            payload = {
                "jobs": [
                    {
                        "id": "first-page",
                        "title": "Technical Product Manager",
                        "company": {"name": "Fixture Company"},
                    }
                ]
            }
            response_url = str(url)
            if params:
                response_url = str(httpx.URL(url).copy_merge_params(params))
            yield httpx.Response(
                200,
                content=json.dumps(payload).encode("utf-8"),
                headers={"content-type": "application/json"},
                request=httpx.Request(method, response_url),
            )

    def mutate_policy():
        with SessionLocal() as mutation_db:
            current_source = mutation_db.get(Source, source_id)
            current_connector = mutation_db.get(SourceConnector, connector_id)
            if mutation == "source_configuration":
                current_source.configuration = {
                    **current_source.configuration,
                    "policy_marker": "after",
                }
            elif mutation == "source_deactivation":
                current_source.status = "INACTIVE"
            elif mutation == "connector_configuration":
                current_connector.configuration = {
                    "request_method": "GET",
                    "policy_marker": "after",
                }
            elif mutation == "connector_deactivation":
                current_connector.status = "INACTIVE"
            elif mutation == "permission_review_revocation":
                current_source.permission_review_status = "REVOKED"
            elif mutation == "forbidden_rule_added":
                mutation_db.add(
                    ForbiddenTargetRule(
                        rule_type="URL_EXACT",
                        pattern=second_page_url,
                        connector_scope="JSON_API",
                        reason="Blocked during JSON API acquisition",
                        active=True,
                        created_by_user_id=user_id,
                    )
                )
            else:
                raise AssertionError(f"unexpected test mutation: {mutation}")
            mutation_db.commit()

    try:
        with SessionLocal() as policy_db:
            repository = SqlAlchemyAcquisitionRepository(policy_db)
            source_row = repository.get_source(source_id)
            connector_row = repository.get_connector(connector_id)
            revalidator = build_acquisition_policy_revalidator(
                repository, source_id, connector_id, source_row, connector_row
            )
            client = MutatingClient(mutate_policy)
            result = JsonApiConnector(
                http_client=client, allow_unsafe_http_client=True
            ).acquire(
                source_id=source_id,
                context={
                    "endpoint_url": endpoint_url,
                    "page_param": "page",
                    "page_start": 1,
                    "max_pages": 2,
                    "acquisition_policy_revalidator": revalidator,
                },
            )

        assert result.status == AcquisitionOutcome.PARTIAL
        assert result.error_category == expected_category
        assert [record.external_record_id for record in result.records] == ["first-page"]
        assert len(client.calls) == 1
    finally:
        with SessionLocal() as cleanup_db:
            for rule in cleanup_db.scalars(
                select(ForbiddenTargetRule).where(
                    ForbiddenTargetRule.pattern == second_page_url
                )
            ).all():
                cleanup_db.delete(rule)
            current_connector = cleanup_db.get(SourceConnector, connector_id)
            current_source = cleanup_db.get(Source, source_id)
            current_admin = cleanup_db.get(User, user_id)
            if current_connector is not None:
                cleanup_db.delete(current_connector)
            if current_source is not None:
                cleanup_db.delete(current_source)
            if current_admin is not None:
                cleanup_db.delete(current_admin)
            cleanup_db.commit()
        engine.dispose()
