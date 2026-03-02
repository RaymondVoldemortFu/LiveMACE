import json

from database.connection import SessionLocal, Base
from database.models import User, Account


def _dump_rows(session, model):
    columns = [c.name for c in model.__table__.columns]
    rows = session.query(model).all()
    return [{col: getattr(row, col) for col in columns} for row in rows]


def _delete_all(session):
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())


def _restore_rows(session, model, rows):
    if not rows:
        return
    if model is Account:
        for row in rows:
            initial = row.get("initial_capital")
            if initial is not None:
                row["current_cash"] = initial
                row["frozen_cash"] = 0
                row["margin_used"] = 0
    session.execute(model.__table__.insert(), rows)


def main():
    with SessionLocal.begin() as session:
        users = _dump_rows(session, User)
        accounts = _dump_rows(session, Account)
        print(
            json.dumps(
                {
                    "users": len(users),
                    "accounts": len(accounts),
                    "action": "backup_before_cleanup",
                },
                ensure_ascii=False,
            )
        )

        _delete_all(session)
        _restore_rows(session, User, users)
        _restore_rows(session, Account, accounts)

        print(
            json.dumps(
                {
                    "status": "ok",
                    "restored_users": len(users),
                    "restored_accounts": len(accounts),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
