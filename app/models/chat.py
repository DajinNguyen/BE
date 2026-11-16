from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class ChatConversation(SQLModel, table=True):
    """AI 챗봇 대화 1건 (사용자별)."""

    __tablename__ = "chat_conversations"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    title: str = Field(max_length=60)  # 첫 질문 앞부분
    created_at: datetime
    updated_at: datetime


class ChatMessage(SQLModel, table=True):
    """대화의 질문·답변 1건. 답변이 제대로 나온 질문만 저장한다."""

    __tablename__ = "chat_messages"

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="chat_conversations.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    role: str = Field(max_length=10)  # user | assistant
    content: str = Field(max_length=4000)
    # 질문할 때 보고 있던 화면 (`ChatContext`). 답변에는 None
    context: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    created_at: datetime
