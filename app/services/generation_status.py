"""AI가 백그라운드에서 채우는 칸의 상태. 프론트는 "generating" 이면 몇 초마다 다시 물어본다.

- ready: 내용이 있다 (시드에 있거나 AI가 다 만들었다). 새 보고서로 다시 만드는 중이어도 예전 내용을 보여 준다
- generating: 지금 만드는 중 (보통 1~2분). 끝나면 다음 요청부터 채워진다
- failed: 만들지 못했다 (자료가 없거나 AI 오류). 한동안 다시 시도하지 않는다
- none: 만들 수 없다 (AI 키가 없거나 4분기처럼 대상이 아닌 경우 등)
"""

from typing import Literal

from sqlmodel import Session, SQLModel

GenerationStatus = Literal["ready", "generating", "failed", "none"]


def status_of(
    session: Session, model: type[SQLModel], stock_code: str, *, has_content: bool
) -> GenerationStatus:
    record = session.get(model, stock_code)
    status = getattr(record, "status", None)
    if status == "generating":
        return "generating"
    if has_content:
        return "ready"
    return "failed" if status == "failed" else "none"
