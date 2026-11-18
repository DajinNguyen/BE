from app.core.db import new_session
from app.models.overview import CompanySegmentsRecord
from app.services.generation_status import status_of


def test_status_of(client):
    with new_session() as session:
        assert status_of(session, CompanySegmentsRecord, "000660", has_content=False) == "none"
        assert status_of(session, CompanySegmentsRecord, "000660", has_content=True) == "ready"
        for status, has_content, expected in [
            (
                "generating",
                True,
                "generating",
            ),  # 새 보고서로 다시 만드는 중이면 예전 내용 + generating
            ("generating", False, "generating"),
            ("failed", False, "failed"),
            ("failed", True, "ready"),  # 다시 만들다 실패해도 예전 내용은 보인다
            ("done", False, "none"),
        ]:
            session.merge(CompanySegmentsRecord(stock_code="000660", status=status))
            session.commit()
            assert (
                status_of(session, CompanySegmentsRecord, "000660", has_content=has_content)
                == expected
            )
