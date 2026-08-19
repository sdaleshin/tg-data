import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tg_data.db.models import Base

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://tgdata:tgdata@localhost:5433/tgdata"
)


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
