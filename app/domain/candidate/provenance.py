"""Validation helpers for candidate-fact source provenance."""


def source_excerpt_is_verifiable(document_text: str | None, source_excerpt: str | None) -> bool:
    """Return true only when a supplied source excerpt occurs in the parsed document text."""
    if not isinstance(document_text, str) or not isinstance(source_excerpt, str):
        return False
    normalized_document = " ".join(document_text.casefold().split())
    normalized_excerpt = " ".join(source_excerpt.casefold().split())
    return bool(normalized_excerpt) and normalized_excerpt in normalized_document
