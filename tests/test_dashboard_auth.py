from app.dashboard.auth import is_admin, parse_admin_ids

def test_parse_admin_ids():
    assert parse_admin_ids("1, 2, bad,3") == frozenset({1,2,3})

def test_is_admin():
    admins = frozenset({42})
    assert is_admin(42, admins)
    assert not is_admin(7, admins)
