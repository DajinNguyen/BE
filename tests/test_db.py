from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

from app.core import db


def test_add_missing_columns_to_old_tables(monkeypatch, tmp_path):
    """예전 DB의 community_comments 에 새로 생긴 칸(답글·사진·삭제 자리)을 붙인다."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:  # 예전 모양으로 되돌린다
        conn.execute(text("DROP TABLE community_comments"))
        conn.execute(
            text(
                "CREATE TABLE community_comments (id INTEGER PRIMARY KEY, post_id INTEGER,"
                " user_id INTEGER, body VARCHAR(500), created_at DATETIME, is_hidden BOOLEAN)"
            )
        )
        conn.execute(
            text("INSERT INTO community_comments VALUES (1, 1, 1, '예전 댓글', '2026-01-01', 0)")
        )
    monkeypatch.setattr(db, "engine", engine)

    db._add_missing_columns()
    db._add_missing_columns()  # 두 번 불러도 괜찮다

    columns = {c["name"] for c in inspect(engine).get_columns("community_comments")}
    assert {"parent_id", "reply_to_nickname", "image_url", "is_deleted"} <= columns
    with engine.connect() as conn:
        row = conn.execute(text("SELECT parent_id, is_deleted FROM community_comments")).one()
    assert tuple(row) == (None, 0)
