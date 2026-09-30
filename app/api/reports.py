from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import get_report_writer
from app.core.db import get_session
from app.schemas.report import CompanyReport
from app.services import report_service
from app.services.report_service import ReportWriter

router = APIRouter(prefix="/api/companies", tags=["reports"])


@router.get("/{stock_code}/report", response_model=CompanyReport)
def get_report(stock_code: str, session: Session = Depends(get_session)):
    return report_service.get_stored_report(session, stock_code)


@router.post("/{stock_code}/report", response_model=CompanyReport)
async def create_report(
    stock_code: str,
    session: Session = Depends(get_session),
    writer: ReportWriter = Depends(get_report_writer),
):
    return await report_service.generate_report(session, stock_code, writer)
