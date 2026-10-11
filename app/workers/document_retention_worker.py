"""Scheduled entrypoint: python -m app.workers.document_retention_worker."""
import json

from app.application.documents.retention import expire_eligible_documents
from app.infrastructure.db.session import SessionLocal


def main() -> None:
    with SessionLocal() as db:
        result = expire_eligible_documents(db)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
