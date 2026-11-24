from app.auth import login


def test_login_rejects_bad_password():
    result = login("alice", "wrong")
    assert result.ok is False
    assert result.attempts == 1
