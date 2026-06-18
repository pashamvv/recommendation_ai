from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from config import settings


def _build_connect_args() -> dict:
    if settings.sqlalchemy_database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


engine = create_engine(
    settings.sqlalchemy_database_url,
    pool_pre_ping=True,
    future=True,
    connect_args=_build_connect_args(),
)
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,
    future=True,
)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def initialize_database() -> None:
    from models import Role

    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        existing_roles = {role.name for role in db.query(Role).all()}
        for role_name in ("admin", "user"):
            if role_name not in existing_roles:
                db.add(Role(name=role_name))
        db.commit()
