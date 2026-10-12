import os

os.environ["DATABASE_URL"] = "sqlite://"
# 테스트는 실제 DART를 부르지 않는다 (.env에 키가 있어도)
os.environ["DART_API_KEY"] = ""

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
