from uuid import uuid4
from app.security.authorization import AuthorizationContext, can_access_user_resource


def test_user_can_access_own_resource():
    user_id = uuid4()
    assert can_access_user_resource(AuthorizationContext(user_id), user_id)


def test_user_cannot_access_other_users_resource():
    assert not can_access_user_resource(AuthorizationContext(uuid4()), uuid4())


def test_admin_can_access_other_users_resource():
    assert can_access_user_resource(AuthorizationContext(uuid4(), is_admin=True), uuid4())
