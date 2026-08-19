import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tg_data.config import settings
from tg_data.db.models import Base

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://tgdata:tgdata@localhost:5433/tgdata_test"
)

TEST_ARCHIVE_SINCE = "2024-01-01"


@pytest.fixture(autouse=True)
def fixed_archive_boundary(monkeypatch):
    """Прибить границу архива к дате из ADR-0003.

    Иначе прогон зависит от ARCHIVE_SINCE в .env разработчика: сдвинутая
    вперёд граница молча отсекает все тестовые сообщения.
    """
    monkeypatch.setattr(settings, "archive_since", TEST_ARCHIVE_SINCE)


@pytest.fixture(scope="session")
def db_engine():
    engine = create_engine(TEST_DB_URL)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def session(db_engine):
    with Session(db_engine) as s:
        yield s
        s.rollback()
