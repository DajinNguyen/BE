"""로그인 사용자별 용어 학습 기록.

프론트가 기기 기록과 합친 전체 기록을 한 번에 보내고(PUT), 서버는 그대로 저장한다.
용어 사전에 없는 용어 id 는 버린다.
"""

from datetime import UTC, datetime

from sqlmodel import Session

from app.models.learning import TermProgressRecord
from app.schemas.learning import TermProgress
from app.services import data_store


def get_progress(session: Session, user_id: int) -> TermProgress:
    """기록이 없는 사용자는 빈 기록."""
    record = session.get(TermProgressRecord, user_id)
    return TermProgress(**record.content) if record else TermProgress()


def save_progress(session: Session, user_id: int, progress: TermProgress) -> TermProgress:
    known = {t.id for t in data_store.get_terms()}
    saved = progress.model_copy(
        update={
            "terms": {k: v for k, v in progress.terms.items() if k in known},
            "study_dates": sorted(set(progress.study_dates)),
        }
    )
    record = session.get(TermProgressRecord, user_id) or TermProgressRecord(
        user_id=user_id, content={}, updated_at=datetime.now(UTC)
    )
    record.content = saved.model_dump(mode="json")
    record.updated_at = datetime.now(UTC)
    session.add(record)
    session.commit()
    return saved
