from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import current_user
from app.core.db import get_session
from app.models.user import User
from app.schemas.chat import ChatConversationDetail, ChatConversationOut, ChatReply, ChatRequest
from app.services import chat_service
from app.services.chat_service import ChatWriter

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/messages", response_model=ChatReply)
def ask(
    body: ChatRequest,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
    writer: ChatWriter | None = Depends(chat_service.get_chat_writer),
):
    """보고 있는 화면의 자료를 근거로 질문에 대답한다. 답변이 제대로 나왔을 때만 5 ADK가 빠진다.

    로그인 필요(401). ADK가 모자라면 402 `INSUFFICIENT_ADK`, 너무 연달아 물으면 429 `TOO_FREQUENT`,
    답변을 만들지 못하면 502 `CHAT_FAILED` (ADK는 빠지지 않는다).
    """
    return chat_service.ask(session, user.id, body, writer)


@router.get("/conversations", response_model=list[ChatConversationOut])
def list_conversations(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """내 대화 목록 (최근에 물은 순)."""
    return chat_service.list_conversations(session, user.id)


@router.get("/conversations/{conversation_id}", response_model=ChatConversationDetail)
def get_conversation(
    conversation_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """대화 한 건의 질문·답변 (오래된 순). 다른 사람의 대화는 404."""
    return chat_service.get_conversation(session, user.id, conversation_id)
