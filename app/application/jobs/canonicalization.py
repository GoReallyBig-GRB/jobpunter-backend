from uuid import UUID

from app.infrastructure.db.repositories.canonicalization import SqlAlchemyCanonicalizationRepository


class CanonicalizationService:
    """Application boundary for processing one acquired raw job record."""

    def __init__(self, repository: SqlAlchemyCanonicalizationRepository):
        self.repository = repository

    def process(self, raw_source_record_id: UUID) -> dict:
        return self.repository.process_raw_record(raw_source_record_id)
