from collections.abc import Iterator

from sqlalchemy import inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import get_settings


def _make_engine(url: str):
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        return create_engine(url, **kwargs)
    return create_engine(url, pool_pre_ping=True)


engine = _make_engine(get_settings().database_url)


def init_db() -> None:
    from app.models import community, report, user  # noqa: F401  테이블 등록용

    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """create_all 은 이미 있는 테이블에 새 칸을 넣지 않는다. 마이그레이션 도구 대신 새 칸만 붙인다.

    새 칸은 비워 둘 수 있거나(None) server_default 가 있어야 한다.
    """
    inspector = inspect(engine)
    dialect = engine.dialect
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} "
                ddl += column.type.compile(dialect)
                if column.server_default is not None:
                    default = column.server_default.arg.compile(dialect=dialect)
                    ddl += f" DEFAULT {default}"
                conn.execute(text(ddl))


def new_session() -> Session:
    """요청 밖(백그라운드 작업)에서 쓰는 세션."""
    return Session(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
