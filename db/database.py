from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from os import getenv
from dotenv import load_dotenv
from db.models import Base


load_dotenv()
_engine = None
_session = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(getenv("URI"))
    return _engine


def get_session():
    global _session
    if _session is None:
        _session = sessionmaker(bind=get_engine())
    return _session()


def init_db():
    Base.metadata.create_all(get_engine())

