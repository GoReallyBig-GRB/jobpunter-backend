"""Semantic matching through the canonical AI task service."""
from __future__ import annotations
import json
from typing import Any
from uuid import UUID
from sqlalchemy.orm import Session
from app.application.ai.schemas import GenerateRequest
from app.application.ai.service import execute_ai_task

async def match_requirements_to_evidence(*, db: Session, user_id: UUID, job: dict[str, Any], description: str, approved_evidence: list[dict[str, Any]]) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]], str | None]:
    if len(description) > 70000:
        return None, [], "The job description exceeds the safe semantic-evaluation input limit."
    evidence_ids = {str(item["id"]) for item in approved_evidence if item.get("id")}
    traces: list[dict[str, Any]] = []
    try:
        extraction = await execute_ai_task(GenerateRequest(task="jd_extraction", prompt=json.dumps({"job_title": job.get("canonical_title"), "employer": job.get("company_name"), "job_description": description}, ensure_ascii=False)), db=db, user_id=user_id, commit_before_call=False)
        extracted = extraction.parsed_data or {}
        requirements_raw = extracted.get("requirements") or []
        if not extracted.get("responsibilities") or not requirements_raw:
            return None, traces, "The description did not yield both substantive responsibilities and requirements."
        requirements = [{"requirement_id": f"REQ-{i:03d}", "requirement_text": item["text"], "mandatory": bool(item.get("mandatory"))} for i, item in enumerate(requirements_raw, 1)]
        traces.append(_trace(db, "jd_extraction", extraction))
        matched = await execute_ai_task(GenerateRequest(task="evidence_matching", prompt=json.dumps({"requirements": requirements, "approved_candidate_evidence": approved_evidence, "instruction": "Assess every requirement only against supplied approved evidence. Return one match per requirement, preserve requirement_id and requirement_text, use direct, transferable, partial, missing, contradicted, or unverified. Direct/transferable requires supplied evidence IDs."}, ensure_ascii=False)), db=db, user_id=user_id, commit_before_call=False)
        traces.append(_trace(db, "evidence_matching", matched))
        output = (matched.parsed_data or {}).get("matches") or []
        by_id = {str(item.get("requirement_id")): item for item in output}
        expected = {item["requirement_id"] for item in requirements}
        valid = len(output) == len(requirements) and len(by_id) == len(output) and set(by_id) == expected
        if valid:
            for requirement in requirements:
                item = by_id[requirement["requirement_id"]]
                refs = item.get("evidence_refs", [])
                if item.get("requirement_text") != requirement["requirement_text"] or any(str(ref) not in evidence_ids for ref in refs):
                    valid = False
                    break
                if item.get("status") in {"direct", "transferable"} and not refs:
                    valid = False
                    break
        if not valid:
            return None, traces, "Semantic matching failed requirement coverage or evidence-provenance validation."
        result = []
        for requirement in requirements:
            item = dict(by_id[requirement["requirement_id"]])
            item["mandatory"] = requirement["mandatory"]
            result.append(item)
        return result, traces, None
    except Exception:
        return None, traces, "Semantic evaluation is unavailable or failed validation."

def _trace(db: Session, task: str, response: Any) -> dict[str, Any]:
    from app.infrastructure.db.models.governance import AIExecution
    execution = db.get(AIExecution, response.execution_id) if response.execution_id else None
    return {"task": task, "execution_id": str(response.execution_id) if response.execution_id else None, "provider": response.provider_used, "model": response.model_used, "output_contract_version": execution.output_contract_version if execution else None, "config_version_id": str(execution.config_version_id) if execution and execution.config_version_id else None}
