import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.db.models import Identity, User
from app.infrastructure.identity.firebase import FirebaseIdentityProvider

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


@pytest.fixture
def db():
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_firebase_identity_maps_repeat_sign_in_to_same_durable_user(db):
    firebase_uid = f"firebase-{uuid4()}"
    provider = FirebaseIdentityProvider(
        token_verifier=lambda _token: {"uid": firebase_uid, "name": "Candidate"},
    )

    first = provider.authenticate("Bearer verified-token", db)
    second = provider.authenticate("Bearer verified-token", db)

    assert first.user_id == second.user_id
    assert first.identity_id == second.identity_id
    assert first.auth_provider == "firebase"
    assert db.scalar(select(Identity).where(
        Identity.provider == "FIREBASE",
        Identity.provider_subject == firebase_uid,
    )) is not None
    assert len(db.scalars(select(User).where(User.id == first.user_id)).all()) == 1


def test_concurrent_first_firebase_sign_ins_converge_on_one_identity():
    """Concurrent first sign-ins for one verified UID must create one local account."""
    engine = create_engine(os.environ["DATABASE_URL"], pool_size=6)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    firebase_uid = f"firebase-race-{uuid4()}"
    barrier = Barrier(2)
    user_ids = []
    try:
        def sign_in():
            def verify(_token):
                barrier.wait(timeout=10)
                return {"uid": firebase_uid, "name": "Candidate"}

            provider = FirebaseIdentityProvider(token_verifier=verify)
            with SessionLocal() as session:
                principal = provider.authenticate("Bearer verified-token", session)
                return principal.user_id, principal.identity_id

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(sign_in), executor.submit(sign_in)]
            principals = [future.result(timeout=20) for future in futures]

        assert principals[0] == principals[1]
        user_ids = [principals[0][0]]
        with SessionLocal() as verify_db:
            identities = verify_db.scalars(select(Identity).where(
                Identity.provider == "FIREBASE",
                Identity.provider_subject == firebase_uid,
            )).all()
            assert len(identities) == 1
            assert identities[0].user_id == principals[0][0]
            assert len(verify_db.scalars(select(User).where(
                User.id == principals[0][0],
            )).all()) == 1
    finally:
        with SessionLocal() as cleanup:
            identity_ids = list(cleanup.scalars(select(Identity.id).where(
                Identity.provider == "FIREBASE",
                Identity.provider_subject == firebase_uid,
            )).all())
            if identity_ids:
                cleanup.execute(delete(Identity).where(Identity.id.in_(identity_ids)))
                cleanup.execute(delete(User).where(User.id.in_(user_ids)))
                cleanup.commit()
        engine.dispose()


def test_firebase_identity_refuses_disabled_local_account(db):
    firebase_uid = f"firebase-disabled-{uuid4()}"
    user = User(status="DISABLED", display_name="Disabled Candidate")
    db.add(user)
    db.flush()
    identity = Identity(
        user_id=user.id,
        provider="FIREBASE",
        provider_subject=firebase_uid,
    )
    db.add(identity)
    db.flush()

    provider = FirebaseIdentityProvider(
        token_verifier=lambda _token: {"uid": firebase_uid},
    )

    with pytest.raises(ValueError, match="not active"):
        provider.authenticate("Bearer verified-token", db)



def test_invalid_firebase_token_does_not_create_local_identity_or_user(db):
    before_users = db.scalar(select(func.count()).select_from(User))
    before_identities = db.scalar(select(func.count()).select_from(Identity))

    def reject_token(_token):
        raise ValueError("Firebase ID token is invalid, expired, or revoked")

    provider = FirebaseIdentityProvider(token_verifier=reject_token)
    with pytest.raises(ValueError, match="invalid, expired, or revoked"):
        provider.authenticate("Bearer invalid-token", db)

    assert db.scalar(select(func.count()).select_from(User)) == before_users
    assert db.scalar(select(func.count()).select_from(Identity)) == before_identities
