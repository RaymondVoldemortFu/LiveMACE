from eval._security import redact_database_url


def test_redact_database_url_hides_password_and_keeps_location():
    database_url = "mysql+pymysql://audit_user:p%40ssword@db.internal:3306/arena"

    redacted = redact_database_url(database_url)

    assert redacted == "mysql+pymysql://audit_user:***@db.internal:3306/arena"
    assert "p%40ssword" not in redacted


def test_redact_database_url_preserves_passwordless_sqlite_url():
    assert redact_database_url("sqlite:///alpha_arena.sqlite") == (
        "sqlite:///alpha_arena.sqlite"
    )
