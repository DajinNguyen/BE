"""로그인 사용자별 관심 기업. 프론트가 전체 목록을 한 번에 보내고(PUT), 서버는 그대로 저장한다."""

from datetime import UTC, datetime

from sqlmodel import Session

from app.models.watchlist import WatchlistRecord
from app.schemas.watchlist import Watchlist
from app.services import data_store


def get_watchlist(session: Session, user_id: int) -> Watchlist:
    """관심 기업이 없는 사용자는 빈 목록."""
    record = session.get(WatchlistRecord, user_id)
    return Watchlist(stock_codes=record.stock_codes if record else [])


def save_watchlist(session: Session, user_id: int, watchlist: Watchlist) -> Watchlist:
    """보낸 순서 그대로 저장한다. 같은 회사는 한 번만, 없는 종목코드는 버린다."""
    codes = [c for c in dict.fromkeys(watchlist.stock_codes) if data_store.company_exists(c)]
    record = session.get(WatchlistRecord, user_id) or WatchlistRecord(
        user_id=user_id, updated_at=datetime.now(UTC)
    )
    record.stock_codes = codes
    record.updated_at = datetime.now(UTC)
    session.add(record)
    session.commit()
    return Watchlist(stock_codes=codes)
