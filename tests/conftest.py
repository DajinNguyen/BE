import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite://"
# 커뮤니티 사진은 임시 폴더에 저장한다
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="dajin-uploads-")
# 테스트는 실제 DART를 부르지 않는다 (.env에 키가 있어도)
os.environ["DART_API_KEY"] = ""
# 네이버 시세도 부르지 않는다 (시드 예시 값으로 동작)
os.environ["NAVER_PRICES_ENABLED"] = "false"
os.environ["NAVER_CLIENT_ID"] = ""
os.environ["NAVER_CLIENT_SECRET"] = ""
os.environ["GOOGLE_NEWS_ENABLED"] = "false"
# AI 도 부르지 않는다 (.env 에 키가 있어도). 필요한 테스트는 가짜 작성자를 넣는다.
# 뉴스 호재/악재 판단도 실제 AI를 부르지 않는다 — 항상 "보통"으로 대체된다.
os.environ["GEMINI_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app.api.deps import get_report_writer  # noqa: E402
from app.core.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.report import ReportContent  # noqa: E402


def _point(category, evidence):
    return {
        "category": category,
        "title": f"{category} 제목",
        "before": {"label": "영업이익", "value": "6.57조 원", "period": "2023"},
        "after": None,
        "reason": "이유예요.",
        "meaning": "뜻이에요.",
        "evidence": evidence,
    }


class FakeReportWriter:
    def __init__(self):
        self.calls = []

    async def write(self, context):
        self.calls.append(context)
        return ReportContent(
            headline="3년 동안 이익이 크게 늘어난 회사예요",
            # 순서가 섞이고 같은 분류가 두 번 와도 서버가 정리해야 한다
            key_points=[
                _point("strength", ["news", "vision"]),
                _point("performance", ["financials", "financials"]),
                _point("performance", ["news"]),
                _point("revenue_source", ["vision"]),
                _point("recent_change", ["financials", "news"]),
                _point("risk", ["financials"]),
            ],
            vision_summary="스마트폰부터 반도체까지 만드는 회사예요.",
            quiz=[
                {
                    "question": "2025년 매출은?",
                    "options": ["258.94조 원", "300.9조 원", "333.6조 원"],
                    "answer_index": 2,
                    "explanation": "333.6조 원이었어요.",
                },
                {
                    "question": "잘못된 정답 번호",
                    "options": ["a", "b"],
                    "answer_index": 5,
                    "explanation": "걸러져야 해요.",
                },
            ],
        )


@pytest.fixture
def fake_writer():
    return FakeReportWriter()


@pytest.fixture
def client(fake_writer):
    SQLModel.metadata.drop_all(engine)
    app.dependency_overrides[get_report_writer] = lambda: fake_writer
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def login_as(client, n: int | None) -> int | None:
    """n번 사용자로 로그인한 상태로 바꾸고 사용자 id 를 돌려준다. None 이면 로그아웃."""
    from app.core.db import new_session
    from app.services import auth_service
    from app.services.auth_service import GoogleProfile

    client.cookies.clear()
    if n is None:
        return None
    with new_session() as session:
        user = auth_service.upsert_user(
            session, GoogleProfile(sub=f"sub-{n}", email=f"u{n}@example.com", name=f"실명{n}")
        )
        user_id = user.id
    client.cookies.set(auth_service.SESSION_COOKIE, auth_service.issue_session(user))
    return user_id


@pytest.fixture
def member(client):
    """ADK가 넉넉한 로그인 사용자 (리포트 만들기처럼 로그인·ADK가 필요한 API를 쓰는 테스트용)."""
    from app.core.db import new_session
    from app.services import wallet_service

    user_id = login_as(client, 1)
    with new_session() as session:
        wallet_service.grant(session, user_id, 10_000, "signup", "테스트용", "test-grant")
    return user_id


@pytest.fixture(autouse=True)
def _no_consent_gate(monkeypatch):
    """대부분의 테스트는 약관 동의를 마친 사용자를 가정한다 (동의 테스트는 다시 켠다)."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "require_consent", False)
