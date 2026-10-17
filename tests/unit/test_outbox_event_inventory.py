"""Regression inventory for durable outbox event producers and dispositions."""

import ast
from pathlib import Path

from app.workers.outbox import build_dispatcher


BUILT_IN_EVENT_TYPES = frozenset({"WORK_REQUESTED"})
APP_ROOT = Path(__file__).resolve().parents[2] / "app"


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def test_every_literal_outbox_emission_has_an_explicit_dispatch_disposition():
    emitted: dict[str, list[str]] = {}
    dynamic_emissions: list[str] = []

    for source_path in sorted(APP_ROOT.rglob("*.py")):
        relative_path = source_path.relative_to(APP_ROOT.parent)
        module = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(module):
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node)
            event_keyword = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "event_type"),
                None,
            )
            if event_keyword is None:
                continue

            # OutboxService.append is the one intentional dynamic constructor; its
            # callers are inventoried below. Do not mistake the constructor itself
            # for an event producer.
            if call_name == "OutboxEvent" and relative_path.as_posix() == "app/application/common/outbox.py":
                continue

            if call_name not in {"OutboxEvent", "append"}:
                continue

            location = f"{relative_path}:{node.lineno}"
            if isinstance(event_keyword, ast.Constant) and isinstance(event_keyword.value, str):
                emitted.setdefault(event_keyword.value, []).append(location)
            else:
                dynamic_emissions.append(location)

    dispatcher = build_dispatcher()
    dispositions = set(dispatcher.handlers) | BUILT_IN_EVENT_TYPES
    missing = {
        event_type: locations
        for event_type, locations in sorted(emitted.items())
        if event_type not in dispositions
    }

    assert not dynamic_emissions, (
        "Outbox event producers must use a literal event_type so the handler inventory "
        f"can be enforced. Dynamic call sites: {sorted(dynamic_emissions)}"
    )
    assert not missing, (
        "Outbox events lack a registered handler or explicit built-in disposition: "
        f"{missing}"
    )
    assert BUILT_IN_EVENT_TYPES == frozenset({"WORK_REQUESTED"}), (
        "Update the built-in disposition set only alongside the dispatcher's explicit "
        "event handling and regression coverage."
    )
