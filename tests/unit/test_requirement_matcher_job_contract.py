import json

from app.infrastructure.ai.requirement_matcher import OpenAICompatibleRequirementMatcher


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({"assessments": []})
                }
            }]
        }


class FakeClient:
    def __init__(self):
        self.request = None

    def post(self, url, **kwargs):
        self.request = kwargs
        return FakeResponse()


def test_matcher_payload_does_not_read_nonexistent_job_description_attribute():
    client = FakeClient()
    matcher = OpenAICompatibleRequirementMatcher(
        endpoint="https://matcher.example/v1/chat/completions",
        api_key="test-key",
        model="test-model",
        client=client,
    )
    # Mirrors the actual SQLAlchemy Job contract: title and company exist, free-standing
    # description does not.
    job = type("Job", (), {
        "canonical_title": "Technical Product Manager",
        "company_name": "Example Co",
        "lifecycle_status": "ACTIVE",
    })()

    matcher([], [], [], job)

    payload = json.loads(client.request["json"]["messages"][1]["content"])
    assert payload["job"]["title"] == "Technical Product Manager"
    assert payload["job"]["company"] == "Example Co"
    assert "description" not in payload["job"]
