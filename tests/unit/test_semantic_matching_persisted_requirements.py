import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.opportunities.semantic_matching import match_requirements_to_evidence


class FakeRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeDB:
    def __init__(self, requirements):
        self.requirements = requirements

    def scalars(self, _statement):
        return FakeRows(self.requirements)


def make_requirement(**overrides):
    values = {
        "id": uuid4(),
        "job_description_version_id": uuid4(),
        "requirement_type": "SKILL",
        "description": "Experience with SQL query design",
        "importance": "HIGH",
        "severity": "MUST_HAVE",
        "hard_constraint": True,
        "extraction_status": "CONFIRMED",
        "source_excerpt": "Experience with SQL query design.",
        "extraction_confidence": 0.93,
        "extraction_version": "job-requirement-extraction-v2",
        "created_at": None,
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_matching_waits_when_no_persisted_requirements_exist(monkeypatch):
    async def should_not_call_ai(*_args, **_kwargs):
        raise AssertionError("AI must not re-extract requirements during evaluation")

    monkeypatch.setattr(
        "app.application.opportunities.semantic_matching.execute_ai_task",
        should_not_call_ai,
    )
    result, traces, reason = asyncio.run(match_requirements_to_evidence(
        db=FakeDB([]),
        user_id=uuid4(),
        job={"canonical_title": "Technical Product Manager"},
        description="Experience with SQL query design.",
        description_version_id=uuid4(),
        requirement_extraction_status="COMPLETE",
        approved_evidence=[{"id": "ev-1", "content": "SQL query design."}],
    ))
    assert result is None
    assert traces == []
    assert "No persisted requirements" in reason


def test_matching_waits_for_human_review_of_extracted_requirements(monkeypatch):
    requirement = make_requirement(extraction_status="NEEDS_REVIEW")

    async def should_not_call_ai(*_args, **_kwargs):
        raise AssertionError("Unreviewed requirements must not be matched")

    monkeypatch.setattr(
        "app.application.opportunities.semantic_matching.execute_ai_task",
        should_not_call_ai,
    )
    result, traces, reason = asyncio.run(match_requirements_to_evidence(
        db=FakeDB([requirement]),
        user_id=uuid4(),
        job={"canonical_title": "Technical Product Manager"},
        description="Experience with SQL query design.",
        description_version_id=requirement.job_description_version_id,
        requirement_extraction_status="NEEDS_REVIEW",
        approved_evidence=[{"id": "ev-1", "content": "SQL query design."}],
    ))
    assert result is None
    assert traces == []
    assert "human review" in reason


def test_matching_waits_for_extraction_to_reach_reviewable_state(monkeypatch):
    requirement = make_requirement()

    async def should_not_call_ai(*_args, **_kwargs):
        raise AssertionError("Unstable extraction must not be matched")

    monkeypatch.setattr(
        "app.application.opportunities.semantic_matching.execute_ai_task",
        should_not_call_ai,
    )
    result, traces, reason = asyncio.run(match_requirements_to_evidence(
        db=FakeDB([requirement]),
        user_id=uuid4(),
        job={"canonical_title": "Technical Product Manager"},
        description="Experience with SQL query design.",
        description_version_id=requirement.job_description_version_id,
        requirement_extraction_status="IN_PROGRESS",
        approved_evidence=[{"id": "ev-1", "content": "SQL query design."}],
    ))
    assert result is None
    assert traces == []
    assert "stable reviewable state" in reason


def test_matching_rejects_confirmed_requirement_without_extraction_version(monkeypatch):
    requirement = make_requirement(extraction_version=None)

    async def should_not_call_ai(*_args, **_kwargs):
        raise AssertionError("Requirements without extraction provenance must not be matched")

    monkeypatch.setattr(
        "app.application.opportunities.semantic_matching.execute_ai_task",
        should_not_call_ai,
    )
    result, traces, reason = asyncio.run(match_requirements_to_evidence(
        db=FakeDB([requirement]),
        user_id=uuid4(),
        job={"canonical_title": "Technical Product Manager"},
        description="Experience with SQL query design.",
        description_version_id=requirement.job_description_version_id,
        requirement_extraction_status="NEEDS_REVIEW",
        approved_evidence=[{"id": "ev-1", "content": "SQL query design."}],
    ))
    assert result is None
    assert traces == []
    assert "extraction-version provenance" in reason

def test_matching_waits_for_prd_severity_classification(monkeypatch):
    requirement = make_requirement(severity=None)

    async def should_not_call_ai(*_args, **_kwargs):
        raise AssertionError("Unclassified requirements must not be scored")

    monkeypatch.setattr(
        "app.application.opportunities.semantic_matching.execute_ai_task",
        should_not_call_ai,
    )
    result, traces, reason = asyncio.run(match_requirements_to_evidence(
        db=FakeDB([requirement]),
        user_id=uuid4(),
        job={"canonical_title": "Technical Product Manager"},
        description="Experience with SQL query design.",
        description_version_id=requirement.job_description_version_id,
        requirement_extraction_status="COMPLETE",
        approved_evidence=[{"id": "ev-1", "content": "SQL query design."}],
    ))
    assert result is None
    assert traces == []
    assert "lacks a valid PRD severity classification" in reason


def test_matching_uses_confirmed_versioned_requirements_and_evidence_refs(monkeypatch):
    requirement = make_requirement()
    captured = {}

    async def fake_execute(request, **_kwargs):
        captured["request"] = request
        import json
        prompt = json.loads(request.prompt)
        captured["prompt"] = prompt
        return SimpleNamespace(
            parsed_data={"matches": [{
                "requirement_id": str(requirement.id),
                "requirement_text": requirement.description,
                "status": "DIRECT",
                "rationale": "The approved evidence supports the confirmed requirement.",
                "evidence_refs": ["ev-1"],
            }]},
            execution_id=None,
            provider_used="test",
            model_used="test-model",
        )

    monkeypatch.setattr(
        "app.application.opportunities.semantic_matching.execute_ai_task",
        fake_execute,
    )
    matches, traces, reason = asyncio.run(match_requirements_to_evidence(
        db=FakeDB([requirement]),
        user_id=uuid4(),
        job={"canonical_title": "Technical Product Manager"},
        description="Experience with SQL query design.",
        description_version_id=requirement.job_description_version_id,
        requirement_extraction_status="NEEDS_REVIEW",
        approved_evidence=[{"id": "ev-1", "content": "SQL query design."}],
        transferability_policy="STRICT",
        evidence_emphasis=["SQL delivery"],
        preferred_terminology=["technical product management"],
        avoided_terminology=["rockstar"],
        other_search_constraints=["Avoid roles requiring regular overnight shifts"],
    ))

    assert reason is None
    assert traces[0]["task"] == "evidence_matching"
    assert captured["request"].task == "evidence_matching"
    assert captured["prompt"]["requirements"][0]["requirement_id"] == str(requirement.id)
    assert captured["prompt"]["requirements"][0]["severity"] == "MUST_HAVE"
    assert captured["prompt"]["candidate_preferences"]["transferability_policy"] == "STRICT"
    assert captured["prompt"]["candidate_preferences"]["evidence_emphasis"] == ["SQL delivery"]
    assert captured["prompt"]["candidate_preferences"]["other_search_constraints"] == [
        "Avoid roles requiring regular overnight shifts"
    ]
    assert matches[0]["mandatory"] is True
    assert matches[0]["severity"] == "MUST_HAVE"
    assert matches[0]["dimension"] == "technical_fit"
    assert matches[0]["source_excerpt"] == requirement.source_excerpt
    assert matches[0]["extraction_version"] == requirement.extraction_version
    assert matches[0]["evidence_refs"] == ["ev-1"]
