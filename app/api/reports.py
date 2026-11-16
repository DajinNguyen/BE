import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlmodel import Session

from app.api.deps import current_user, get_report_writer
from app.core.db import get_session, new_session
from app.models.user import User
from app.schemas.report import CompanyReport
from app.services import report_service, wallet_service
from app.services.data_store import get_company
from app.services.report_service import ReportWriter

router = APIRouter(prefix="/api/companies", tags=["reports"])


class StartedReport(CompanyReport):
    """리포트 만들기 응답: 리포트 상태 + 7 ADK를 뺀 뒤 잔액."""

    balance: int


@router.get("/{stock_code}/report", response_model=CompanyReport)
async def get_report(stock_code: str, session: Session = Depends(get_session)):
    """이미 만들어진 리포트 보기. 로그인 없이도 볼 수 있고 ADK도 들지 않는다."""
    return await report_service.get_report(session, stock_code)


@router.post("/{stock_code}/report", response_model=StartedReport)
def create_report(
    stock_code: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
    writer: ReportWriter = Depends(get_report_writer),
):
    """생성을 시작하고 바로 `generating`을 돌려준다. 프론트는 GET으로 완료를 확인한다.

    로그인이 필요하다 (401 `LOGIN_REQUIRED`). 시작하면 7 ADK를 빼고 `balance` 에 남은 잔액을 준다.
    리포트를 만들지 못하면 서버가 되돌려 놓는다. ADK가 모자라면 시작하지 않고 400 `INSUFFICIENT_BALANCE`.
    이미 만드는 중이면 ADK를 쓰지 않는다.
    """
    name = get_company(stock_code).name  # 없는 회사면 404
    held: list[int] = []

    def reserve() -> None:
        held.append(
            wallet_service.hold(
                session,
                user.id,
                wallet_service.REPORT_PRICE,
                "report",
                f"AI 리포트 · {name}",
                f"report:{stock_code}:{uuid.uuid4().hex}",
            )
        )

    try:
        report, should_run = report_service.start_generation(session, stock_code, reserve)
    except Exception:
        for hold_id in held:  # 잡아 둔 뒤에 시작하지 못했으면 돌려놓는다
            wallet_service.release(session, hold_id)
        raise
    if should_run:
        background_tasks.add_task(
            report_service.run_generation,
            stock_code,
            writer,
            new_session,
            held[0] if held else None,
        )
    return StartedReport(**report.model_dump(), balance=wallet_service.balance(session, user.id))
