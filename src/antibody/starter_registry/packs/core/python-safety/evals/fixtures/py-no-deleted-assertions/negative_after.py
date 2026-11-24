from app.auth import login


def test_login_rejects_bad_password():
    outcome = login("alice", "wrong")
    assert outcome.ok is False
    assert outcome.error == "invalid credentials"
    assert outcome.attempts == 1
