import asyncio
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.application.ai.schemas import GenerateRequest
from app.application.ai.service import execute_ai_task
from app.infrastructure.ai.google_generation import ProviderResult


def _approved_evidence():
    return [{
        "id": "evidence-1",
        "type": "experience",
        "content": "Owned product roadmap and delivery for a software platform.",
        "revision": 1,
    }]


def _matching_prompt():
    return json.dumps({
        "job_title": "Product Manager",
        "employer": "Example Ltd",
        "job_description_version_id": "8d87d52e-7ca1-4b6f-9b40-2f9f5d821d71",
        "requirements": [{
            "requirement_id": "REQ-1",
            "requirement_text": "Experience managing software products",
            "mandatory": True,
        }],
        "approved_candidate_evidence": _approved_evidence(),
        "candidate_preferences": {"transferability_policy": "MODERATE"},
        "instruction": "Compare each requirement only with the supplied approved evidence.",
    })


def _suggested_answers_prompt():
    return json.dumps({
        "questions": ["Why this role?"],
        "approved_candidate_evidence": _approved_evidence(),
        "instruction": "Draft evidence-grounded answers.",
    })


def _cover_letter_prompt():
    return json.dumps({
        "job_title": "Product Manager",
        "employer": "Example Ltd",
        "job_description": (
            "Responsibilities include leading product discovery, writing clear requirements, "
            "coordinating technical delivery, measuring outcomes, planning launches, and improving "
            "customer workflows. The role will manage cross-functional collaboration and ensure "
            "reliable execution. Qualifications include product management experience, analytical "
            "ability, strong stakeholder communication skills, and demonstrated delivery leadership "
            "across complex software products."
        ),
        "approved_candidate_evidence": _approved_evidence(),
        "instruction": "Write from supplied evidence only.",
    })


class FakeSession:
    def __init__(self):
        self.added = []
        self.commit_count = 0

    def scalar(self, _statement):
        return None

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commit_count += 1

    def refresh(self, _row):
        return None


def valid_matching_result():
    return {
        "matches": [
            {
                "requirement_id": "REQ-1",
                "requirement_text": "Experience managing software products",
                "status": "direct",
                "rationale": "The approved evidence explicitly documents product ownership.",
                "evidence_refs": ["evidence-1"],
            }
        ]
    }


def test_evidence_matching_forces_json_and_validates_before_success(monkeypatch):
    db = FakeSession()
    calls = []

    async def fake_generate(**kwargs):
        calls.append(kwargs)
        return ProviderResult(
            raw_text='{"matches": []}',
            parsed_data=valid_matching_result(),
            model_used="gemini-test",
            provider_used="google",
            latency_ms=17,
        )

    monkeypatch.setattr("app.application.ai.service.get_ai_provider", lambda _provider: type("FakeAdapter", (), {"generate": staticmethod(fake_generate)})())
    request = GenerateRequest(task="evidence_matching", prompt=_matching_prompt())

    response = asyncio.run(execute_ai_task(
        request, db=db, user_id=uuid4(), request_id="evidence-valid",
    ))

    assert calls[0]["json_mode"] is True
    assert response.parsed_data["matches"][0]["status"] == "direct"
    assert db.added[0].status == "SUCCEEDED"
    assert db.added[0].task_policy_version == "v1"
    assert db.added[0].system_policy_version == "v1"
    assert db.added[0].error_code is None


def test_other_structured_task_uses_its_own_output_contract(monkeypatch):
    db = FakeSession()
    calls = []

    async def fake_generate(**kwargs):
        calls.append(kwargs)
        return ProviderResult(
            raw_text='{"answers": [{"question": "Why this role?", "answer": "Supported by evidence.", "evidence_basis": ["evidence-1"], "confidence": "medium", "unsupported_claims": []}]}',
            parsed_data={
                "answers": [{
                    "question": "Why this role?",
                    "answer": "Supported by evidence.",
                    "evidence_basis": ["evidence-1"],
                    "confidence": "medium",
                    "unsupported_claims": [],
                }]
            },
            model_used="gemini-test",
            provider_used="google",
            latency_ms=12,
        )

    monkeypatch.setattr("app.application.ai.service.get_ai_provider", lambda _provider: type("FakeAdapter", (), {"generate": staticmethod(fake_generate)})())
    request = GenerateRequest(
        task="suggested_answers",
        prompt=_suggested_answers_prompt(),
        options={"jsonMode": True},
    )

    response = asyncio.run(execute_ai_task(request, db=db, user_id=uuid4()))

    assert calls[0]["json_mode"] is True
    assert response.parsed_data == {
        "answers": [{
            "question": "Why this role?",
            "answer": "Supported by evidence.",
            "evidence_basis": ["evidence-1"],
            "confidence": "medium",
            "unsupported_claims": [],
        }]
    }
    assert db.added[0].status == "SUCCEEDED"


