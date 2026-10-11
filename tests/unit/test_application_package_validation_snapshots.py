from types import SimpleNamespace
from uuid import uuid4

from app.application.application_packages.claim_validation import (
    _artifact_validation_snapshot,
    _evidence_validation_snapshot,
)


def test_validation_provenance_is_an_immutable_pre_execution_snapshot():
    evidence_id = uuid4()
    item = SimpleNamespace(
        id=uuid4(),
        artifact_type="tailored_cv",
        content_hash="original-content-hash",
        evidence_refs=[str(evidence_id)],
        confidence="high",
        unsupported_claims=[],
        ai_execution_id=None,
    )
    evidence = SimpleNamespace(
        id=evidence_id,
        revision=3,
        content={"role": "Product Manager"},
    )

    artifact_snapshot = _artifact_validation_snapshot([item])
    evidence_snapshot = _evidence_validation_snapshot([evidence])

    item.content_hash = "changed-content-hash"
    evidence.revision = 4
    evidence.content = {"role": "Different role"}

    assert artifact_snapshot[0]["content_hash"] == "original-content-hash"
    assert evidence_snapshot[0]["revision"] == 3
    assert evidence_snapshot[0]["content_hash"] != _evidence_validation_snapshot([evidence])[0]["content_hash"]
