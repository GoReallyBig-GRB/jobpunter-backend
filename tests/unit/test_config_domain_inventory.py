"""A0R-08: every governed configuration key family is inventoried and has a runtime consumer."""
import re
from pathlib import Path

from app.api.v1.admin import ConfigDraftRequest  # noqa: F401  (schema owning the key pattern)
from app.application.ai.task_registry import AI_TASK_REGISTRY

ROOT = Path(__file__).resolve().parents[2]

# family -> (key regex alternative as it appears in the schema, consumer file, consumer needle)
INVENTORY = {
    "ai.task.<id>": (r"ai\.task\.[a-z0-9_.-]{1,140}", "app/application/ai/service.py", 'f"ai.task.{task}"'),
    "decision.rules.candidate_evaluation": (
        r"decision\.rules\.candidate_evaluation",
        "app/application/opportunities/evaluation_service.py",
        '"decision.rules.candidate_evaluation"',
    ),
}


def _schema_pattern() -> str:
    metadata = ConfigDraftRequest.model_fields["config_key"].metadata
    return next(m.pattern for m in metadata if getattr(m, "pattern", None))


def test_schema_accepts_exactly_the_inventoried_key_families():
    pattern = _schema_pattern()
    alternatives = pattern.removeprefix("^(?:").removesuffix(")$").split("|")
    assert sorted(alternatives) == sorted(spec[0] for spec in INVENTORY.values()), (
        "A governed configuration family was added or removed; update INVENTORY and "
        "docs/admin/CONFIG_DOMAIN_INVENTORY.md with its consumer."
    )


def test_every_inventoried_family_has_a_runtime_consumer():
    for family, (_, consumer, needle) in INVENTORY.items():
        assert needle in (ROOT / consumer).read_text(), f"{family} has no consumer in {consumer}"


def test_every_registered_ai_task_is_a_valid_governed_key():
    regex = re.compile(_schema_pattern())
    for task in AI_TASK_REGISTRY:
        assert regex.fullmatch(f"ai.task.{task}"), task
