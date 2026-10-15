from app.application.opportunities.evaluation_service import _merge_preference_terms


def test_nested_and_flat_preferred_terminology_are_combined_without_duplicates():
    preferences = {
        "preferredTerminology": ["technical product", "roadmap"],
        "terminologyPreferences": {
            "prefer": ["roadmap", "product delivery"],
            "avoid": ["rockstar"],
        },
    }

    assert _merge_preference_terms(
        preferences,
        ("preferredTerminology", "preferred_terminology"),
        "prefer",
    ) == ["technical product", "roadmap", "product delivery"]


def test_nested_terminology_preferences_are_supported_without_flat_aliases():
    preferences = {
        "terminology_preferences": {
            "prefer": ["product strategy"],
            "avoid": ["ninja"],
        },
    }

    assert _merge_preference_terms(
        preferences,
        ("preferredTerminology", "preferred_terminology"),
        "prefer",
    ) == ["product strategy"]
    assert _merge_preference_terms(
        preferences,
        ("avoidedTerminology", "avoided_terminology"),
        "avoid",
    ) == ["ninja"]


def test_malformed_legacy_terminology_values_are_ignored_safely():
    preferences = {
        "preferredTerminology": "not-a-list",
        "terminologyPreferences": {"prefer": ["  ", 3, "clear language"]},
    }

    assert _merge_preference_terms(
        preferences,
        ("preferredTerminology", "preferred_terminology"),
        "prefer",
    ) == ["clear language"]
