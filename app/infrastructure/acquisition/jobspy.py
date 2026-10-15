"""JobSpy connector. Network access occurs only when explicitly invoked."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.infrastructure.acquisition.outcomes import result_after_failure
from app.ports.acquisition import (
    AcquisitionAdapterError,
    AcquisitionBatchResult,
    AcquisitionOutcome,
    RawAcquisition,
)


class JobSpyConnector:
    connector_type = "JOBSPY"
    connector_version = "1.0.0"

    _allowed_sites = {
        "indeed",
        "linkedin",
        "zip_recruiter",
        "glassdoor",
        "google",
        "bayt",
        "naukri",
        "bdjobs",
        "cv_library",
        "irishjobs",
        "reed",
        "totaljobs",
        "jobsite",
        "whatjobs",
        "jora",
    }

    def acquire(self, *, source_id: UUID, context: dict[str, Any]) -> AcquisitionBatchResult:
        search_terms = context.get("search_terms")
        location = context.get("location")
        sites = context.get("site_names", ["indeed", "google"])
        results_wanted = context.get("results_wanted", 10)
        hours_old = context.get("hours_old", 72)

        if not isinstance(search_terms, list) or not search_terms:
            raise ValueError("context.search_terms must be a non-empty list")
        if any(not isinstance(term, str) or not term.strip() for term in search_terms):
            raise ValueError("search_terms must contain non-empty strings")
        if not isinstance(location, str) or not location.strip():
            raise ValueError("context.location must be a non-empty string")
        if not isinstance(sites, list) or not sites:
            raise ValueError("context.site_names must be a non-empty list")
        normalized_sites = [str(site).strip().lower() for site in sites]
        unsupported = sorted(set(normalized_sites) - self._allowed_sites)
        if unsupported:
            raise ValueError(f"Unsupported JobSpy site name(s): {', '.join(unsupported)}")
        if not isinstance(results_wanted, int) or isinstance(results_wanted, bool) or not 1 <= results_wanted <= 100:
            raise ValueError("results_wanted must be an integer from 1 to 100")
        if not isinstance(hours_old, int) or isinstance(hours_old, bool) or not 1 <= hours_old <= 720:
            raise ValueError("hours_old must be an integer from 1 to 720")

        try:
            from jobspy import scrape_jobs
        except ImportError as exc:
            raise AcquisitionAdapterError(
                AcquisitionOutcome.UNAVAILABLE,
                "JOBSPY_DEPENDENCY_UNAVAILABLE",
            ) from exc

        observed_at = datetime.now(timezone.utc)
        acquired: list[RawAcquisition] = []
        for term in search_terms:
            try:
                frame = scrape_jobs(
                    site_name=normalized_sites,
                    search_term=term.strip(),
                    location=location.strip(),
                    results_wanted=results_wanted,
                    hours_old=hours_old,
                    country_indeed=context.get("country_indeed", "Nigeria"),
                )
            except Exception:
                return result_after_failure(
                    acquired,
                    status=AcquisitionOutcome.UNAVAILABLE,
                    error_category="JOBSPY_QUERY_FAILED",
                )
            if frame is None:
                continue
            for row in frame.to_dict(orient="records"):
                raw = self._json_safe(row)
                raw["query"] = term.strip()
                raw["requested_location"] = location.strip()
                raw["connector"] = self.connector_type
                raw["connector_version"] = self.connector_version
                serialized = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                content_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
                source_url = self._optional_string(raw.get("job_url") or raw.get("url"))
                external_id = self._optional_string(raw.get("id"))
                acquired.append(
                    RawAcquisition(
                        source_id=source_id,
                        external_record_id=external_id,
                        source_url=source_url,
                        raw_content=raw,
                        content_hash=content_hash,
                        observed_at=observed_at,
                    )
                )
        return AcquisitionBatchResult(
            status=AcquisitionOutcome.SUCCEEDED,
            records=tuple(acquired),
        )

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        # Pandas/NumPy missing values and scalar types are normalized without
        # making pandas a hard dependency of the connector module.
        if value.__class__.__name__ in {"NAType", "NaTType"}:
            return None
        if hasattr(value, "item") and callable(value.item):
            try:
                value = value.item()
            except (ValueError, TypeError):
                pass
        if isinstance(value, (str, int, float, bool)):
            return value
        if hasattr(value, "isoformat") and callable(value.isoformat):
            try:
                return value.isoformat()
            except (ValueError, TypeError):
                pass
        return str(value)

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
