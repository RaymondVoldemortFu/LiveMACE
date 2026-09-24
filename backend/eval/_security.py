from sqlalchemy.engine import make_url


def redact_database_url(database_url: str) -> str:
    return make_url(database_url).render_as_string(hide_password=True)