def test_production_cover_letter_uses_schema_even_when_json_mode_is_false(monkeypatch):
    db = FakeSession()
    calls = []

    async def fake_generate(**kwargs):
        calls.append(kwargs)
        return ProviderResult(
            raw_text='{"content": "A grounded letter.", "missing_information": [], "unsupported_claims": []}',
            parsed_data={
                "content": "A grounded letter.",
                "missing_information": [],
                "unsupported_claims": [],
            },
            model_used="gemini-test",
            provider_used="google",
            latency_ms=9,
        )

    monkeypatch.setattr("app.application.ai.service.get_ai_provider", lambda _provider: type("FakeAdapter", (), {"generate": staticmethod(fake_generate)})())
    request = GenerateRequest(task="cover_letter", prompt=_cover_letter_prompt())

    response = asyncio.run(execute_ai_task(request, db=db, user_id=uuid4()))

    assert calls[0]["json_mode"] is True
    assert response.raw_text == "A grounded letter."
    assert response.parsed_data["content"] == "A grounded letter."
    assert db.added[0].status == "SUCCEEDED"


def test_invalid_suggested_answers_output_is_not_recorded_as_success(monkeypatch):
    db = FakeSession()

    async def fake_generate(**_kwargs):
        return ProviderResult(
            raw_text='{"answers": [{"question": "Why this role?", "answer": "A", "invented": true}]}',
            parsed_data={
                "answers": [
                    {"question": "Why this role?", "answer": "A", "invented": True}
                ]
            },
            model_used="gemini-test",
            provider_used="google",
            latency_ms=12,
        )

    monkeypatch.setattr("app.application.ai.service.get_ai_provider", lambda _provider: type("FakeAdapter", (), {"generate": staticmethod(fake_generate)})())
    request = GenerateRequest(
        task="suggested_answers",
        prompt=_suggested_answers_prompt(),
        options={"jsonMode": True},
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(execute_ai_task(request, db=db, user_id=uuid4()))

    assert error.value.status_code == 502
    assert error.value.detail["code"] == "AI_INVALID_OUTPUT"
    assert db.added[0].status == "FAILED"
    assert db.added[0].error_code == "AI_INVALID_OUTPUT"


def test_invalid_evidence_matching_output_is_not_recorded_as_success(monkeypatch):
    db = FakeSession()

    async def fake_generate(**_kwargs):
        return ProviderResult(
            raw_text='{"matches": [{"status": "probably-good"}]}',
            parsed_data={"matches": [{"status": "probably-good"}]},
            model_used="gemini-test",
            provider_used="google",
            latency_ms=17,
        )

    monkeypatch.setattr("app.application.ai.service.get_ai_provider", lambda _provider: type("FakeAdapter", (), {"generate": staticmethod(fake_generate)})())
    request = GenerateRequest(task="evidence_matching", prompt=_matching_prompt())

    with pytest.raises(HTTPException) as error:
        asyncio.run(execute_ai_task(
            request, db=db, user_id=uuid4(), request_id="evidence-invalid",
        ))

    assert error.value.status_code == 502
    assert error.value.detail["code"] == "AI_INVALID_OUTPUT"
    assert db.added[0].status == "FAILED"
    assert db.added[0].error_code == "AI_INVALID_OUTPUT"
    assert db.added[0].finished_at is not None
    assert db.commit_count == 2


def test_background_requirement_extraction_uses_server_owned_output_contract(monkeypatch):
    db = FakeSession()
    calls = []

    async def fake_generate(**kwargs):
        calls.append(kwargs)
        parsed = {
            "requirements": [{
                "requirement_type": "SKILL",
                "description": "Experience with Python",
                "source_excerpt": "three years of Python experience",
                "importance": "HIGH",
                "severity": "MUST_HAVE",
                "hard_constraint": True,
                "confidence": 0.9,
            }]
        }
        import json
        return ProviderResult(
            raw_text=json.dumps(parsed),
            parsed_data=parsed,
            model_used="gemini-test",
            provider_used="google",
            latency_ms=12,
        )

    monkeypatch.setattr("app.application.ai.service.get_ai_provider", lambda _provider: type("FakeAdapter", (), {"generate": staticmethod(fake_generate)})())
    request = GenerateRequest(
        task="job_requirement_extraction",
        prompt='{"job_title":"Backend Engineer","employer":"Example Ltd","job_description":"The successful candidate will build and maintain backend services using Python. You will collaborate with product and engineering teams, troubleshoot production issues, and write automated tests. Applicants must have three years of Python experience and a bachelor degree or equivalent."}',
        options={"jsonMode": True},
    )

    response = asyncio.run(execute_ai_task(request, db=db, user_id=None))

    assert calls[0]["json_mode"] is True
    assert response.parsed_data["requirements"][0]["requirement_type"] == "SKILL"
    assert db.added[0].task == "job_requirement_extraction"
    assert db.added[0].user_id is None
    assert db.added[0].status == "SUCCEEDED"


def test_background_requirement_extraction_rejects_ungrounded_source_before_success(monkeypatch):
    db = FakeSession()

    async def fake_generate(**_kwargs):
        import json
        parsed = {
            "requirements": [{
                "requirement_type": "SKILL",
                "description": "Experience with Python",
                "source_excerpt": "Invented qualification not present in the JD",
                "importance": "HIGH",
                "hard_constraint": True,
                "confidence": 0.9,
            }]
        }
        return ProviderResult(
            raw_text=json.dumps(parsed),
            parsed_data=parsed,
            model_used="gemini-test",
            provider_used="google",
            latency_ms=12,
        )

    monkeypatch.setattr("app.application.ai.service.get_ai_provider", lambda _provider: type("FakeAdapter", (), {"generate": staticmethod(fake_generate)})())
    request = GenerateRequest(
        task="job_requirement_extraction",
        prompt='{"job_title":"Backend Engineer","employer":"Example Ltd","job_description":"The successful candidate will build and maintain backend services using Python. You will collaborate with product and engineering teams, troubleshoot production issues, and write automated tests. Applicants must have three years of Python experience and a bachelor degree or equivalent."}',
        options={"jsonMode": True},
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(execute_ai_task(request, db=db, user_id=None))

    assert error.value.status_code == 502
    assert error.value.detail["code"] == "AI_INVALID_OUTPUT"
    assert db.added[0].status == "FAILED"
    assert db.added[0].error_code == "AI_INVALID_OUTPUT"



def test_document_parsing_rejects_unanchored_source_text():
    from app.application.ai.output_contracts import InvalidTaskOutput, validate_task_output

    payload = {
        "document_type": "resume",
        "fields": [{
            "name": "employer",
            "value": "Example Corp",
            "source_text": "Worked at Fabricated Corp",
            "confidence": "high",
        }],
        "conflicts": [],
        "unresolved_items": [],
    }
    with pytest.raises(InvalidTaskOutput):
        validate_task_output("document_parsing", payload, {
            "document_text": "Worked at Example Corp as a product manager."
        })


def test_claim_validation_rejects_unsupplied_evidence_refs_and_artifact_types():
    from app.application.ai.output_contracts import InvalidTaskOutput, validate_task_output

    payload = {
        "checks": [{
            "artifact_type": "cover_letter",
            "claim_text": "I led product delivery.",
            "status": "supported",
            "evidence_refs": ["not-supplied"],
            "rationale": "The cited evidence supports this claim.",
            "confidence": "high",
        }],
        "audited_artifact_types": ["cover_letter"],
        "unassessed_claims": [],
        "coverage_complete": True,
    }
    input_data = {
        "artifacts": [{
            "artifact_type": "cover_letter",
            "content": "I led product delivery.",
            "declared_evidence_refs": [],
            "declared_unsupported_claims": [],
        }],
        "approved_candidate_evidence": [{
            "id": "evidence-1",
            "type": "experience",
            "content": {"summary": "Led product delivery."},
            "verified_at": None,
        }],
    }
    with pytest.raises(InvalidTaskOutput):
        validate_task_output("claim_evidence_validation", payload, input_data)
