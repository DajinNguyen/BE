"""재무 상세 "사업별 성과"·"왜 이렇게 됐나요?"를 만들 작업. API(백그라운드)와 일괄 생성 스크립트가 같이 쓴다."""

from collections.abc import Callable
from functools import partial

from sqlmodel import Session

from app.core.db import new_session
from app.services import performance_service, quarter_segment_service
from app.services.performance_service import ReasonWriter
from app.services.quarter_segment_service import QuarterSegmentWriter


def start_jobs(
    session: Session,
    stock_code: str,
    writer: ReasonWriter | None,
    segment_writer: QuarterSegmentWriter | None,
    rcept_no: str | None,
    *,
    can_use_ai: Callable[[], bool] = lambda: True,
    on_use_ai: Callable[[], None] = lambda: None,
) -> list[Callable[[], str]]:
    """만들어야 하는 것을 "만드는 중"으로 표시하고, 실행할 작업 목록을 돌려준다 (각 작업은 "done"|"failed")."""
    jobs: list[Callable[[], str]] = []
    name, chain = performance_service.latest_quarter(stock_code)
    latest = chain[-1]
    if (
        segment_writer is not None
        and can_use_ai()
        and quarter_segment_service.start_generation(session, stock_code, rcept_no, latest.q)
    ):
        on_use_ai()
        last_year = performance_service.same_quarter_last_year(chain)
        before = performance_service.previous_quarter(chain)
        jobs.append(
            partial(
                quarter_segment_service.run_generation,
                stock_code,
                segment_writer,
                new_session,
                company_name=name,
                rcept_no=rcept_no,
                revenue=latest.revenue,
                operating_income=latest.operating_income,
                prior_revenue=last_year.revenue if last_year else None,
                quarter=latest.q,
                previous_rcept_no=before.rcept_no if before else None,
            )
        )
    if (
        writer is not None
        and can_use_ai()
        and performance_service.start_generation(session, stock_code, rcept_no)
    ):
        on_use_ai()
        jobs.append(partial(performance_service.run_generation, stock_code, writer, new_session))
    return jobs
