from fastapi import APIRouter, BackgroundTasks, Depends
from sqlmodel import Session

from app.api.deps import get_report_writer
from app.core.db import get_session, new_session
from app.schemas.report import CompanyReport
from app.services import report_service
from app.services.report_service import ReportWriter

router = APIRouter(prefix="/api/companies", tags=["reports"])


@router.get("/{stock_code}/report", response_model=CompanyReport)
def get_report(stock_code: str, session: Session = Depends(get_session)):
    return report_service.get_report(session, stock_code)


@router.post("/{stock_code}/report", response_model=CompanyReport)
def create_report(
    stock_code: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    writer: ReportWriter = Depends(get_report_writer),
):
    """생성을 시작하고 바로 `generating`을 돌려준다. 프론트는 GET으로 완료를 확인한다."""
    report, should_run = report_service.start_generation(session, stock_code)
    if should_run:
        background_tasks.add_task(report_service.run_generation, stock_code, writer, new_session)
    return report
