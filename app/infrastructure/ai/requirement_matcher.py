"""Configurable OpenAI-compatible structured requirement assessor.

No provider is required at import time. If the endpoint, model, and API key are
configured, this adapter asks the model for explicit requirement assessments.
The deterministic domain evaluator remains responsible for evidence validation,
hard constraints, scoring, and the final outcome.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx


class MatcherConfigurationError(RuntimeError):
    pass


class MatcherProviderError(RuntimeError):
    pass


_PII_KEYS = {
    "name", "full_name", "first_name", "last_name", "email", "phone",
    "phone_number", "mobile", "address", "street_address", "date_of_birth",
    "dob", "national_id", "passport_number", "ssn",
}
_EMAIL_PATTERN = re.compile(r"\\b[\\w.+-]+@[\\w.-]+\\.[A-Za-z]{2,}\\b")
_PHONE_PATTERN = re.compile(r"(?<!\\w)(?:\\+?\\d[\\d .()/-]{7,}\\d)(?!\\w)")


def _sanitize_for_matching(value: Any, *, depth: int = 0) -> Any:
    """Remove common direct identifiers and bound candidate payload size."""
    if depth >= 6:
        return "[truncated]"
    if isinstance(value, Mapping):
        sanitized = {}
        for key, item in list(value.items())[:100]:
            normalized_key = str(key).strip().lower().replace("-", "_").replace(" ", "_")
            if normalized_key in _PII_KEYS:
                continue
            sanitized[str(key)[:100]] = _sanitize_for_matching(item, depth=depth + 1)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize_for_matching(item, depth=depth + 1) for item in list(value)[:100]]
    if isinstance(value, str):
        bounded = value[:8000]
        bounded = _EMAIL_PATTERN.sub("[redacted-email]", bounded)
        return _PHONE_PATTERN.sub("[redacted-phone]", bounded)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:1000]


class OpenAICompatibleRequirementMatcher:
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname:
            raise MatcherConfigurationError("matcher endpoint must be an absolute HTTPS URL")
        if not api_key.strip() or not model.strip():
            raise MatcherConfigurationError("matcher API key and model are required")
        if timeout_seconds <= 0:
            raise MatcherConfigurationError("timeout_seconds must be positive")
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.client = client

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleRequirementMatcher | None":
        endpoint = os.getenv("JOBPUNTER_MATCHER_ENDPOINT", "").strip()
        api_key = os.getenv("JOBPUNTER_MATCHER_API_KEY", "").strip()
        model = os.getenv("JOBPUNTER_MATCHER_MODEL", "").strip()
        if not any((endpoint, api_key, model)):
            return None
        if not all((endpoint, api_key, model)):
            raise MatcherConfigurationError(
                "Set JOBPUNTER_MATCHER_ENDPOINT, JOBPUNTER_MATCHER_API_KEY, "
                "and JOBPUNTER_MATCHER_MODEL together"
            )
        return cls(
            endpoint=endpoint,
            api_key=api_key,
            model=model,
            timeout_seconds=float(os.getenv("JOBPUNTER_MATCHER_TIMEOUT_SECONDS", "30")),
        )

    def __call__(
        self,
        requirements: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        job: Any,
    ) -> list[Mapping[str, Any]]:
        allowed_requirement_ids = {str(item["id"]) for item in requirements}
        allowed_evidence_ids = {str(item["id"]) for item in evidence if item.get("id") is not None}
        payload = {
            "job": {
                "title": getattr(job, "canonical_title", None),
                "company": getattr(job, "company_name", None),
                "description": getattr(job, "description", None),
            },
            "requirements": _sanitize_for_matching(requirements),
            "candidate_claims": _sanitize_for_matching(claims),
            "candidate_evidence": _sanitize_for_matching(evidence),
            "output_contract": {
                "assessments": [
                    {
                        "requirement_id": "one provided requirement ID",
                        "result": "MATCH | NO_MATCH | UNKNOWN",
                        "evidence_ids": ["only IDs from candidate_evidence"],
                        "rationale": "brief evidence-based rationale",
                    }
                ]
            },
            "rules": [
                "Assess each requirement independently.",
                "Use only the supplied candidate claims and evidence.",
                "Never invent credentials, skills, employment history, or evidence IDs.",
                "Use MATCH only when supplied evidence directly supports the requirement.",
                "Use NO_MATCH only when supplied candidate information clearly contradicts or fails a stated hard constraint.",
                "Use UNKNOWN when evidence is missing, ambiguous, or insufficient.",
                "Return one assessment per requirement and no extra requirement IDs.",
                "Return JSON only, with a top-level assessments array.",
            ],
        }
        request_body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a cautious job-requirement evidence assessor. "
                        "Do not make hiring decisions. Output only valid JSON."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, default=str)},
            ],
        }
        try:
            if self.client is None:
                with httpx.Client(
                    timeout=self.timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    response = client.post(
                        self.endpoint,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=request_body,
                    )
            else:
                response = self.client.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=request_body,
                    timeout=self.timeout_seconds,
                )
            response.raise_for_status()
            response_payload = response.json()
            content = response_payload["choices"][0]["message"]["content"]
            decoded = json.loads(content)
            assessments = decoded.get("assessments") if isinstance(decoded, dict) else None
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise MatcherProviderError(f"matcher provider returned an unusable response: {type(exc).__name__}") from exc

        if not isinstance(assessments, list):
            raise MatcherProviderError("matcher response must contain an assessments array")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in assessments:
            if not isinstance(item, dict):
                raise MatcherProviderError("each assessment must be an object")
            requirement_id = str(item.get("requirement_id", ""))
            result = str(item.get("result", "")).upper()
            evidence_ids = item.get("evidence_ids", [])
            if requirement_id not in allowed_requirement_ids:
                raise MatcherProviderError("matcher referenced an unknown requirement")
            if requirement_id in seen:
                raise MatcherProviderError("matcher returned duplicate requirement assessments")
            if result not in {"MATCH", "NO_MATCH", "UNKNOWN"}:
                raise MatcherProviderError("matcher returned an invalid result")
            if not isinstance(evidence_ids, list) or any(
                str(value) not in allowed_evidence_ids for value in evidence_ids
            ):
                raise MatcherProviderError("matcher referenced unavailable candidate evidence")
            if result == "MATCH" and not evidence_ids:
                # The domain evaluator will also enforce this, but fail closed here.
                result = "UNKNOWN"
            normalized.append({
                "requirement_id": requirement_id,
                "result": result,
                "evidence_ids": [str(value) for value in evidence_ids],
                "rationale": str(item.get("rationale", ""))[:2000],
            })
            seen.add(requirement_id)

        # Missing assessments intentionally remain missing; the domain policy maps them to review.
        return normalized


def configured_requirement_matcher() -> OpenAICompatibleRequirementMatcher | None:
    """Return the configured matcher, or None when matching is deliberately disabled."""
    return OpenAICompatibleRequirementMatcher.from_environment()
