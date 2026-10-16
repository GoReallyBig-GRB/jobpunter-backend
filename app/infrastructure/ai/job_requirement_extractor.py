"""Grounded job-description requirement extraction with mandatory human review."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx


EXTRACTION_VERSION = "job-requirement-extraction-v3"
REQUIREMENT_TYPES = {
    "SKILL", "EXPERIENCE", "EDUCATION", "CERTIFICATION", "LOCATION",
    "WORK_AUTHORIZATION", "WORK_MODE", "EMPLOYMENT_TYPE", "OTHER",
}
IMPORTANCE_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
REQUIREMENT_SEVERITIES = {"MUST_HAVE", "IMPORTANT", "PREFERRED", "CONTEXTUAL"}
REQUIREMENT_DIMENSIONS = {"CORE_ROLE", "DOMAIN", "TECHNICAL", "SENIORITY", "EDUCATION", "LOCATION", "OTHER"}
_EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?\d[\d .()/-]{7,}\d)(?!\w)")


class RequirementExtractionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExtractedRequirement:
    requirement_type: str
    description: str
    source_excerpt: str
    importance: str
    severity: str
    dimension: str
    hard_constraint: bool
    confidence: float


def _normalized_quote(value: str) -> str:
    return " ".join(value.casefold().split())


def _safe_description_for_provider(description: str) -> str:
    text = _EMAIL_PATTERN.sub("[redacted-email]", description[:100_000])
    return _PHONE_PATTERN.sub("[redacted-phone]", text)


class OpenAICompatibleJobRequirementExtractor:
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
            raise ValueError("extraction endpoint must be an absolute HTTPS URL")
        if not api_key.strip() or not model.strip():
            raise ValueError("extraction API key and model are required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.client = client

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleJobRequirementExtractor | None":
        endpoint = os.getenv("JOBPUNTER_MATCHER_ENDPOINT", "").strip()
        api_key = os.getenv("JOBPUNTER_MATCHER_API_KEY", "").strip()
        model = os.getenv("JOBPUNTER_MATCHER_MODEL", "").strip()
        if not any((endpoint, api_key, model)):
            return None
        if not all((endpoint, api_key, model)):
            raise ValueError(
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
        *,
        title: str,
        company: str | None,
        description: str,
    ) -> list[ExtractedRequirement]:
        safe_description = _safe_description_for_provider(description)
        payload = {
            "job_title": title,
            "company": company,
            "job_description": safe_description,
            "output_contract": {
                "requirements": [{
                    "requirement_type": "SKILL | EXPERIENCE | EDUCATION | CERTIFICATION | LOCATION | WORK_AUTHORIZATION | WORK_MODE | EMPLOYMENT_TYPE | OTHER",
                    "description": "one concise, atomic requirement",
                    "source_excerpt": "verbatim supporting excerpt copied from job_description",
                    "importance": "LOW | MEDIUM | HIGH | CRITICAL",
                    "severity": "MUST_HAVE | IMPORTANT | PREFERRED | CONTEXTUAL",
                    "dimension": "CORE_ROLE | DOMAIN | TECHNICAL | SENIORITY | EDUCATION | LOCATION | OTHER",
                    "hard_constraint": "boolean; true only when the text makes it mandatory",
                    "confidence": "number from 0 to 1",
                }]
            },
            "rules": [
                "Extract only explicit requirements or clearly stated role conditions.",
                "Do not infer requirements from the title or general industry norms.",
                "Every source_excerpt must be a verbatim substring of job_description.",
                "Split independent requirements into separate entries.",
                "Assign severity from explicit JD wording: MUST_HAVE for stated required qualifications, IMPORTANT for material qualifications or responsibilities central to the role, PREFERRED for explicitly preferred qualifications, and CONTEXTUAL only for background or context that does not impose a material qualification or responsibility.",
                "Do not infer severity from a job title or industry convention; use the actual description wording.",
                "Classify each requirement by its primary fit dimension: CORE_ROLE for core role capability/responsibility, DOMAIN for sector/domain knowledge, TECHNICAL for tools/technologies/methods, SENIORITY for experience level or scope, EDUCATION for degrees/certifications/licensure, LOCATION for location/work-mode/work-authorization constraints, and OTHER only when none fit.",
                "Do not use keyword-only guesses when the JD context makes the primary dimension clear.",
                "Use OTHER for statements that do not fit another category.",
                "Do not include contact details or personal data.",
                "Return an empty requirements array if no explicit requirements are present.",
                "Return JSON only.",
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
                        "Extract explicit job requirements faithfully. Do not invent or infer. "
                        "All extracted items will require human review."
                    ),
                },
                {"role": "user", "content": json.dumps(payload)},
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
            content = response.json()["choices"][0]["message"]["content"]
            decoded = json.loads(content)
            items = decoded.get("requirements") if isinstance(decoded, dict) else None
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise RequirementExtractionError(
                f"requirement extractor returned an unusable response: {type(exc).__name__}"
            ) from exc

        if not isinstance(items, list):
            raise RequirementExtractionError("extractor response must contain a requirements array")

        extracted: list[ExtractedRequirement] = []
        seen: set[tuple[str, str]] = set()
        normalized_description = _normalized_quote(safe_description)
        for item in items:
            if not isinstance(item, dict):
                raise RequirementExtractionError("each extracted requirement must be an object")
            kind = str(item.get("requirement_type", "")).upper()
            importance = str(item.get("importance", "")).upper()
            severity = str(item.get("severity", "")).upper()
            dimension = str(item.get("dimension", "")).upper()
            statement = str(item.get("description", "")).strip()
            excerpt = str(item.get("source_excerpt", "")).strip()
            hard_constraint = item.get("hard_constraint")
            confidence = item.get("confidence")
            if kind not in REQUIREMENT_TYPES or importance not in IMPORTANCE_LEVELS or severity not in REQUIREMENT_SEVERITIES or dimension not in REQUIREMENT_DIMENSIONS:
                raise RequirementExtractionError("invalid requirement type, importance, PRD severity, or fit dimension")
            if not statement or len(statement) > 2000 or not excerpt or len(excerpt) > 4000:
                raise RequirementExtractionError("requirement description or source excerpt is invalid")
            if type(hard_constraint) is not bool:
                raise RequirementExtractionError("hard_constraint must be a boolean")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                raise RequirementExtractionError("confidence must be a number from 0 to 1")
            if _normalized_quote(excerpt) not in normalized_description:
                raise RequirementExtractionError("source excerpt is not grounded in the job description")
            key = (_normalized_quote(statement), _normalized_quote(excerpt))
            if key in seen:
                continue
            seen.add(key)
            extracted.append(ExtractedRequirement(
                requirement_type=kind,
                description=statement,
                source_excerpt=excerpt,
                importance=importance,
                severity=severity,
                dimension=dimension,
                hard_constraint=hard_constraint,
                confidence=float(confidence),
            ))
            if len(extracted) > 100:
                raise RequirementExtractionError("extractor returned more than 100 requirements")
        return extracted


def configured_job_requirement_extractor() -> OpenAICompatibleJobRequirementExtractor | None:
    return OpenAICompatibleJobRequirementExtractor.from_environment()
