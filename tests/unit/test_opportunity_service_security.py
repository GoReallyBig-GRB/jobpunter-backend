from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.errors import DomainError
from app.application.opportunities.service import OpportunityService
from app.domain.opportunities.service import qualify_punt


class NoProfileDB:
    def scalar(self, statement):
        return None


def test_candidate_without_profile_cannot_list_private_evaluations():
    with pytest.raises(DomainError) as exc:
        OpportunityService(NoProfileDB()).list_evaluations(uuid4())
    assert exc.value.code == "CANDIDATE_PROFILE_NOT_FOUND"
    assert exc.value.status_code == 404


@pytest.mark.parametrize(
    "evaluation",
    [
        SimpleNamespace(validity_status="STALE", outcome="QUALIFIED", hard_blockers=[]),
        SimpleNamespace(validity_status="CURRENT", outcome="NOT_QUALIFIED", hard_blockers=[]),
        SimpleNamespace(validity_status="CURRENT", outcome="QUALIFIED", hard_blockers=["work_authorization"]),
    ],
)
def test_unqualified_evaluations_cannot_become_punts(evaluation):
    with pytest.raises(ValueError):
        qualify_punt(evaluation)
