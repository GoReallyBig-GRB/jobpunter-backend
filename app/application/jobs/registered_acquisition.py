"""Run a configured acquisition connector after enforcing source/connector state."""

from copy import deepcopy
import hashlib
import json
from uuid import UUID

from app.application.jobs.service import AcquisitionResult, AcquisitionService
from app.application.jobs.source_governance import assert_automated_acquisition_permitted
from app.application.jobs.target_policy import ForbiddenTargetError, assert_target_not_forbidden
from app.ports.acquisition import AcquisitionAdapterError, AcquisitionOutcome
from app.infrastructure.acquisition.registry import AcquisitionConnectorRegistry
from app.ports.repositories.acquisition import AcquisitionRepository


def build_acquisition_policy_revalidator(
    repository: AcquisitionRepository,
    source_id: UUID,
    connector_id: UUID,
    source_row: object,
    connector_row: object,
):
    """Build a per-request guard tied to the source and connector authorized for this run."""
    source_configuration = deepcopy(getattr(source_row, "configuration", None) or {})
    source_base_url = getattr(source_row, "base_url", None)
    source_type = getattr(source_row, "source_type", None)
    permission_review_reference = getattr(source_row, "permission_review_reference", None)
    permission_reviewed_at = getattr(source_row, "permission_reviewed_at", None)
    connector_configuration = deepcopy(getattr(connector_row, "configuration", None) or {})
    connector_capabilities = deepcopy(getattr(connector_row, "capabilities", None) or {})
    connector_limits = deepcopy(getattr(connector_row, "limits", None) or {})
    connector_configuration_version = getattr(connector_row, "active_configuration_version", None)
    connector_version = str(getattr(connector_row, "connector_version", "")).strip()

    def revalidate_request_policy(*, url: str, connector_type: str, method: str) -> None:
        if not isinstance(method, str) or method.strip().upper() != "GET":
            raise AcquisitionAdapterError(
                AcquisitionOutcome.RESTRICTED, "ACQUISITION_METHOD_NOT_ALLOWED"
            )
        current_source = repository.get_source(source_id)
        current_connector = repository.get_connector(connector_id)
        if current_source is None or str(current_source.status).strip().upper() != "ACTIVE":
            raise AcquisitionAdapterError(AcquisitionOutcome.RESTRICTED, "SOURCE_POLICY_CHANGED")
        if (
            current_connector is None or current_connector.source_id != source_id
            or str(current_connector.status).strip().upper() != "ACTIVE"
        ):
            raise AcquisitionAdapterError(AcquisitionOutcome.RESTRICTED, "CONNECTOR_POLICY_CHANGED")
        try:
            assert_automated_acquisition_permitted(current_source)
        except ValueError as exc:
            raise AcquisitionAdapterError(
                AcquisitionOutcome.RESTRICTED, "PERMISSION_REVIEW_REQUIRED"
            ) from exc
        if (
            (getattr(current_source, "configuration", None) or {}) != source_configuration
            or getattr(current_source, "base_url", None) != source_base_url
            or getattr(current_source, "source_type", None) != source_type
            or getattr(current_source, "permission_review_reference", None)
            != permission_review_reference
            or getattr(current_source, "permission_reviewed_at", None) != permission_reviewed_at
            or (getattr(current_connector, "configuration", None) or {}) != connector_configuration
            or (getattr(current_connector, "capabilities", None) or {}) != connector_capabilities
            or (getattr(current_connector, "limits", None) or {}) != connector_limits
            or getattr(current_connector, "active_configuration_version", None)
            != connector_configuration_version
            or str(current_connector.connector_type).strip().upper()
            != str(connector_type).strip().upper()
            or str(current_connector.connector_version).strip() != connector_version
        ):
            raise AcquisitionAdapterError(
                AcquisitionOutcome.RESTRICTED, "ACQUISITION_CONFIGURATION_CHANGED"
            )
        current_rules = repository.list_active_forbidden_target_rules()
        try:
            assert_target_not_forbidden(url, current_rules, connector_type=connector_type)
        except (ForbiddenTargetError, TypeError, ValueError) as exc:
            raise AcquisitionAdapterError(
                AcquisitionOutcome.RESTRICTED, "FORBIDDEN_TARGET_RULE_MATCHED"
            ) from exc

    def fingerprint(value: object) -> str:
        serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    revalidate_request_policy.policy_snapshot = {
        "source_id": str(source_id),
        "connector_id": str(connector_id),
        "source_configuration_sha256": fingerprint(source_configuration),
        "source_base_url": source_base_url,
        "source_type": source_type,
        "permission_review_reference": permission_review_reference,
        "permission_reviewed_at": (
            permission_reviewed_at.isoformat()
            if hasattr(permission_reviewed_at, "isoformat")
            else str(permission_reviewed_at) if permission_reviewed_at is not None else None
        ),
        "connector_configuration_sha256": fingerprint(connector_configuration),
        "connector_capabilities_sha256": fingerprint(connector_capabilities),
        "connector_limits_sha256": fingerprint(connector_limits),
        "connector_configuration_version": connector_configuration_version,
        "connector_type": str(getattr(connector_row, "connector_type", "")).strip().upper(),
        "connector_version": connector_version,
    }
    return revalidate_request_policy

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
        merged_context["forbidden_target_rules"] = rules
        merged_context["acquisition_policy_revalidator"] = build_acquisition_policy_revalidator(
            self.repository, source_id, connector_id, source_row, connector_row
        )
        connector = self.registry.create(
            connector_row.connector_type,
            connector_row.connector_version,
        )
        return AcquisitionService(self.repository, connector).run(
            source_id, connector_id, run_key, merged_context
        )
