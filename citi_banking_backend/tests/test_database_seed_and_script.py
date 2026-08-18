import importlib
import runpy
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database, seed
from app.database import Base
from app.models import Account, AuditLog, Customer, Policy, Rule, Transaction


def test_get_db_closes_its_session(monkeypatch):
    class FakeSession:
        closed = False

        def close(self):
            self.closed = True

    fake = FakeSession()
    monkeypatch.setattr(database, "SessionLocal", lambda: fake)
    iterator = database.get_db()
    assert next(iterator) is fake
    iterator.close()
    assert fake.closed


def test_init_db_without_seed_and_idempotent_seed(tmp_path, monkeypatch):
    # Other tests seed a different temporary database during app startup. Reload
    # this module so this test starts with clean ORM row templates of its own.
    importlib.reload(seed)
    engine = create_engine(f"sqlite:///{tmp_path / 'seed.db'}")
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(seed, "engine", engine)
    monkeypatch.setattr(seed, "SessionLocal", sessions)

    seed.init_db(seed=False)
    with sessions() as db:
        assert db.query(Customer).count() == 0

    seed.init_db(seed=True)
    seed.init_db(seed=True)
    with sessions() as db:
        assert db.query(Customer).count() == len(seed.SEED_CUSTOMERS)
        assert db.query(Account).count() == len(seed.SEED_ACCOUNTS)
        assert db.query(Policy).count() == len(seed.SEED_POLICIES)
        assert db.query(Rule).count() == len(seed.SEED_RULES)
        assert db.query(Transaction).count() == 4
        assert db.query(AuditLog).filter_by(event_type="seed").count() == 1


def test_init_db_command_invokes_seed(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(seed, "init_db", lambda seed=True: calls.append(seed))
    script = Path(__file__).resolve().parents[1] / "scripts" / "init_db.py"
    runpy.run_path(str(script), run_name="__main__")
    assert calls == [True]
    assert "Database initialized" in capsys.readouterr().out


def test_seed_module_main_entrypoint(capsys):
    runpy.run_module("app.seed", run_name="__main__")
    assert "Database initialized and seeded" in capsys.readouterr().out
