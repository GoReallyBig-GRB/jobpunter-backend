"""Run a configured acquisition connector after enforcing source/connector state."""

from uuid import UUID

from app.application.jobs.service import AcquisitionResult, AcquisitionService
from app.application.jobs.source_governance import assert_automated_acquisition_permitted
from app.application.jobs.target_policy import ForbiddenTargetError, assert_target_not_forbidden
from app.infrastructure.acquisition.registry import AcquisitionConnectorRegistry
from app.ports.repositories.acquisition import AcquisitionRepository


class RegisteredAcquisitionService:
    def __init__(
        self,
        repository: AcquisitionRepository,
        registry: AcquisitionConnectorRegistry | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry or AcquisitionConnectorRegistry()

    def run(
        self, source_id: UUID, connector_id: UUID, run_key: str, context: dict
    ) -> AcquisitionResult:
        source_row = self.repository.get_source(source_id)
        if source_row is None:
            raise ValueError("source does not exist")
        if str(source_row.status).strip().upper() != "ACTIVE":
            raise ValueError("source is not active")
        connector_row = self.repository.get_connector(connector_id)
        if connector_row is None or connector_row.source_id != source_id:
            raise ValueError("connector does not belong to source")
        if str(connector_row.status).strip().upper() != "ACTIVE":
            raise ValueError("connector is not active")
        try:
            assert_automated_acquisition_permitted(source_row)
        except ValueError:
            self.repository.record_source_operational_state(
                source_id, "RESTRICTED", "PERMISSION_REVIEW_REQUIRED"
            )
            raise

        # Persisted configuration provides defaults; explicit per-run values override them.
        # Runtime context must be a mapping to avoid surprising or unsafe coercion.
        if not isinstance(context, dict):
            raise ValueError("acquisition context must be an object")
        merged_context: dict = {}
        for config in (source_row.configuration, connector_row.configuration, context):
            if config is None:
                continue
            if not isinstance(config, dict):
                raise ValueError("source and connector configuration must be objects")
            merged_context.update(config)

        rules = self.repository.list_active_forbidden_target_rules()
        connector_type = str(connector_row.connector_type).strip().upper()
        # JobSpy delegates network access to an external library that does not expose a
        # per-request interception hook. Fail closed whenever a rule applies rather than
        # claim that its outbound requests are policy-enforced.
        if connector_type == "JOBSPY" and any(
            rule.get("active", True)
            and str(rule.get("connector_scope", "ALL")).upper() in {"ALL", "JOBSPY"}
            for rule in rules
        ):
            raise ValueError("forbidden-target policy cannot be enforced by the JOBSPY adapter")

        target_urls = []
        if connector_type in {"SCRAPY", "PLAYWRIGHT"}:
            configured_urls = merged_context.get("start_urls")
            if isinstance(configured_urls, list):
                target_urls.extend(url for url in configured_urls if isinstance(url, str))
        elif connector_type == "JSON_API":
            endpoint = merged_context.get("endpoint_url")
            if isinstance(endpoint, str):
                target_urls.append(endpoint)
        elif connector_type == "RSS_ATOM":
            feed_url = merged_context.get("feed_url")
            if isinstance(feed_url, str):
                target_urls.append(feed_url)
        elif connector_type == "GREENHOUSE_JOB_BOARD":
            target_urls.append(source_row.base_url)

        for target_url in target_urls:
            try:
                assert_target_not_forbidden(target_url, rules, connector_type=connector_type)
            except ForbiddenTargetError as exc:
                raise ValueError(
                    f"acquisition target denied by forbidden-target rule {exc.rule_id}"
                ) from exc

        merged_context["forbidden_target_rules"] = rules
        connector = self.registry.create(
            connector_row.connector_type,
            connector_row.connector_version,
        )
        return AcquisitionService(self.repository, connector).run(
            source_id, connector_id, run_key, merged_context
        )
