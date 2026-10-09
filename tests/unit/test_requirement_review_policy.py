import uuid

import pytest

from app.api.errors import DomainError
from app.application.jobs.requirement_review import review_requirement


class FakeRequirement:
    def __init__(self, status="NEEDS_REVIEW"):
        self.id = uuid.uuid4()
        self.extraction_status = status
        self.requirement_type = "SKILL"
        self.description = "Experience with Python"
        self.importance = "HIGH"
        self.hard_constraint = False
        self.source_excerpt = "Experience with Python"
        self.extraction_confidence = 0.91
        self.extraction_version = "test-v1"
        self.created_at = None


class FakeResult:
    def __init__(self, value):
        self.value = value
    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, requirement):
        self.requirement = requirement
        self.added = []
        self.committed = False
    def scalar(self, statement):
        return FakeResult(self.requirement).scalar_one_or_none()
    def add(self, value):
        self.added.append(value)
    def commit(self):
        self.committed = True
    def refresh(self, value):
        pass


def test_review_confirmation_is_explicit_and_audited():
    requirement = FakeRequirement()
    db = FakeSession(requirement)
    reviewer = uuid.uuid4()

    result = review_requirement(
        db, requirement_id=requirement.id, reviewer_user_id=reviewer,
        decision="confirm", note="Excerpt verified against source text",
    )

    assert requirement.extraction_status == "CONFIRMED"
    assert db.committed
    assert len(db.added) == 1
    assert db.added[0].action == "CONFIRM"
    assert db.added[0].reviewer_user_id == reviewer
    assert result["extraction_status"] == "CONFIRMED"


def test_review_rejection_is_audited():
    requirement = FakeRequirement()
    db = FakeSession(requirement)

    result = review_requirement(
        db, requirement_id=requirement.id, reviewer_user_id=uuid.uuid4(), decision="REJECT",
    )

    assert requirement.extraction_status == "REJECTED"
    assert result["extraction_status"] == "REJECTED"


def test_already_reviewed_requirement_cannot_be_decided_again():
    requirement = FakeRequirement(status="CONFIRMED")
    db = FakeSession(requirement)

    with pytest.raises(DomainError) as error:
        review_requirement(
            db, requirement_id=requirement.id, reviewer_user_id=uuid.uuid4(), decision="REJECT",
        )

    assert error.value.code == "REQUIREMENT_ALREADY_REVIEWED"
    assert not db.committed
